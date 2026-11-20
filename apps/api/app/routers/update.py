"""软件更新：查 GitHub 最新版 → 选源下载 → 校验 → 解包 → 重启应用。

改动（0.5.3）：下载不再只认 GitHub 一个地址。
国内直连 release-assets.githubusercontent.com 下 700MB+ 安装包基本必断，
所以改成「镜像源列表 + 逐个探测 + 断点续传 + 自动切换 + 哈希校验」。

源顺序（可在设置页/接口里改，也可用环境变量 TIKU_UPDATE_MIRRORS 覆盖）：
  1. 清华 TUNA   mirrors.tuna.tsinghua.edu.cn/github-release/...
     —— 注意：TUNA 的 github-release 只收录少量项目，本仓库多半没收录，
        所以「先探测、404 就静默跳过」，不能把它当成必成功的主路径。
  2. ghproxy 类加速（把 github.com 原地址挂到加速域名后面）
  3. GitHub 直连（最稳但最慢，作为兜底）

流程：
  1. GET  /api/update/check     对比本地 VERSION 与最新 release tag
  2. POST /api/update/download  按源下载 → 校验大小/SHA256 → 解包到 data/update/staged
  3. GET  /api/update/progress  下载进度（前端轮询）
  4. POST /api/update/apply     生成 PowerShell 脚本：等软件退出 → robocopy 覆盖 → 重启
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import DATA_DIR, ROOT, VERSION

router = APIRouter(prefix="/api/update", tags=["update"])

OWNER = "spbbhyg"
REPO = "tiku-studio"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
UPDATE_DIR = DATA_DIR / "update"
PENDING = UPDATE_DIR / "pending.json"
CHECK_CACHE = UPDATE_DIR / "check.json"
MIRRORS_FILE = UPDATE_DIR / "mirrors.json"
LOG_FILE = UPDATE_DIR / "update.log"
CACHE_TTL = 600          # 10 分钟内重复点「检查更新」不再打 GitHub（省掉 60 次/小时的额度）

# 轻量更新包（只含 exe + _internal 差异 + apps/web/dist）优先于全量包
LIGHT_HINT = ("update", "patch", "增量")

DEFAULT_MIRRORS = [
    {
        "id": "tuna",
        "name": "清华 TUNA",
        "url": "https://mirrors.tuna.tsinghua.edu.cn/github-release/{owner}/{repo}/{tag}/{asset}",
        "probe": True,
    },
    {
        "id": "ghproxy",
        "name": "ghproxy 加速",
        "url": "https://ghfast.top/https://github.com/{owner}/{repo}/releases/download/{tag}/{asset}",
        "probe": True,
    },
    {
        "id": "github",
        "name": "GitHub 直连",
        "url": "https://github.com/{owner}/{repo}/releases/download/{tag}/{asset}",
        "probe": False,
    },
]

# 下载进度（前端轮询 /progress）
_PROG: dict = {"active": False, "done": 0, "total": 0, "percent": 0,
               "source": "", "speed": 0.0, "error": "", "switched": []}
_PROG_LOCK = threading.Lock()
_DL_LOCK = threading.Lock()


# ------------------------------------------------------------------ 小工具
def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return ROOT


def _norm(v: str) -> tuple[int, ...]:
    """把 v1.2.3 / 1.2 / v1.2.3-beta 变成可比较的元组。"""
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def _local_exe() -> Path | None:
    root = _app_root()
    for name in ("神奋.exe", "tiku-studio.exe"):
        p = root / name
        if p.is_file():
            return p
    return None


def _log(msg: str) -> None:
    """更新过程留痕：出问题时用户能把 data/update/update.log 发过来。"""
    try:
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:                                   # noqa: BLE001
        pass
    print(f"[update] {msg}")


def _prog(**kw) -> None:
    with _PROG_LOCK:
        _PROG.update(kw)


# ------------------------------------------------------------------ 镜像配置
def _load_mirrors() -> list[dict]:
    """环境变量 > data/update/mirrors.json > 内置默认。"""
    raw = os.environ.get("TIKU_UPDATE_MIRRORS", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list) and data:
                return [m for m in data if m.get("url")]
        except Exception:                               # noqa: BLE001
            _log(f"环境变量 TIKU_UPDATE_MIRRORS 解析失败：{raw[:80]}")
    try:
        if MIRRORS_FILE.is_file():
            data = json.loads(MIRRORS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return [m for m in data if m.get("url")]
    except Exception:                                   # noqa: BLE001
        pass
    return [dict(m) for m in DEFAULT_MIRRORS]


def _save_mirrors(items: list[dict]) -> None:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    MIRRORS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ 检查更新
def _cache_read(force: bool = False):
    if force:
        return None
    try:
        d = json.loads(CHECK_CACHE.read_text(encoding="utf-8"))
        if time.time() - float(d.get("at") or 0) < CACHE_TTL:
            return d.get("payload")
    except Exception:                                   # noqa: BLE001
        return None
    return None


def _cache_write(payload: dict) -> None:
    try:
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        CHECK_CACHE.write_text(json.dumps({"at": time.time(), "payload": payload},
                                          ensure_ascii=False), encoding="utf-8")
    except Exception:                                   # noqa: BLE001
        pass


def _limited(resp) -> dict:
    """把 403 说人话：GitHub 对未登录调用是 60 次/小时，按出口 IP 算。"""
    reset = (resp.headers or {}).get("X-RateLimit-Reset") or ""
    remain = (resp.headers or {}).get("X-RateLimit-Remaining") or ""
    if remain == "0" and reset.isdigit():
        mins = max(1, int((int(reset) - time.time()) / 60))
        return {"ok": False, "limited": True, "code": "E_RATE_LIMIT", "current": VERSION,
                "error": f"GitHub 限流：每小时只给 60 次查询，现在用完了，约 {mins} 分钟后自动恢复。"
                         f"稍后再点一次，或者换个网络（手机热点）试试。"}
    return {"ok": False, "limited": True, "code": "E_RATE_LIMIT", "current": VERSION,
            "error": "GitHub 暂时拒绝访问（403）。多半也是限流，等一会儿再试。"}


def _pick_assets(assets: list[dict]) -> tuple[dict | None, dict | None, dict | None]:
    """从 release 资产里挑出：轻量更新包 / 全量安装包 / SHA256SUMS。"""
    zips = [a for a in assets if str(a.get("name") or "").lower().endswith(".zip")]
    light = next((a for a in zips
                  if any(k in str(a.get("name") or "").lower() for k in LIGHT_HINT)), None)
    full = next((a for a in zips if a is not light), None)
    sums = next((a for a in assets
                 if "sha256" in str(a.get("name") or "").lower()), None)
    return light, full, sums


def _check_remote() -> dict:
    """实时打一次 GitHub，不做任何缓存。download() 必须用这个。"""
    try:
        r = httpx.get(f"{API}/releases/latest", timeout=12,
                      headers={"Accept": "application/vnd.github+json",
                               "User-Agent": "shenfen-updater"})
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "code": "E_NET", "current": VERSION,
                "error": f"连不上 GitHub：{exc}（检查网络/代理）"}
    if r.status_code == 404:
        return {"ok": True, "current": VERSION, "latest": VERSION, "has_update": False,
                "note": "仓库里还没有发布版本"}
    if r.status_code == 403:
        return _limited(r)
    if r.status_code != 200:
        return {"ok": False, "code": "E_HTTP", "current": VERSION,
                "error": f"GitHub 返回 {r.status_code}"}

    data = r.json()
    tag = str(data.get("tag_name") or data.get("name") or "")
    assets = [{"id": a.get("id"), "name": a.get("name"), "size": a.get("size"),
               "url": a.get("browser_download_url")}
              for a in (data.get("assets") or [])]
    light, full, sums = _pick_assets(assets)
    return {
        "ok": True,
        "current": VERSION,
        "latest": tag,
        "has_update": bool(tag) and _norm(tag) > _norm(VERSION),
        "published": data.get("published_at"),
        "notes": (data.get("body") or "")[:2000],
        "page": data.get("html_url"),
        "assets": assets,
        "light": light,
        "full": full,
        "sums": sums,
        # 兼容老前端字段
        "zip": light or full,
        "cached_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/check")
def check(force: int = 0):
    """查最新版。结果缓存 10 分钟：GitHub 未登录调用只有 60 次/小时。"""
    cached = _cache_read(force=bool(force))
    if cached:
        return cached
    out = _check_remote()
    if out.get("ok") and out.get("latest"):
        _cache_write(out)
    return out


@router.get("/mirrors")
def get_mirrors():
    return {"items": _load_mirrors(),
            "note": "顺序即优先级。tuna 只有收录了本仓库才会命中，探测失败会自动跳过。"}


class MirrorIn(BaseModel):
    items: list[dict]


@router.post("/mirrors")
def set_mirrors(body: MirrorIn):
    items = [m for m in (body.items or []) if m.get("url")]
    if not items:
        raise HTTPException(400, "至少要留一个下载源")
    _save_mirrors(items)
    return {"ok": True, "items": items}


# ------------------------------------------------------------------ 下载
def _mirror_url(mirror: dict, tag: str, asset: str) -> str:
    return str(mirror["url"]).format(owner=OWNER, repo=REPO, tag=tag, asset=asset)


def _probe(url: str, tmo: float = 6.0) -> bool:
    """探测源能不能用：带 Range 拿 1 字节，200/206 都算通。"""
    try:
        r = httpx.get(url, timeout=tmo, follow_redirects=True, headers={
            "Range": "bytes=0-0", "User-Agent": "shenfen-updater"})
        ok = r.status_code in (200, 206)
        return ok
    except Exception:                                   # noqa: BLE001
        return False


def _pull_once(url: str, target: Path, expect: int, stall: float = 30.0) -> int:
    """从 url 续传到 target，返回已写入总字节数。read 超时即视为卡死，抛异常换源。"""
    done = target.stat().st_size if target.exists() else 0
    headers = {"User-Agent": "shenfen-updater"}
    if done:
        headers["Range"] = f"bytes={done}-"
    tmo = httpx.Timeout(connect=20.0, read=stall, write=30.0, pool=30.0)
    t0, base = time.time(), done
    with httpx.stream("GET", url, timeout=tmo, follow_redirects=True, headers=headers) as r:
        if r.status_code == 416:                        # 已经下完
            return done
        if r.status_code not in (200, 206):
            raise RuntimeError(f"HTTP {r.status_code}")
        mode = "ab" if (r.status_code == 206 and done) else "wb"
        if mode == "wb":
            done = 0
        with target.open(mode) as fh:
            for chunk in r.iter_bytes(1 << 20):
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                el = max(0.001, time.time() - t0)
                _prog(done=done, total=expect,
                      percent=int(done * 100 / expect) if expect else 0,
                      speed=round((done - base) / el / 1048576, 2))
    return done


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _expected_hash(sums: dict | None, asset_name: str) -> str:
    """从 SHA256SUMS 里取本安装包的期望哈希；拿不到就返回空串（跳过校验）。"""
    if not sums:
        return ""
    try:
        r = httpx.get(str(sums.get("url")), timeout=15, follow_redirects=True,
                      headers={"User-Agent": "shenfen-updater"})
        if r.status_code != 200:
            return ""
        for line in r.text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            digest, name = parts[0], parts[-1].lstrip("*")
            if name.split("/")[-1] == asset_name:
                return digest.lower()
    except Exception as exc:                            # noqa: BLE001
        _log(f"取 SHA256SUMS 失败，跳过哈希校验：{exc}")
    return ""


def _disk_free_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(path).free / (1024 ** 3)
    except Exception:                                   # noqa: BLE001
        return 999.0


def download():
    """按镜像顺序下载安装包，任何一个源成功即可。"""
    if not _DL_LOCK.acquire(blocking=False):
        raise HTTPException(409, "已经有一个下载任务在跑了，等它结束再来")
    _prog(active=True, done=0, total=0, percent=0, source="", speed=0.0, error="", switched=[])
    try:
        info = _check_remote()                          # 关键：不用缓存，避免下到旧版本
        if not info.get("ok", True):
            _prog(active=False, error=info.get("error") or "查不到最新版本")
            raise HTTPException(503, info.get("error") or "查不到最新版本")
        if not info.get("has_update"):
            _prog(active=False, error="已是最新版")
            raise HTTPException(400, "已经是最新版了")

        asset = info.get("light") or info.get("full")
        if not asset:
            _prog(active=False, error="没有可下载的安装包")
            raise HTTPException(400, "这个版本没带 zip 安装包，点「打开 Release 页面」手动下载")
        name = str(asset.get("name") or "")
        expect = int(asset.get("size") or 0)
        tag = str(info.get("latest") or "")

        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        target = UPDATE_DIR / name
        # 磁盘：解包后大概是压缩包的 1.5 倍，留够空间再说
        need = (expect / (1024 ** 3)) * 1.6 + 0.3 if expect else 2.0
        free = _disk_free_gb(UPDATE_DIR)
        if free < need:
            _prog(active=False, error="磁盘空间不足")
            raise HTTPException(507, f"磁盘空间不够：需要约 {need:.1f} GB，{UPDATE_DIR.drive} 盘只剩 {free:.1f} GB")

        want_hash = _expected_hash(info.get("sums"), name)
        mirrors = _load_mirrors()
        switched: list[str] = []
        last_err = ""
        got_source = ""

        for mirror in mirrors:
            mid = str(mirror.get("id") or mirror.get("name") or "?")
            url = _mirror_url(mirror, tag, name)
            if mirror.get("probe", True) and not _probe(url):
                switched.append(f"{mid}: 探测不通，跳过")
                _log(f"源 {mid} 探测失败：{url}")
                continue
            _prog(source=str(mirror.get("name") or mid))
            _log(f"开始从 {mid} 下载 {name}（{expect/1048576:.0f} MB）")
            for attempt in range(1, 4):                 # 单个源最多 3 次续传
                try:
                    done = _pull_once(url, target, expect)
                except Exception as exc:                # noqa: BLE001
                    last_err = f"{mid}: {exc}"
                    _log(f"{mid} 第 {attempt} 次中断：{exc}")
                    time.sleep(1.5)
                    continue
                if expect and done < expect:
                    last_err = f"{mid}: 只下到 {done/1048576:.0f}/{expect/1048576:.0f} MB"
                    _log(f"{mid} 不完整：{last_err}")
                    continue
                got_source = mid
                break
            if got_source:
                break
            switched.append(f"{mid}: 失败（{last_err}）")

        if not got_source:
            # 所有源都不行：给一个明确的兜底提示，别只报「各种原因」
            page = info.get("page") or f"https://github.com/{OWNER}/{REPO}/releases/latest"
            _prog(active=False, error="所有下载源都失败了", switched=switched)
            raise HTTPException(502,
                                f"所有下载源都失败了：{'；'.join(switched) or '无可用源'}。"
                                f"可以点「打开 Release 页面」手动下载：{page}")

        size = target.stat().st_size
        if size < 1000:
            _prog(active=False, error="下载到的文件是空的")
            raise HTTPException(502, "下载到的文件是空的，可能被网络中间设备拦了，换个源再试")

        if want_hash:
            got = _sha256_of(target)
            if got != want_hash:
                target.unlink(missing_ok=True)
                _prog(active=False, error="安装包校验失败")
                _log(f"哈希不匹配：期望 {want_hash} 实际 {got}")
                raise HTTPException(502, "安装包校验失败（SHA256 不匹配），已删除重下，请再点一次下载")

        staged = UPDATE_DIR / "staged"
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
        staged.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(target) as zf:
                bad = zf.testzip()
                if bad:
                    raise HTTPException(502, f"压缩包坏了（{bad}），重新下载一次")
                zf.extractall(staged)
        except HTTPException:
            _prog(active=False, error="解包失败")
            raise
        except Exception as exc:                        # noqa: BLE001
            _prog(active=False, error="解包失败")
            raise HTTPException(502, f"解包失败：{exc}") from exc

        inner = list(staged.iterdir())
        if len(inner) == 1 and inner[0].is_dir():
            staged = inner[0]

        PENDING.write_text(json.dumps({
            "tag": tag, "dir": str(staged), "zip": str(target),
            "light": bool(asset is info.get("light") and info.get("light")),
            "source": got_source,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False), encoding="utf-8")
        _prog(active=False, percent=100, done=size, total=size)
        _log(f"下载完成：{name} 来自 {got_source}，已解包到 {staged}")
        return {"ok": True, "tag": tag, "dir": str(staged), "files": len(list(staged.rglob("*"))),
                "source": got_source, "switched": switched}
    finally:
        _DL_LOCK.release()


@router.post("/download")
def api_download():
    return download()


@router.get("/progress")
def progress():
    with _PROG_LOCK:
        return dict(_PROG)


@router.get("/log")
def tail_log(lines: int = 120):
    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:                                   # noqa: BLE001
        return {"lines": [], "hint": "还没有下载记录"}
    return {"lines": text[-max(1, min(500, lines)):]}


@router.get("/status")
def status():
    if not PENDING.is_file():
        return {"pending": None, "current": VERSION}
    try:
        data = json.loads(PENDING.read_text(encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        data = None
    return {"pending": data, "current": VERSION}


_APPLY_PS = r"""# 神奋 自动更新脚本（软件自己生成的，跑完自己删）
$ErrorActionPreference = 'Stop'
$stage = '{stage}'
$root  = '{root}'
$exe   = '{exe}'
$port  = 8100

