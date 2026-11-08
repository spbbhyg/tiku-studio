"""公共题库的 GitHub 同步。

设计要点（与《GitHub题库同步-提示词》一致）：

- 仓库结构：`manifest.json` + `packs/*.jsonl` + `images/<sha1>.<ext>`
- 发布：一次提交（blob → tree → commit → ref），包文件永不修改，只追加
- 拉取：按 manifest 的 seq 只拉新包，question 按 `content_hash` 幂等合并
- 令牌：本机 `gh` / `git credential` / 手填 PAT 三选一，DPAPI 加密存盘
- 只有仓库所有者能发布；服务只接受本机（127.0.0.1）发起的发布请求
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import DATA_DIR, LIBRARY_DIR
from ..models import Book, Question, Setting

API = "https://api.github.com"
SETTING_KEY = "github_sync"
SECRET_DIR = DATA_DIR / ".secrets"
SECRET_FILE = SECRET_DIR / "github.json"

DEFAULT_OWNER = "spbbhyg"
DEFAULT_REPO = "tiku-public"
DEFAULT_MIRROR = "jsdelivr"
MIRRORS = {
    "jsdelivr": "https://cdn.jsdelivr.net/gh/{owner}/{repo}@{ref}/{path}",
    "raw": "https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}",
    "ghproxy": "https://ghproxy.net/https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}",
}
REMOTE_BOOK_TITLE = "公共题库（GitHub）"


class GHError(Exception):
    """带错误码的同步异常，前端直接显示 message。"""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ---------------------------------------------------------------- 设置存取

def _state(db: Session) -> dict:
    row = db.get(Setting, SETTING_KEY)
    return dict(row.value or {}) if row else {}


def _save_state(db: Session, patch: dict) -> dict:
    state = _state(db)
    state.update(patch)
    row = db.get(Setting, SETTING_KEY)
    if row:
        row.value = state
    else:
        db.add(Setting(key=SETTING_KEY, value=state))
    db.commit()
    return state


def config_of(db: Session) -> dict:
    st = _state(db)
    return {
        "owner": (st.get("owner") or DEFAULT_OWNER).strip(),
        "repo": (st.get("repo") or DEFAULT_REPO).strip(),
        "mirror": (st.get("mirror") or DEFAULT_MIRROR).strip(),
    }


def save_config(db: Session, owner: str | None, repo: str | None, mirror: str | None) -> dict:
    patch: dict = {}
    if owner is not None:
        owner = owner.strip()
        if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", owner or ""):
            raise GHError("ERR_BAD_OWNER", "GitHub 用户名不合法")
        patch["owner"] = owner
    if repo is not None:
        repo = repo.strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", repo or ""):
            raise GHError("ERR_BAD_REPO", "仓库名不合法")
        patch["repo"] = repo
    if mirror is not None:
        if mirror not in MIRRORS:
            raise GHError("ERR_BAD_MIRROR", "未知的下载源")
        patch["mirror"] = mirror
    _save_state(db, patch)
    return config_of(db)


# ---------------------------------------------------------------- 令牌

class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    buf = ctypes.create_string_buffer(data, len(data))
    src = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    dst = _BLOB()
    flags = 0 if protect else 1  # CRYPTPROTECT_UI_FORBIDDEN
    ok = fn(ctypes.byref(src), None, None, None, None, flags, ctypes.byref(dst))
    if not ok:
        raise OSError("DPAPI 调用失败")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def _read_secret() -> dict:
    try:
        raw = json.loads(SECRET_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    token = ""
    try:
        if raw.get("protect") == "dpapi":
            token = _dpapi(base64.b64decode(raw["token"]), False).decode("utf-8")
        else:
            token = raw.get("token", "")
    except Exception:  # noqa: BLE001
        token = ""
    return {"token": token, "login": raw.get("login", ""), "source": raw.get("source", "file")}


def _write_secret(token: str, login: str, source: str = "pat") -> None:
    SECRET_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict = {"login": login, "source": source, "saved_at": _now()}
    try:
        payload.update({"protect": "dpapi", "token": base64.b64encode(_dpapi(token.encode(), True)).decode()})
    except Exception:  # noqa: BLE001
        payload.update({"protect": "plain", "token": token})
    SECRET_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(SECRET_FILE, 0o600)
    except Exception:  # noqa: BLE001
        pass


def _run(cmd: list[str], stdin: str | None = None, timeout: int = 6) -> str:
    """跑一个外部命令取输出；超时就连整棵进程树一起杀。

    Windows 上凭据助手可能拉起隐藏的 GUI 进程，只杀直接子进程时
    communicate() 会卡在管道上，所以这里必须 taskkill /T。
    """
    e = dict(os.environ)
    e["GIT_TERMINAL_PROMPT"] = "0"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(  # noqa: S603
        cmd, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", env=e, creationflags=flags,
    )
    try:
        out, _ = p.communicate(stdin, timeout=timeout)
        return out or ""
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],  # noqa: S603,S607
                       capture_output=True, timeout=10)
        try:
            p.communicate(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        return ""


def detect_token() -> dict:
    """按优先级找一个可用令牌，返回 {token, source}。"""
    for key in ("TIKU_GITHUB_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(key):
            return {"token": os.environ[key].strip(), "source": "env"}

    saved = _read_secret()
    if saved.get("token"):
        return {"token": saved["token"], "source": saved.get("source") or "file"}

    gh = shutil.which("gh")
    if gh:
        try:
            out = _run([gh, "auth", "token"], timeout=5).strip()
            if out.startswith(("ghp_", "github_pat_")):
                return {"token": out.splitlines()[0].strip(), "source": "gh"}
        except Exception:  # noqa: BLE001
            pass

    # 不再尝试 `git credential fill`：这台机器的 credential.helper=helper-selector
    # 会拉起看不见的 GCM 窗口，子进程管道卡死（必须 taskkill /T 才脱身）。
    return {"token": "", "source": ""}


# ---------------------------------------------------------------- HTTP

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _http(url: str, method: str = "GET", body: dict | None = None, token: str = "",
          timeout: int = 60, retries: int = 2) -> tuple[int, dict | list | None, bytes]:
    """单次请求最多重试 `retries` 次（只重试网络层错误，HTTP 4xx/5xx 不重试）。

    国内到 api.github.com 的 TLS/读取偶发超时，一次卡住就整次发布会失败，
    所以这里退避重试；上传大图的 blob 请求给 60 秒。
    """
    data = None
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "tiku-studio"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read()
                try:
                    return resp.status, json.loads(raw.decode("utf-8")), raw
                except Exception:  # noqa: BLE001
                    return resp.status, None, raw
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:  # noqa: BLE001
                payload = None
            return exc.code, payload, raw
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
    raise GHError("ERR_NETWORK", f"连不上 GitHub：{last_exc}", 502) from last_exc


def _api(path: str, method: str = "GET", body: dict | None = None, token: str = "",
         timeout: int = 60) -> tuple[int, dict | list | None]:
    status, data, _ = _http(f"{API}{path}", method, body, token, timeout)
    return status, data


def verify_token(token: str) -> dict:
    status, data = _api("/user", token=token)
    if status == 401:
        raise GHError("ERR_TOKEN_INVALID", "令牌无效或已过期（GitHub 返回 401）", 401)
    if status != 200 or not isinstance(data, dict):
        raise GHError("ERR_TOKEN_CHECK", f"校验令牌失败（HTTP {status}）", 502)
    return {"login": data.get("login", ""), "name": data.get("name") or data.get("login", "")}


# ---------------------------------------------------------------- 远端读取

def _mirror_url(cfg: dict, path: str, mirror: str | None = None) -> str:
    tpl = MIRRORS.get(mirror or cfg["mirror"]) or MIRRORS[DEFAULT_MIRROR]
    return tpl.format(owner=cfg["owner"], repo=cfg["repo"], ref="main", path=path)


_CACHE: dict[str, tuple[float, bytes]] = {}


def _fetch_once(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "tiku-studio", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def _fetch_with_deadline(url: str, timeout: int) -> bytes:
    """带硬超时的下载。

    urllib 的 timeout 管不住 TLS 握手：raw.githubusercontent.com 在这台机器上
    握手能卡 30 秒以上，所以这里放到守护线程里 join 超时，到点就走下一个源。
    """
    box: dict = {}

    def run() -> None:
        try:
            box["data"] = _fetch_once(url, timeout)
        except Exception as exc:  # noqa: BLE001
            box["err"] = exc

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise TimeoutError("下载超时")
    if "err" in box:
        raise box["err"]
    return box["data"]


def _fetch_remote(path: str, cfg: dict, timeout: int = 5, fresh: bool = False) -> bytes:
    key = f"{cfg['owner']}/{cfg['repo']}:{path}"
    hit = _CACHE.get(key)
    if hit and not fresh and time.time() - hit[0] < 60:
        return hit[1]
    order = [cfg["mirror"]] + [m for m in MIRRORS if m != cfg["mirror"]]
    errors: list[str] = []
    for mirror in order:
        url = _mirror_url(cfg, path, mirror)
        if fresh:
            # jsDelivr / raw 都会缓存 @main 分支的内容，加参数强制取新的
            url += ("&" if "?" in url else "?") + f"t={int(time.time())}"
        try:
            data = _fetch_with_deadline(url, timeout)
            _CACHE[key] = (time.time(), data)
            return data
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                errors.append(f"{mirror}: HTTP {exc.code}")
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{mirror}: {type(exc).__name__}")
            continue
    raise GHError("ERR_REMOTE", f"下载 {path} 失败（{'；'.join(errors) or '全部源都不可用'}）", 502)


def _manifest_via_api(cfg: dict) -> dict | None:
    tok = detect_token()
    if not tok.get("token"):
        return None
    status, data = _api(f"/repos/{cfg['owner']}/{cfg['repo']}/contents/manifest.json?ref=main",
                        token=tok["token"])
    if status != 200 or not isinstance(data, dict) or not data.get("content"):
        return None
    try:
        return json.loads(base64.b64decode(data["content"]).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def fetch_manifest(cfg: dict) -> dict:
    # GitHub API → raw/jsDelivr（带 cache-buster）；CDN 的 @main 缓存可能落后十几分钟，
    # 刚发布完就读会读到旧的 seq，所以本机有令牌时优先问 API。
    raw: bytes | None = None
    m_api = _manifest_via_api(cfg)
    if isinstance(m_api, dict) and "seq" in m_api:
        return m_api
    try:
        raw = _fetch_remote("manifest.json", cfg, fresh=True)
    except GHError:
        raise
    try:
        m = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise GHError("ERR_MANIFEST", "manifest.json 不是合法 JSON", 502) from exc
    if not isinstance(m, dict) or "seq" not in m:
        raise GHError("ERR_MANIFEST", "manifest.json 缺少 seq 字段", 502)
    return m


# ---------------------------------------------------------------- 本地题 → 包

def _q_hash(q: Question) -> str:
    return q.content_hash or ""


def skip_ids(db: Session) -> set[int]:
    st = _state(db)
    out = set()
    for x in (st.get("skip_ids") or []):
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            continue
    return out


def set_skip(db: Session, qid: int, skip: bool) -> dict:
    ids = skip_ids(db)
    if skip:
        ids.add(int(qid))
    else:
        ids.discard(int(qid))
    _save_state(db, {"skip_ids": sorted(ids)})
    return {"ok": True, "id": int(qid), "skip": bool(skip), "count": len(ids)}


def publishable(db: Session, include_drafts: bool, skip: set[int] | None = None) -> list[Question]:
    stmt = select(Question).order_by(Question.id)
    rows = list(db.scalars(stmt).all())
    skip = skip_ids(db) if skip is None else skip
    if include_drafts:
        rows = [q for q in rows if q.status != "rejected"]
    else:
        rows = [q for q in rows if q.status == "confirmed"]
    return [q for q in rows if q.id not in skip]


def _image_bytes(rel: str) -> tuple[bytes, str] | None:
    p = (LIBRARY_DIR / rel).resolve()
    try:
        p.relative_to(LIBRARY_DIR.resolve())
    except ValueError:
        return None
    if not p.is_file():
        return None
    return p.read_bytes(), p.suffix.lower() or ".png"


def build_pack(questions: list[Question]) -> tuple[list[dict], dict[str, bytes]]:
    """把题转成 jsonl 行 + 需要上传的图片（key=sha1，value=bytes）。"""
    lines: list[dict] = []
    images: dict[str, bytes] = {}
    for q in questions:
        imgs = []
        for item in (q.images or []):
            rel = item.get("path") if isinstance(item, dict) else str(item)
            if not rel:
                continue
            got = _image_bytes(rel)
            if not got:
                continue
            data, ext = got
            sha = hashlib.sha1(data).hexdigest()
            images[sha + ext] = data
            imgs.append({"sha1": sha, "ext": ext, "caption": (item or {}).get("caption", "") if isinstance(item, dict) else ""})
        lines.append({
            "content_hash": _q_hash(q),
            "stem": q.stem or "",
            "answer": q.answer or "",
            "analysis": q.analysis or "",
            "q_type": q.q_type or "简答",
            "difficulty": q.difficulty or 2,
            "knowledge_points": q.knowledge_points or [],
            "tags": q.tags or [],
            "source_title": q.source_title or "",
            "source_citation": q.source_citation or "",
            "options": q.options or [],
            "layout": q.layout,
            "answer_layout": q.answer_layout,
            "images": imgs,
            "created_at": (q.created_at.isoformat() if q.created_at else _now()),
        })
    return lines, images


# ---------------------------------------------------------------- 发布

def _git_data(token: str, cfg: dict, files: dict[str, bytes], message: str) -> dict:
    owner, repo = cfg["owner"], cfg["repo"]
    status, ref = _api(f"/repos/{owner}/{repo}/git/ref/heads/main", token=token)
    if status == 404:
        raise GHError("ERR_REF", "仓库还没有 main 分支（空仓库需先有一次提交）", 409)
    if status != 200 or not isinstance(ref, dict):
        raise GHError("ERR_REF", f"读取 main 失败（HTTP {status}）", 502)
    base_sha = ref["object"]["sha"]

    status, commit = _api(f"/repos/{owner}/{repo}/git/commits/{base_sha}", token=token)
    if status != 200 or not isinstance(commit, dict):
        raise GHError("ERR_COMMIT", f"读取基准提交失败（HTTP {status}）", 502)
    base_tree = commit["tree"]["sha"]

    tree_items = []
    for path, data in files.items():
        status, blob = _api(f"/repos/{owner}/{repo}/git/blobs", "POST",
                            {"content": base64.b64encode(data).decode(), "encoding": "base64"}, token,
                            timeout=120)
        if status not in (200, 201) or not isinstance(blob, dict):
            raise GHError("ERR_BLOB", f"上传 {path} 失败（HTTP {status}）", 502)
        tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})

    status, tree = _api(f"/repos/{owner}/{repo}/git/trees", "POST",
                        {"base_tree": base_tree, "tree": tree_items}, token, timeout=240)
    if status not in (200, 201) or not isinstance(tree, dict):
        raise GHError("ERR_TREE", f"创建目录树失败（HTTP {status}）", 502)

    status, new_commit = _api(f"/repos/{owner}/{repo}/git/commits", "POST",
                              {"message": message, "tree": tree["sha"], "parents": [base_sha]}, token,
                              timeout=120)
    if status not in (200, 201) or not isinstance(new_commit, dict):
        raise GHError("ERR_COMMIT", f"创建提交失败（HTTP {status}）", 502)

    status, _ = _api(f"/repos/{owner}/{repo}/git/refs/heads/main", "PATCH",
                     {"sha": new_commit["sha"], "force": False}, token, timeout=120)
    if status not in (200, 201):
        raise GHError("ERR_REF_UPDATE", f"更新 main 失败（HTTP {status}）", 502)
    return {"commit": new_commit["sha"], "files": len(files)}


def publish(db: Session, include_drafts: bool = False, message: str = "", force: bool = False) -> dict:
    cfg = config_of(db)
    tok = detect_token()
    if not tok["token"]:
        raise GHError("ERR_NO_TOKEN", "本机找不到 GitHub 令牌：在设置里粘贴一个 PAT，或先 `gh auth login`", 401)
    me = verify_token(tok["token"])
    if (me["login"] or "").lower() != cfg["owner"].lower():
        raise GHError("ERR_FORBIDDEN_OWNER",
                      f"当前令牌属于 {me['login']}，只能发布到自己的仓库（{cfg['owner']}/{cfg['repo']}）", 403)

    state = _state(db)
    questions = publishable(db, include_drafts)
    if not questions:
        raise GHError("ERR_NOTHING", "没有可发布的题：先把题目标成「已确认」，或在发布时勾选包含草稿", 400)

    done = dict(state.get("published") or {})
    sent_imgs = set(state.get("published_images") or [])
    fresh = list(questions) if force else [q for q in questions if (q.content_hash or "") not in done]
    if not fresh:
        return {"ok": True, "up_to_date": True, "seq": int(state.get("last_seq") or 0),
                "questions": 0, "images": 0, "message": "没有新题需要发布"}

    lines, images = build_pack(fresh)
    if not lines:
        raise GHError("ERR_NOTHING", "没有可发布的题", 400)
    if not force:
        # 已上传过的图不再重复传（文件名就是内容 sha1，天然去重）
        images = {k: v for k, v in images.items() if k not in sent_imgs}

    remote = fetch_manifest(cfg)
    seq = int(remote.get("seq") or 0) + 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    pack_path = f"packs/{stamp}-{seq}.jsonl"
    body = "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n"

    files: dict[str, bytes] = {pack_path: body.encode("utf-8")}
    for name, data in images.items():
        files[f"images/{name}"] = data

    old_packs = list(remote.get("packs") or [])
    old_packs.append({
        "file": pack_path, "seq": seq, "count": len(lines),
        "created_at": _now(), "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()[:16],
    })
    counts = remote.get("counts") or {}
    manifest = {
        "schema": 1,
        "owner": cfg["owner"],
        "seq": seq,
        "updated_at": _now(),
        "counts": {
            "questions": len(done) + len(lines),
            "images": len(sent_imgs) + len(images),
        },
        "packs": old_packs,
    }
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    total = sum(len(v) for v in files.values())
    if total > 45 * 1024 * 1024:
        raise GHError("ERR_TOO_BIG", f"本次要传 {total / 1048576:.1f} MB，超过单次上限 45 MB；请减小图片或分批发布", 413)

    msg = message or f"publish: {len(lines)} questions ({seq})"
    res = _git_data(tok["token"], cfg, files, msg)
    _CACHE.clear()
    for q in fresh:
        if q.content_hash:
            done[q.content_hash] = seq
    _save_state(db, {
        "last_seq": seq,
        "last_publish_at": _now(),
        "last_commit": res["commit"],
        "last_counts": {"questions": len(lines), "images": len(images), "bytes": total},
        "published": done,
        "published_images": sorted(sent_imgs | set(images)),
    })
    return {
        "ok": True, "seq": seq, "questions": len(lines), "images": len(images),
        "bytes": total, "pack": pack_path, "commit": res["commit"],
        "url": f"https://github.com/{cfg['owner']}/{cfg['repo']}/commit/{res['commit']}",
    }


# ---------------------------------------------------------------- 拉取

def _remote_book(db: Session, cfg: dict) -> Book:
    title = f"{REMOTE_BOOK_TITLE} · {cfg['owner']}/{cfg['repo']}"
    book = db.scalar(select(Book).where(Book.title == title))
    if book:
        return book
    book = Book(role="self_made", subject="化学", grade=0, semester="", publisher="GitHub",
                edition_year=datetime.now(timezone.utc).year, title=title,
                description="从 GitHub 公共题库同步下来的题（只读，请勿在此题库内编辑）")
    db.add(book)
    db.commit()
    return book


def _ensure_local_image(sha: str, ext: str, cfg: dict, pack_dir: str) -> str:
    rel = f"public/{sha}{ext}"
    dst = LIBRARY_DIR / rel
    if dst.is_file():
        return rel
    raw = _fetch_remote(f"images/{sha}{ext}", cfg)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(raw)
    return rel


def pull(db: Session) -> dict:
    cfg = config_of(db)
    remote = fetch_manifest(cfg)
    state = _state(db)
    last_seq = int(state.get("last_seq") or 0)
    remote_seq = int(remote.get("seq") or 0)
    if remote_seq <= last_seq:
        _save_state(db, {"last_pull_at": _now(), "remote_seq": remote_seq})
        return {"ok": True, "updated": 0, "skipped": 0, "seq": remote_seq, "up_to_date": True}

    book = _remote_book(db, cfg)
    added = skipped = 0
    for pack in sorted(remote.get("packs") or [], key=lambda x: int(x.get("seq") or 0)):
        if int(pack.get("seq") or 0) <= last_seq:
            continue
        raw = _fetch_remote(pack["file"], cfg)
        for line in raw.decode("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            h = (item.get("content_hash") or "").strip()
            if not h:
                h = hashlib.sha1(((item.get("stem") or "") + (item.get("answer") or "")).encode("utf-8")).hexdigest()
            exists = db.scalar(select(Question).where(Question.book_id == book.id, Question.content_hash == h))
            if exists:
                skipped += 1
                continue
            imgs = []
            for im in item.get("images") or []:
                try:
                    rel = _ensure_local_image(im["sha1"], im.get("ext") or ".png", cfg, pack["file"])
                except Exception:  # noqa: BLE001
                    continue
                imgs.append({"path": rel, "caption": im.get("caption") or ""})
            db.add(Question(
                book_id=book.id, content_hash=h,
                stem=item.get("stem") or "", answer=item.get("answer") or "",
                analysis=item.get("analysis") or "", q_type=item.get("q_type") or "简答",
                difficulty=int(item.get("difficulty") or 2),
                knowledge_points=item.get("knowledge_points") or [],
                tags=item.get("tags") or [], images=imgs,
                options=item.get("options") or [],
                source_title=item.get("source_title") or "",
                source_citation=item.get("source_citation") or "",
                layout=item.get("layout"), answer_layout=item.get("answer_layout"),
                scope_status="ok", status="confirmed",
            ))
            added += 1
            db.flush()
        _save_state(db, {"last_seq": int(pack.get("seq") or 0)})
    db.commit()
    _save_state(db, {"last_pull_at": _now(), "remote_seq": remote_seq})
    return {"ok": True, "updated": added, "skipped": skipped, "seq": remote_seq, "up_to_date": False}


# ---------------------------------------------------------------- 状态

def status(db: Session) -> dict:
    cfg = config_of(db)
    st = _state(db)
    tok = detect_token()
    info: dict = {
        "configured": bool(tok["token"]),
        "owner": cfg["owner"], "repo": cfg["repo"], "mirror": cfg["mirror"],
        "mirrors": list(MIRRORS.keys()),
        "token": {"present": bool(tok["token"]), "source": tok["source"]},
        "local": {
            "confirmed": int(db.scalar(select(func.count()).select_from(Question)
                                       .where(Question.status == "confirmed")) or 0),
            "drafts": int(db.scalar(select(func.count()).select_from(Question)
                                    .where(Question.status == "draft")) or 0),
        },
        "last": {
            "seq": int(st.get("last_seq") or 0),
            "publish_at": st.get("last_publish_at") or "",
            "pull_at": st.get("last_pull_at") or "",
            "commit": st.get("last_commit") or "",
        },
        "url": f"https://github.com/{cfg['owner']}/{cfg['repo']}",
        "skip_ids": sorted(skip_ids(db)),
        "published": len((st.get("published") or {})),
    }
    if tok["token"]:
        try:
            me = verify_token(tok["token"])
            info["token"].update({"login": me["login"], "name": me["name"], "valid": True})
            info["can_publish"] = (me["login"] or "").lower() == cfg["owner"].lower()
        except GHError as exc:
            info["token"].update({"valid": False, "error": exc.message})
            info["can_publish"] = False
    else:
        info["can_publish"] = False
    try:
        remote = fetch_manifest(cfg)
        seq = int(remote.get("seq") or 0)
        info["remote"] = {"seq": seq, "counts": remote.get("counts") or {},
                          "updated_at": remote.get("updated_at") or "", "reachable": True, "cached": False}
        _save_state(db, {"remote_seq": seq, "remote_counts": remote.get("counts") or {}, "remote_at": _now()})
    except GHError as exc:
        # 网络不稳时用上次读到的值兜底，避免设置页一直转圈
        info["remote"] = {
            "reachable": False, "error": exc.message, "cached": True,
            "seq": int(st.get("remote_seq") or 0),
            "counts": st.get("remote_counts") or {},
            "updated_at": st.get("remote_at") or "",
        }
    return info


def save_token(db: Session, token: str) -> dict:
    token = (token or "").strip()
    if not token:
        raise GHError("ERR_EMPTY", "令牌是空的")
    me = verify_token(token)
    _write_secret(token, me["login"], "pat")
    _save_state(db, {"token_login": me["login"]})
    return {"ok": True, "login": me["login"]}


def clear_token(db: Session) -> dict:
    try:
        SECRET_FILE.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}
