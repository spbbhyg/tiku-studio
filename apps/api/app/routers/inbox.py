"""投递箱：把 data/inbox 里的 bundle JSON 导入题库（资料库页用）。

AI 拆解入口已移除，但「丢一个 JSON 进 inbox 再导入」这条离线通道保留。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import INBOX_DIR
from ..db import get_db
from ..services import bundle_import

router = APIRouter(prefix="/api/ingest", tags=["inbox"])


def _files() -> list[Path]:
    if not INBOX_DIR.is_dir():
        return []
    return sorted([p for p in INBOX_DIR.iterdir() if p.suffix.lower() == ".json"],
                  key=lambda p: p.stat().st_mtime, reverse=True)


@router.get("/inbox")
def list_inbox():
    out = []
    for p in _files():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            kind = bundle_import.detect_kind(data)
        except Exception:
            kind = "无法识别"
        st = p.stat()
        out.append({"name": p.name, "kind": kind, "size": st.st_size,
                    "mtime": st.st_mtime})
    return {"dir": str(INBOX_DIR), "items": out}


@router.post("/inbox/{name}/import")
def import_inbox(name: str, db: Session = Depends(get_db)):
    target = (INBOX_DIR / name).resolve()
    if not str(target).startswith(str(INBOX_DIR.resolve())) or not target.is_file():
        raise HTTPException(404, "找不到这个文件")
    try:
        return bundle_import.import_bundle_file(db, target)
    except Exception as e:  # 给前端可读的错误
        raise HTTPException(400, str(e))


@router.delete("/inbox/{name}")
def delete_inbox(name: str):
    target = (INBOX_DIR / name).resolve()
    if not str(target).startswith(str(INBOX_DIR.resolve())) or not target.is_file():
        raise HTTPException(404, "找不到这个文件")
    target.unlink()
    return {"deleted": name}