Write-Host "等待软件退出…"
for ($i = 0; $i -lt 240; $i++) {{
  $busy = $false
  try {{
    $c = New-Object Net.Sockets.TcpClient
    $c.Connect('127.0.0.1', $port); $busy = $true; $c.Close()
  }} catch {{ $busy = $false }}
  if (-not $busy) {{ break }}
  Start-Sleep -Milliseconds 500
}}
Start-Sleep -Milliseconds 800

Write-Host "覆盖文件：$stage -> $root"
$log = Join-Path $root 'update-apply.log'
robocopy $stage $root /E /IS /IT /R:2 /W:1 /NFL /NDL /NP /LOG:$log | Out-Null
$rc = $LASTEXITCODE
if ($rc -ge 8) {{
  Write-Host "覆盖失败（robocopy $rc）：目标目录可能没有写权限，或程序还没退出" -ForegroundColor Red
  Write-Host "请把软件装到有写权限的目录（如 D:\神奋），或右键「以管理员身份运行」后再更新"
  Read-Host "按回车退出"
  exit $rc
}}
Write-Host "完成，日志在 $log"

if (Test-Path $exe) {{
  Start-Process -FilePath $exe
}} else {{
  Write-Host "没找到启动程序，请手动打开 神奋.exe" -ForegroundColor Yellow
}}
Start-Sleep -Seconds 2
Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
"""


@router.post("/apply")
def apply():
    if not PENDING.is_file():
        raise HTTPException(400, "还没有下载好的新版本，先点「下载更新」")
    data = json.loads(PENDING.read_text(encoding="utf-8"))
    tag = str(data.get("tag") or "")
    if tag and _norm(tag) <= _norm(VERSION):
        raise HTTPException(400, f"待安装的版本（{tag}）不比当前版本（{VERSION}）新，已忽略")
    stage = Path(data.get("dir") or "")
    if not stage.is_dir():
        raise HTTPException(400, "解包出来的文件不见了，重新下载一次")
    exe = _local_exe()
    frozen = getattr(sys, "frozen", False)
    if not frozen:
        return {"ok": True, "mode": "dev",
                "note": f"开发态不覆盖文件。新版本已解包到 {stage}，把它的 web/ 与 exe 手动替换即可；改完后重启 python 就是新版。"}

    root = _app_root()
    if root.drive.upper() == "C:" and "program files" in str(root).lower():
        _log(f"安装目录在 Program Files，覆盖大概率失败：{root}")
    script = UPDATE_DIR / "apply.ps1"
    script.write_text(_APPLY_PS.format(stage=str(stage), root=str(root), exe=str(exe or "")),
                      encoding="utf-8")
    CREATE_NO_WINDOW = 0x08000000
    DETACHED = 0x00000008
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        creationflags=CREATE_NO_WINDOW | DETACHED, close_fds=True,
    )
    _log(f"已生成覆盖脚本：{script}")
    return {"ok": True, "mode": "exe", "script": str(script),
            "note": "更新脚本已经在后台等着了：软件会自己关掉、覆盖文件、再打开。10 秒后回来看一眼。"}
