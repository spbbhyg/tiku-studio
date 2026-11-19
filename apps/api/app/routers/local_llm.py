"""本地大模型（千问）接入：走 Ollama。

为什么走 Ollama：这台机器装不了 llama-cpp-python（PyPI 没有 Windows 轮子，
源码编译要 VS + CMake），而 Ollama 自带 GPU/CPU 推理、OpenAI 兼容接口、
模型用 `ollama pull` 增量下载，换台有显卡的电脑也是同一份软件直接跑。

这个模块只做三件事：
1. 看本机有没有 Ollama、有没有下过模型；
2. 帮用户把三档千问（0.5B / 1.5B / 3B）拉下来，并给出进度；
3. 一键把当前 AI 模型切到本地某一档。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import ai

router = APIRouter(prefix="/api/local", tags=["local-llm"])

BASE = "http://127.0.0.1:11434"
TIERS = [
    {"tag": "qwen2.5:0.5b", "name": "千问 0.5B（轻量）", "size_mb": 400,
     "hint": "最快，7GB 内存的电脑也顺；适合改错字、凑题干"},
    {"tag": "qwen2.5:1.5b", "name": "千问 1.5B（基础）", "size_mb": 1000,
     "hint": "中文更顺，写引子、翻译够用；CPU 上约 3–5 字/秒"},
    {"tag": "qwen2.5:3b", "name": "千问 3B（好用）", "size_mb": 1900,
     "hint": "质量明显更好；有显卡的电脑很轻松，纯 CPU 会很慢"},
]

_PULLS: dict[str, dict] = {}
_LOCK = threading.Lock()


# ---------- 找 ollama ----------

def _which() -> str:
    env = os.environ.get("OLLAMA_EXE")
    if env and Path(env).exists():
        return env
    found = shutil.which("ollama") or shutil.which("ollama.exe")
    if found:
        return found
    cands = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path("C:/Program Files/Ollama/ollama.exe"),
        Path("C:/Program Files (x86)/Ollama/ollama.exe"),
        Path(os.environ.get("USERPROFILE", "")) / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe",
    ]
    for c in cands:
        try:
            if c.exists():
                return str(c)
        except OSError:
            continue
    return ""


def _api() -> tuple[bool, list[dict], str]:
    """问一下 Ollama 服务：在不在、有哪些模型。"""
    try:
        r = httpx.get(f"{BASE}/api/tags", timeout=3.0)
        if r.status_code != 200:
            return False, [], f"本地模型服务回了 {r.status_code}"
        models = []
        for m in (r.json() or {}).get("models", []):
            models.append({
                "name": m.get("name") or m.get("model") or "",
                "size_mb": round((m.get("size") or 0) / 1048576),
                "updated": (m.get("modified_at") or "")[:19].replace("T", " "),
            })
        models.sort(key=lambda x: x["name"])
        return True, models, ""
    except Exception as e:  # noqa: BLE001
        return False, [], "本地模型服务没在跑（连不上 127.0.0.1:11434）" if isinstance(e, httpx.HTTPError) else str(e)


def _start() -> tuple[bool, str]:
    exe = _which()
    if not exe:
        return False, "这台电脑还没装 Ollama 本地模型引擎"
    try:
        flags = 0
        if hasattr(subprocess, "DETACHED_PROCESS"):
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        subprocess.Popen([exe, "serve"], close_fds=True, creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        return False, f"启动本地模型服务失败：{e}"
    for _ in range(30):
        time.sleep(0.5)
        ok, _models, _err = _api()
        if ok:
            return True, "本地模型服务已启动"
    return False, "本地模型服务起来了但没响应，稍等几秒再点一次"


# ---------- 接口 ----------

@router.get("/status")
def status():
    ok, models, err = _api()
    exe = _which()
    pulled = {m["name"].split(":")[0] + ":" + m["name"].split(":")[-1] if ":" in m["name"] else m["name"]: m for m in models}
    tiers = []
    for t in TIERS:
        got = pulled.get(t["tag"]) or next((m for m in models if m["name"].startswith(t["tag"].split(":")[0]) and m["name"].endswith(t["tag"].split(":")[1])), None)
        tiers.append({**t, "pulled": bool(got), "size_mb": (got or {}).get("size_mb") or t["size_mb"]})
    return {
        "installed": bool(exe),
        "exe": exe,
        "running": ok,
        "base": BASE,
        "models": models,
        "tiers": tiers,
        "error": err,
        "download_url": "https://ollama.com/download",
        "current": ai.public_config(_db()),
    }


def _db() -> Session:
    from ..db import SessionLocal
    return SessionLocal()


class PullIn(BaseModel):
    tag: str = ""


def _run_pull(tag: str) -> None:
    exe = _which()
    if not exe:
        with _LOCK:
            _PULLS[tag] = {"tag": tag, "state": "error", "error": "这台电脑还没装 Ollama"}
        return
    with _LOCK:
        _PULLS[tag] = {"tag": tag, "state": "running", "line": "正在连接模型仓库…", "started": time.time()}
    try:
        proc = subprocess.Popen([exe, "pull", tag], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in proc.stdout or []:
            line = line.strip()
            if not line:
                continue
            with _LOCK:
                p = _PULLS.setdefault(tag, {"tag": tag})
                p["line"] = line[-160:]
                p["state"] = "running"
        code = proc.wait()
        ok, models, err = _api()
        if code == 0:
            with _LOCK:
                _PULLS[tag] = {"tag": tag, "state": "done", "line": "下载完成", "models": models}
        else:
            with _LOCK:
                _PULLS[tag] = {"tag": tag, "state": "error", "error": err or f"下载失败（退出码 {code}）"}
    except Exception as e:  # noqa: BLE001
        with _LOCK:
            _PULLS[tag] = {"tag": tag, "state": "error", "error": f"下载失败：{e}"}


@router.post("/pull")
def pull(body: PullIn):
    tag = (body.tag or "").strip()
    if not tag:
        return {"ok": False, "error": "要先选一档（0.5B / 1.5B / 3B）"}
    ok, _m, _e = _api()
    if not ok:
        started, msg = _start()
        if not started:
            return {"ok": False, "error": msg}
    with _LOCK:
        cur = _PULLS.get(tag)
    if cur and cur.get("state") == "running":
        return {"ok": True, "state": "running", "note": "这一档已经在下了"}
    threading.Thread(target=_run_pull, args=(tag,), daemon=True).start()
    time.sleep(0.6)
    with _LOCK:
        return {"ok": True, "state": _PULLS.get(tag, {}).get("state", "running")}


@router.get("/pull/{tag}")
def pull_state(tag: str):
    with _LOCK:
        return _PULLS.get(tag) or {"tag": tag, "state": "idle"}


@router.post("/launch")
def launch():
    ok, _m, _e = _api()
    if ok:
        return {"ok": True, "note": "本地模型服务本来就在跑"}
    ok2, msg = _start()
    return {"ok": ok2, "note": msg}


class UseIn(BaseModel):
    tag: str = "qwen2.5:0.5b"


@router.post("/use")
def use(body: UseIn, db: Session = Depends(get_db)):
    tag = (body.tag or "").strip() or "qwen2.5:0.5b"
    ok, models, err = _api()
    if not ok:
        return {"ok": False, "error": err or "本地模型服务没在跑"}
    if not any(m["name"] == tag for m in models):
        return {"ok": False, "error": f"还没下载「{tag}」：先点这一档的「下载」"}
    cfg = ai.save_config(db, api_key="ollama", base_url=f"{BASE}/v1", model=tag)
    return {"ok": True, "note": f"已把 AI 模型切到本地的 {tag}（不联网也能用）", "config": cfg}


@router.post("/off")
def use_off(db: Session = Depends(get_db)):
    """从本地模型切回云端（清掉本地通道）。"""
    cfg = ai.save_config(db, clear_key=True)
    return {"ok": True, "note": "已切回云端模型（去 AI 模型页填 Key）", "config": cfg}
