"""内置免费模型服务（Our Free Model standalone）的进程管理。

不依赖 DeepSeek Harness：直接把 `services/ofm` 里的独立服务（Node 脚本）拉起来，
它在本机开一个 OpenAI 兼容端口（默认 18903），并自带 API Key。
后端拿到 base_url + key 后，AI 功能就与 DSH 网关无关了。
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

from ..config import DATA_DIR, ROOT

OFM_DIR = ROOT / "services" / "ofm"
OFM_ENTRY = OFM_DIR / "packages" / "standalone" / "cli.mjs"
OFM_DATA = DATA_DIR / "ofm"
DEFAULT_PORT = 18903
DEFAULT_MODEL = "deepseek-v4.1-flash-free"

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_state: dict = {"running": False, "port": 0, "base_url": "", "key": "", "models": [], "error": "", "started_at": 0}


def _node_exe() -> str:
    """优先用随包携带的 node，其次用系统 node。"""
    for cand in (ROOT / "runtime" / "node.exe", ROOT / "runtime" / "node" / "node.exe"):
        if cand.exists():
            return str(cand)
    return shutil.which("node") or shutil.which("node.exe") or "node"


def _clear_stale_lock() -> None:
    """独立服务异常退出会留下 service.lock；PID 已死就删掉，否则它拒绝启动。"""
    lock = OFM_DATA / "service.lock"
    if not lock.exists():
        return
    try:
        info = json.loads(lock.read_text(encoding="utf-8") or "{}")
        pid = int(info.get("pid") or 0)
    except Exception:
        pid = 0
    alive = False
    if pid:
        try:
            if sys.platform == "win32":
                out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                     capture_output=True, text=True, timeout=5).stdout
                alive = str(pid) in out
            else:
                os.kill(pid, 0)
                alive = True
        except Exception:
            alive = False
    if not alive:
        lock.unlink(missing_ok=True)


def _read_key() -> str:
    f = OFM_DATA / "settings.json"
    if not f.exists():
        return ""
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return str(d.get("forwardKey") or "")
    except Exception:
        return ""


def _health(port: int) -> bool:
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2)
        return r.status_code == 200 and (r.json() or {}).get("ok") is True
    except Exception:
        return False


def _list_models(base_url: str, key: str) -> list[str]:
    try:
        r = httpx.get(f"{base_url}/models", headers={"Authorization": f"Bearer {key}"}, timeout=8)
        return [m.get("id", "") for m in (r.json() or {}).get("data", []) if m.get("id")]
    except Exception:
        return []


def status() -> dict:
    return dict(_state)


def ensure_running(port: int = DEFAULT_PORT, wait: float = 25.0) -> dict:
    """确保内置服务在跑；返回 {ok, base_url, key, models, error}。"""
    global _proc
    with _lock:
        if _health(port):
            key = _read_key()
            _state.update(running=True, port=port, base_url=f"http://127.0.0.1:{port}/v1", key=key,
                          models=_list_models(f"http://127.0.0.1:{port}/v1", key), error="")
            return {"ok": True, **_state}

        _clear_stale_lock()
        if not OFM_ENTRY.exists():
            _state.update(running=False, error=f"缺少内置服务文件：{OFM_ENTRY}")
            return {"ok": False, **_state}

        OFM_DATA.mkdir(parents=True, exist_ok=True)
        node = _node_exe()
        log = open(DATA_DIR / "ofm.log", "ab", buffering=0)
        try:
            _proc = subprocess.Popen(
                [node, str(OFM_ENTRY), "--port", str(port), "--data-dir", str(OFM_DATA)],
                cwd=str(OFM_DIR), stdout=log, stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
            )
        except FileNotFoundError:
            _state.update(running=False, error="没有找到 Node.js（需要 22.19+ 或 24+），可把 node.exe 放到 runtime/ 下")
            return {"ok": False, **_state}

        deadline = time.time() + wait
        while time.time() < deadline:
            if _health(port):
                key = _read_key()
                _state.update(running=True, port=port, base_url=f"http://127.0.0.1:{port}/v1", key=key,
                              models=_list_models(f"http://127.0.0.1:{port}/v1", key),
                              error="", started_at=int(time.time()))
                return {"ok": True, **_state}
            if _proc.poll() is not None:
                break
            time.sleep(0.8)

        _state.update(running=False, error="内置服务启动超时，详见 data/ofm.log")
        return {"ok": False, **_state}


def stop() -> None:
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            try:
                _proc.terminate()
            except Exception:
                pass
        _proc = None
        _state.update(running=False)


def pick_model(models: list[str], prefer: str = DEFAULT_MODEL) -> str:
    if prefer in models:
        return prefer
    for m in models:
        if "deepseek" in m and "free" in m:
            return m
    return models[0] if models else prefer
