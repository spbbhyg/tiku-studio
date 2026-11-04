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
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ROOT
from ..db import get_db
from ..models import Book, Question
from ..services import ai
from ..services.bundle_import import _question_hash
from ..services.quizforge_import import QuizForgeError, import_banks, read_banks
from .authoring import _ensure_topic_tags

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


def _clean_upload_name(name: str) -> str:
    """浏览器上传的中文文件名常被按 latin-1 解码，这里还原成中文。"""
    raw = name or ""
    for enc in ("gbk", "utf-8"):
        try:
            fixed = raw.encode("latin-1").decode(enc)
            if fixed and fixed != raw:
                return fixed
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return raw


def _store_parsed(db: Session, parsed: dict, paper_title: str) -> dict:
    """把 AI 拆好的整卷写进题库：一本书 + 去重后的题目 + 专题标签。"""
    book = db.scalar(select(Book).where(Book.title == paper_title))
    if book is None:
        book = Book(role="paper_set", subject="化学", grade=12, semester="全",
                    publisher="试卷导入", edition_year=datetime.now(timezone.utc).year,
                    title=paper_title)
        db.add(book)
        db.flush()
    tags = sorted({kp for q in parsed["questions"] for kp in (q.get("knowledge_points") or [])})
    _ensure_topic_tags(db, tags)
    imported = 0
    skipped = 0
    for q in parsed["questions"]:
        payload = {
            "stem": q["stem"], "q_type": q["q_type"], "options": [], "answer": q["answer"],
            "analysis": q.get("analysis", ""), "knowledge_points": q.get("knowledge_points") or [],
        }
        h = _question_hash(book.id, payload)
        exists = db.scalar(select(Question).where(
            Question.book_id == book.id, Question.content_hash == h))
        if exists is not None:
            skipped += 1
            continue
        db.add(Question(
            book_id=book.id, content_hash=h, stem=payload["stem"], options=[],
            answer=payload["answer"], analysis=payload["analysis"] or None,
            q_type=q.get("q_type") or "综合", difficulty=3,
            knowledge_points=payload["knowledge_points"], images=[],
            tags=payload["knowledge_points"], source_title=paper_title,
            source_page="导入", status="confirmed", scope_status="extension",
        ))
        imported += 1
    db.commit()
    return {"ok": True, "title": paper_title, "book_id": book.id,
            "imported": imported, "skipped": skipped, "total": len(parsed["questions"]),
            "tags": tags}


@router.post("/paper")
def import_paper(file: UploadFile = File(...), title: str = "", db: Session = Depends(get_db)) -> dict:
    """导入整张试卷：PDF 文字/OCR → AI 拆题 + 答案 → 自动归类进题库。"""
    filename = _clean_upload_name(file.filename or "试卷.pdf")
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "目前支持 PDF 试卷（拍照/扫描也可，会自动 OCR）")
    tmp_dir = Path(tempfile.mkdtemp(prefix="paper-upload-"))
    target = tmp_dir / "paper.pdf"
    try:
        size = 0
        with target.open("wb") as fh:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > 80 * 1024 * 1024:
                    raise HTTPException(400, "文件太大（上限 80MB）")
                fh.write(chunk)
        text = ai.pdf_to_text(target, max_pages=30)
        if not text.strip():
            raise HTTPException(400, "这份 PDF 没有文字层，OCR 也没读到内容；请换清晰版")
        parsed = ai.parse_paper(db, text)
        paper_title = (title or parsed.get("title") or Path(filename).stem)[:120]
        return _store_parsed(db, parsed, paper_title)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/papers")
def import_papers(files: list[UploadFile] = File(...), title: str = "",
                  db: Session = Depends(get_db)) -> dict:
    """多份 PDF 一起导入（题目卷 + 答案卷）：合并文字后由 AI 按题号配对拆题。"""
    if not files:
        raise HTTPException(400, "请至少选择一份 PDF")
    if len(files) > 8:
        raise HTTPException(400, "一次最多 8 份 PDF")
    tmp_dir = Path(tempfile.mkdtemp(prefix="papers-upload-"))
    names: list[str] = []
    chunks: list[str] = []
    try:
        for idx, file in enumerate(files):
            filename = _clean_upload_name(file.filename or f"试卷{idx + 1}.pdf")
            if not filename.lower().endswith(".pdf"):
                raise HTTPException(400, f"「{filename}」不是 PDF")
            target = tmp_dir / f"paper-{idx}.pdf"
            size = 0
            with target.open("wb") as fh:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 80 * 1024 * 1024:
                        raise HTTPException(400, f"「{filename}」太大（单份上限 80MB）")
                    fh.write(chunk)
            text = ai.pdf_to_text(target, max_pages=30)
            if not text.strip():
                raise HTTPException(400, f"「{filename}」没有文字层，OCR 也没读到内容")
            names.append(filename)
            chunks.append(f"=== 文档：{filename} ===\n{text}")
        combined = "\n\n".join(chunks)
        parsed = ai.parse_paper(db, combined)
        paper_title = (title or parsed.get("title") or Path(names[0]).stem)[:120]
        result = _store_parsed(db, parsed, paper_title)
        return {**result, "files": names, "documents": len(names)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

