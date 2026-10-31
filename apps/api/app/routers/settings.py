"""数据位置设置：查看 / 修改 / 迁移 / 重启。"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from datetime import datetime, timedelta
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config
from ..config import ROOT


router = APIRouter(prefix="/api/settings", tags=["settings"])


class StorageIn(BaseModel):
    data_dir: str
    move: bool = True          # 是否把现有数据复制到新位置


@router.get("/storage")
def get_storage():
    return config.storage_info()


@router.post("/storage")
def set_storage(body: StorageIn):
    raw = (body.data_dir or "").strip().strip('"')
    if not raw:
        raise HTTPException(400, "请填写目标目录")
    target = Path(raw).expanduser()
    if not target.is_absolute():
        raise HTTPException(400, "请填绝对路径，例如 D:\\tiku-data")
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:                      # noqa: BLE001
        raise HTTPException(400, f"这个目录不可写：{exc}")

    same = target.resolve() == config.DATA_DIR.resolve()
    moved = False
    if body.move and not same:
        try:
            src_db = config.DB_PATH
            if src_db.is_file():
                dst_db = target / src_db.name
                if not dst_db.exists():
                    shutil.copy2(src_db, dst_db)
            src_lib = config.LIBRARY_DIR
            if src_lib.is_dir():
                shutil.copytree(src_lib, target / "library", dirs_exist_ok=True)
                moved = True
        except Exception as exc:                  # noqa: BLE001
            raise HTTPException(400, f"复制旧数据失败：{exc}")

    config.save_data_dir(target)
    return {"ok": True, "data_dir": str(target), "moved": moved, "restart_required": True}


@router.post("/storage/reset")
def reset_storage():
    config.reset_data_dir()
    return {"ok": True, "data_dir": str(config.DEFAULT_DATA_DIR), "restart_required": True}


@router.post("/restart")
def restart_server():
    """建一个 1 分钟后触发的一次性计划任务来拉起服务，然后本进程退出。"""
    vbs = ROOT / "run-hidden.vbs"
    cmd = ROOT / "run-server.cmd"
    if not (vbs.is_file() or cmd.is_file()):
        raise HTTPException(400, "找不到启动脚本，请手动重启服务")

    boot = f'wscript.exe "{vbs}"' if vbs.is_file() else f'cmd /c "{cmd}"'
    when = (datetime.now() + timedelta(minutes=1)).strftime("%H:%M")
    try:
        subprocess.run(                                    # noqa: S603,S607
            ["schtasks", "/Create", "/TN", "tiku-restart", "/TR", boot,
             "/SC", "ONCE", "/ST", when, "/F"],
            cwd=str(ROOT), capture_output=True, timeout=20,
        )
    except Exception as exc:                               # noqa: BLE001
        raise HTTPException(500, f"创建重启任务失败：{exc}")

    def _bye() -> None:
        time.sleep(0.8)
        os._exit(0)

    threading.Thread(target=_bye, daemon=True).start()
    return {"ok": True, "restarting": True, "at": when, "wait_seconds": 75}
