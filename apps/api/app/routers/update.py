"""软件更新：查 GitHub 最新版 → 下载 → 解包 → 重启应用。

仓库是公开的（spbbhyg/tiku-studio），查版本与下载安装包都不需要 token。
只有「发新版」才需要 token，那是发版的人自己在本机用，跟这里无关。

流程（都是本地动作，无遥测）：
1. `GET /api/update/check`   → 对比本地 VERSION 与 GitHub 最新 release 的 tag；
2. `POST /api/update/download` → 把 release 里的 zip 下载到 data/update/ 并解包到子目录；
3. `POST /api/update/apply`  → 生成一个 PowerShell 小脚本并后台启动：等软件自己退出、
   把解包出来的文件覆盖到安装目录、再重新启动软件。

开发态（不是 PyInstaller 打包的 exe）不做覆盖，只提示"代码已经在这里了，重启 python 就行"。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException

from ..config import DATA_DIR, ROOT, VERSION

router = APIRouter(prefix="/api/update", tags=["update"])

OWNER = "spbbhyg"
REPO = "tiku-studio"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
UPDATE_DIR = DATA_DIR / "update"
PENDING = UPDATE_DIR / "pending.json"
CHECK_CACHE = UPDATE_DIR / "check.json"
CACHE_TTL = 600          # 10 分钟内重复点「检查更新」不再打 GitHub（省掉 60 次/小时的额度）


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


def _cache_read(force: bool = False):
    if force:
        return None
    try:
        d = json.loads(CHECK_CACHE.read_text(encoding="utf-8"))
        if time.time() - float(d.get("at") or 0) < CACHE_TTL:
            return d.get("payload")
    except Exception:                                  # noqa: BLE001
        return None
    return None


def _cache_write(payload: dict) -> None:
    try:
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        CHECK_CACHE.write_text(json.dumps({"at": time.time(), "payload": payload},
                                          ensure_ascii=False), encoding="utf-8")
    except Exception:                                  # noqa: BLE001
        pass


def _limited(resp) -> dict:
    """把 403 说人话：GitHub 对未登录调用是 60 次/小时，按出口 IP 算。"""
    reset = (resp.headers or {}).get("X-RateLimit-Reset") or ""
    remain = (resp.headers or {}).get("X-RateLimit-Remaining") or ""
    if remain == "0" and reset.isdigit():
        mins = max(1, int((int(reset) - time.time()) / 60))
        return {"ok": False, "limited": True, "current": VERSION,
                "error": f"GitHub 限流：每小时只给 60 次查询，现在用完了，约 {mins} 分钟后自动恢复。"
                         f"稍后再点一次，或者换个网络（手机热点）试试。"}
    return {"ok": False, "limited": True, "current": VERSION,
            "error": "GitHub 暂时拒绝访问（403）。多半也是限流，等一会儿再试。"}


@router.get("/check")
def check(force: int = 0):
    """查最新版。结果缓存 10 分钟：GitHub 未登录调用只有 60 次/小时。"""
    cached = _cache_read(force=bool(force))
    if cached:
        return cached

    try:
        r = httpx.get(f"{API}/releases/latest", timeout=12,
                      headers={"Accept": "application/vnd.github+json",
                               "User-Agent": "shenfen-updater"})
    except Exception as exc:                           # noqa: BLE001
        return {"ok": False, "current": VERSION,
                "error": f"连不上 GitHub：{exc}（检查网络/代理）"}
    if r.status_code == 404:
        out = {"ok": True, "current": VERSION, "latest": VERSION, "has_update": False,
               "note": "仓库里还没有发布版本"}
        _cache_write(out)
        return out
    if r.status_code == 403:
        return _limited(r)
    if r.status_code != 200:
        return {"ok": False, "current": VERSION, "error": f"GitHub 返回 {r.status_code}"}

    data = r.json()
    tag = str(data.get("tag_name") or data.get("name") or "")
    assets = [{"id": a.get("id"), "name": a.get("name"), "size": a.get("size"),
               "url": a.get("browser_download_url")}
              for a in (data.get("assets") or [])]
    zips = [a for a in assets if str(a.get("name") or "").lower().endswith(".zip")]
    has = bool(tag) and _norm(tag) > _norm(VERSION)
    out = {
        "ok": True,
        "current": VERSION,
        "latest": tag,
        "has_update": has,
        "published": data.get("published_at"),
        "notes": (data.get("body") or "")[:2000],
        "page": data.get("html_url"),
        "assets": assets,
        "zip": zips[0] if zips else None,
        "cached_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _cache_write(out)
    return out


@router.post("/download")
def download():
    info = check()
    if not info.get("ok", True):
        raise HTTPException(503, info.get("error") or "查不到最新版本")
    if not info.get("has_update"):
        raise HTTPException(400, "已经是最新版了")
    asset = info.get("zip")
    if not asset:
        raise HTTPException(400, "这个版本没有带 zip 安装包，去 GitHub 页面手动下载")
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    target = UPDATE_DIR / str(asset["name"])
    # 优先走 API 资产端点：它会 302 到 release-assets.githubusercontent.com，
    # 而 github.com/…/releases/download/… 在有些网络（比如本机）根本连不通。
    if asset.get("id"):
        url = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/assets/{asset['id']}"
        headers = {"Accept": "application/octet-stream", "User-Agent": "shenfen-updater"}
    else:
        url = str(asset.get("url"))
        headers = {"User-Agent": "shenfen-updater"}
    try:
        with httpx.stream("GET", url, timeout=60, follow_redirects=True, headers=headers) as r:
            if r.status_code != 200:
                raise HTTPException(502, f"下载失败：{r.status_code}")
            with target.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 16):
                    fh.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:                      # noqa: BLE001
        raise HTTPException(502, f"下载失败：{exc}")

    staged = UPDATE_DIR / "staged"
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)
    staged.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(target) as zf:
            bad = zf.testzip()
            if bad:
                raise HTTPException(502, f"压缩包坏了：{bad}")
            zf.extractall(staged)
    except HTTPException:
        raise
    except Exception as exc:                      # noqa: BLE001
        raise HTTPException(502, f"解包失败：{exc}")

    # 有的包里还套一层目录，往下钻一层
    inner = [p for p in staged.iterdir()]
    if len(inner) == 1 and inner[0].is_dir():
        staged = inner[0]

    PENDING.write_text(json.dumps({
        "tag": info.get("latest"), "dir": str(staged), "zip": str(target),
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "tag": info.get("latest"), "dir": str(staged),
            "files": len(list(staged.rglob("*")))}


@router.get("/status")
def status():
    if not PENDING.is_file():
        return {"pending": None, "current": VERSION}
    try:
        data = json.loads(PENDING.read_text(encoding="utf-8"))
    except Exception:                             # noqa: BLE001
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
    stage = Path(data.get("dir") or "")
    if not stage.is_dir():
        raise HTTPException(400, "解包出来的文件不见了，重新下载一次")
    exe = _local_exe()
    frozen = getattr(sys, "frozen", False)
    if not frozen:
        # 开发态：文件已经在源码目录里了，别把 dist 覆盖到仓库
        return {"ok": True, "mode": "dev",
                "note": f"开发态不覆盖文件。新版本已解包到 {stage}，把它的 web/ 与 exe 手动替换即可；改完后重启 python 就是新版。"}

    root = _app_root()
    script = UPDATE_DIR / "apply.ps1"
    script.write_text(_APPLY_PS.format(stage=str(stage), root=str(root), exe=str(exe or "")),
                      encoding="utf-8")
    CREATE_NO_WINDOW = 0x08000000
    DETACHED = 0x00000008
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        creationflags=CREATE_NO_WINDOW | DETACHED, close_fds=True,
    )
    return {"ok": True, "mode": "exe", "script": str(script),
            "note": "更新脚本已经在后台等着了：软件会自己关掉、覆盖文件、再打开。10 秒后回来看一眼。"}
