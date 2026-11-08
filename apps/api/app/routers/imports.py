"""整卷导入：上传 PDF，AI 按题号拆题后写进题库。

- `POST /api/import/paper`   单份 PDF
- `POST /api/import/papers`  多份（题目卷 + 答案卷）
"""
from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Book, Question
from ..services import ai
from ..services.bundle_import import _question_hash
from .authoring import _ensure_topic_tags

router = APIRouter(prefix="/api/import", tags=["import"])



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

