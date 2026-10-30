"""内置数据导入适配器：目前支持 QuizForge（SQLite 题库）。

设计意图：把「另一个工具抽好的题库」一次性搬进本系统，之后统一在题库/组卷里用，
不需要用户手工导出 CSV 再整理。

- `GET  /api/import/quizforge`        默认库文件是否可用 + 里头的题库列表
- `POST /api/import/quizforge`        按库路径（或默认路径）导入，可指定 bank_ids
- `POST /api/import/quizforge/upload` 上传一个 .db 直接导入（浏览器里最顺手）
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import ROOT
from ..db import get_db
from ..models import Book, Question
from ..services.quizforge_import import QuizForgeError, import_banks, read_banks

router = APIRouter(prefix="/api/import", tags=["import"])

MAX_DB_BYTES = 200 * 1024 * 1024


def default_quizforge_db() -> Path | None:
    """按优先级找 QuizForge 的数据库文件。"""
    env = os.environ.get("QUIZFORGE_DB")
    candidates = [Path(env)] if env else []
    candidates += [
        ROOT.parent / "quizforge" / "data" / "quizforge.db",
        Path.home() / "Documents" / "quizforge" / "data" / "quizforge.db",
    ]
    for path in candidates:
        try:
            if path and path.is_file():
                return path
        except OSError:
            continue
    return None


class ImportIn(BaseModel):
    db_path: str | None = Field(default=None, description="QuizForge 的 .db 路径，留空用默认位置")
    bank_ids: list[str] = Field(default_factory=list, description="只导入这些题库，留空导入全部")


@router.get("/quizforge")
def quizforge_info(db: Session = Depends(get_db)) -> dict:
    path = default_quizforge_db()
    if path is None:
        return {"found": False, "db_path": None, "banks": [], "source": "none",
                "hint": "没找到 quizforge.db：可直接上传该文件，或设置环境变量 QUIZFORGE_DB"}
    try:
        banks = read_banks(path)
    except QuizForgeError as e:
        return {"found": False, "db_path": str(path), "banks": [], "source": "none", "hint": str(e)}

    # 标出「已导入了吗」，侧边栏和弹窗都靠这个字段显示状态
    existing = {}
    for book in db.query(Book).filter(Book.publisher == "QuizForge").all():
        existing[book.title] = book
    for b in banks:
        book = existing.get(b["name"])
        b["imported"] = book is not None
        b["book_id"] = book.id if book else None
        b["imported_count"] = (
            db.query(Question).filter(Question.book_id == book.id).count() if book else 0
        )

    return {"found": True, "db_path": str(path), "banks": banks, "source": "default",
            "hint": f"共 {len(banks)} 个题库，合计 {sum(b['question_count'] for b in banks)} 题"}


@router.post("/quizforge")
def quizforge_import(body: ImportIn, db: Session = Depends(get_db)) -> dict:
    path = Path(body.db_path) if body.db_path else default_quizforge_db()
    if path is None:
        raise HTTPException(400, "没有指定数据库，也没找到默认的 quizforge.db")
    try:
        result = import_banks(db, path, body.bank_ids or None)
    except QuizForgeError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "db_path": str(path), **result}


@router.post("/quizforge/upload")
def quizforge_upload(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    name = (file.filename or "quizforge.db").lower()
    if not name.endswith((".db", ".sqlite", ".sqlite3")):
        raise HTTPException(400, "请上传 QuizForge 的 .db 文件")
    tmp_dir = Path(tempfile.mkdtemp(prefix="qf-upload-"))
    target = tmp_dir / "upload.db"
    try:
        size = 0
        with target.open("wb") as fh:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_DB_BYTES:
                    raise HTTPException(400, "文件太大（上限 200MB）")
                fh.write(chunk)
        try:
            result = import_banks(db, target, None)
        except QuizForgeError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "db_path": file.filename, **result}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
