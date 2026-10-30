"""杂项：统计、设置、题图文件、schema 下载。"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import LIBRARY_DIR, SCHEMA_DIR
from ..db import get_db
from ..models import Book, Paper, Question
from ..schemas import SettingsIn, SettingsOut, StatsOut
from ..services import ingest_llm

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/stats", response_model=StatsOut)
def stats(db: Session = Depends(get_db)):
    def count(model, cond=None):
        stmt = select(func.count()).select_from(model)
        if cond is not None:
            stmt = stmt.where(cond)
        return db.scalar(stmt) or 0

    by_subject = dict(db.execute(
        select(Book.subject, func.count()).select_from(Question)
        .join(Book, Question.book_id == Book.id).group_by(Book.subject)).all())
    by_type = dict(db.execute(
        select(Question.q_type, func.count()).select_from(Question).group_by(Question.q_type)).all())
    return StatsOut(
        books=count(Book), textbooks=count(Book, Book.role == "textbook"),
        questions=count(Question),
        confirmed_questions=count(Question, Question.status == "confirmed"),
        draft_questions=count(Question, Question.status == "draft"),
        suspect_questions=count(Question, Question.scope_status == "suspect"),
        extension_questions=count(Question, Question.scope_status == "extension"),
        papers=count(Paper), by_subject=by_subject, by_type=by_type,
    )


@router.get("/settings", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_db)):
    cfg = ingest_llm.get_settings(db)
    if cfg.get("llm_api_key"):
        cfg["llm_api_key"] = cfg["llm_api_key"][:4] + "****" if len(cfg["llm_api_key"]) > 4 else "****"
    return SettingsOut(**cfg)


@router.put("/settings", response_model=SettingsOut)
def put_settings(body: SettingsIn, db: Session = Depends(get_db)):
    patch = body.model_dump(exclude_unset=True)
    # 掩码值不覆盖真实 key
    if patch.get("llm_api_key", "").endswith("****"):
        patch.pop("llm_api_key", None)
    cfg = ingest_llm.save_settings(db, patch)
    if cfg.get("llm_api_key"):
        cfg["llm_api_key"] = cfg["llm_api_key"][:4] + "****" if len(cfg["llm_api_key"]) > 4 else "****"
    return SettingsOut(**cfg)


@router.get("/images/{path:path}")
def image_file(path: str):
    """题图：path 是相对 data/library 的相对路径，禁止目录穿越。"""
    base = LIBRARY_DIR.resolve()
    target = (base / path).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target)


@router.get("/schema/{name}")
def schema_file(name: str):
    if name not in ("catalog-bundle.schema.json", "question-bundle.schema.json"):
        raise HTTPException(404)
    return FileResponse(SCHEMA_DIR / name, media_type="application/json")


@router.get("/qtypes")
def qtypes():
    return ["口算", "计算", "填空", "选择", "判断", "拼音", "字词", "笔画",
            "连线", "默写", "听写", "古诗", "字谜", "看图列式", "应用", "操作", "其他"]
