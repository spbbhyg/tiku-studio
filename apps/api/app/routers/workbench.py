"""工作台接口：分类维度、错题集、导出日志、AI 用量、批量拆题。

统一前缀 /api/wb，前端「设置 / 题库 / QF题库 / 试卷」共用。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import LIBRARY_DIR, ROOT
from ..db import get_db
from ..models import Book, Paper, PaperQuestion, Question, QuestionVersion
from ..models_wb import CategoryDim, CategoryValue, ExportLog, WrongBook, WrongItem
from ..services import ai as ai_svc
from ..services import versions as ver_svc

router = APIRouter(prefix="/api/wb", tags=["workbench"])

CHEM_TOPICS = ["有机化学", "高分子化学", "晶体化学", "无机综合与结构推断", "方程式与元素化学",
               "热力学与化学平衡", "电化学", "动力学", "分析化学"]


# ------------------------------------------------------------------ 分类维度
class DimIn(BaseModel):
    key: str = ""
    name: str


class ValueIn(BaseModel):
    name: str


def _seed_categories(db: Session) -> None:
    """首次启动塞一个内置「专题」维度（Chemy 那九个）。"""
    if db.scalar(select(func.count()).select_from(CategoryDim)):
        return
    dim = CategoryDim(key="topic", name="专题", sort=0, builtin=True)
    db.add(dim)
    db.flush()
    for i, name in enumerate(CHEM_TOPICS):
        db.add(CategoryValue(dim_key="topic", name=name, sort=i))
    db.commit()


@router.get("/categories")
def list_categories(db: Session = Depends(get_db)):
    _seed_categories(db)
    dims = db.scalars(select(CategoryDim).order_by(CategoryDim.sort, CategoryDim.id)).all()
    vals = db.scalars(select(CategoryValue).order_by(CategoryValue.sort, CategoryValue.id)).all()
    out = []
    for d in dims:
        out.append({
            "key": d.key, "name": d.name, "sort": d.sort, "builtin": bool(d.builtin),
            "values": [{"id": v.id, "name": v.name, "sort": v.sort} for v in vals if v.dim_key == d.key],
        })
    return {"dims": out}


@router.post("/categories")
def create_dim(body: DimIn, db: Session = Depends(get_db)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "维度名不能为空")
    key = (body.key or "").strip() or f"dim{int(time.time())}"
    if db.scalar(select(CategoryDim).where(CategoryDim.key == key)):
        raise HTTPException(400, "这个 key 已存在")
    sort = db.scalar(select(func.max(CategoryDim.sort))) or 0
    db.add(CategoryDim(key=key, name=name, sort=sort + 1))
    db.commit()
    return {"ok": True, "key": key}


@router.patch("/categories/{key}")
def rename_dim(key: str, body: DimIn, db: Session = Depends(get_db)):
    dim = db.scalar(select(CategoryDim).where(CategoryDim.key == key))
    if not dim:
        raise HTTPException(404, "维度不存在")
    if (body.name or "").strip():
        dim.name = body.name.strip()
    db.commit()
    return {"ok": True}


@router.delete("/categories/{key}")
def delete_dim(key: str, db: Session = Depends(get_db)):
    dim = db.scalar(select(CategoryDim).where(CategoryDim.key == key))
    if not dim:
        raise HTTPException(404, "维度不存在")
    db.query(CategoryValue).filter(CategoryValue.dim_key == key).delete()
    db.delete(dim)
    db.commit()
    return {"ok": True}


@router.post("/categories/{key}/values")
def add_value(key: str, body: ValueIn, db: Session = Depends(get_db)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "取值不能为空")
    if not db.scalar(select(CategoryDim).where(CategoryDim.key == key)):
        raise HTTPException(404, "维度不存在")
    if db.scalar(select(CategoryValue).where(CategoryValue.dim_key == key, CategoryValue.name == name)):
        raise HTTPException(400, "这个取值已存在")
    sort = db.scalar(select(func.max(CategoryValue.sort)).where(CategoryValue.dim_key == key)) or 0
    db.add(CategoryValue(dim_key=key, name=name, sort=sort + 1))
    db.commit()
    return {"ok": True}


@router.patch("/values/{vid}")
def rename_value(vid: int, body: ValueIn, db: Session = Depends(get_db)):
    v = db.get(CategoryValue, vid)
    if not v:
        raise HTTPException(404, "取值不存在")
    if (body.name or "").strip():
        v.name = body.name.strip()
    db.commit()
    return {"ok": True}


@router.delete("/values/{vid}")
def delete_value(vid: int, db: Session = Depends(get_db)):
    v = db.get(CategoryValue, vid)
    if not v:
        raise HTTPException(404, "取值不存在")
    db.delete(v)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ 题库排序
class ReorderIn(BaseModel):
    ids: list[int]


@router.post("/questions/reorder")
def reorder_questions(body: ReorderIn, db: Session = Depends(get_db)):
    if not body.ids:
        raise HTTPException(400, "没有题目")
    for i, qid in enumerate(body.ids):
        q = db.get(Question, qid)
        if q:
            q.sort_order = i
    db.commit()
    return {"ok": True, "count": len(body.ids)}


# ------------------------------------------------------------------ 错题集
class BookIn(BaseModel):
    name: str
    note: str | None = None


class ItemsIn(BaseModel):
    question_ids: list[int]


@router.get("/wrongbooks")
def list_wrongbooks(db: Session = Depends(get_db)):
    rows = db.execute(
        select(WrongBook, func.count(WrongItem.id))
        .outerjoin(WrongItem, WrongItem.book_id == WrongBook.id)
        .group_by(WrongBook.id).order_by(WrongBook.id)
    ).all()
    return {"books": [{"id": b.id, "name": b.name, "note": b.note,
                       "count": n, "created_at": b.created_at.isoformat()} for b, n in rows]}


@router.post("/wrongbooks")
def create_wrongbook(body: BookIn, db: Session = Depends(get_db)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "名字不能为空")
    if db.scalar(select(WrongBook).where(WrongBook.name == name)):
        raise HTTPException(400, "已经有同名错题集")
    b = WrongBook(name=name, note=(body.note or "").strip() or None)
    db.add(b)
    db.commit()
    return {"ok": True, "id": b.id}


@router.patch("/wrongbooks/{bid}")
def rename_wrongbook(bid: int, body: BookIn, db: Session = Depends(get_db)):
    b = db.get(WrongBook, bid)
    if not b:
        raise HTTPException(404, "错题集不存在")
    if (body.name or "").strip():
        b.name = body.name.strip()
    if body.note is not None:
        b.note = body.note.strip() or None
    db.commit()
    return {"ok": True}


@router.delete("/wrongbooks/{bid}")
def delete_wrongbook(bid: int, db: Session = Depends(get_db)):
    b = db.get(WrongBook, bid)
    if not b:
        raise HTTPException(404, "错题集不存在")
    db.query(WrongItem).filter(WrongItem.book_id == bid).delete()
    db.delete(b)
    db.commit()
    return {"ok": True}


@router.get("/wrongbooks/{bid}/items")
def wrongbook_items(bid: int, db: Session = Depends(get_db)):
    if not db.get(WrongBook, bid):
        raise HTTPException(404, "错题集不存在")
    rows = db.execute(
        select(WrongItem, Question).join(Question, Question.id == WrongItem.question_id)
        .where(WrongItem.book_id == bid).order_by(WrongItem.last_at.desc())
    ).all()
    out = []
    for it, q in rows:
        out.append({
            "question_id": q.id, "stem": q.stem, "options": q.options or [], "answer": q.answer,
            "analysis": q.analysis, "q_type": q.q_type, "difficulty": q.difficulty,
            "knowledge_points": q.knowledge_points or [], "images": q.images or [],
            "tags": q.tags or [], "source_title": q.source_title,
            "wrong_count": it.wrong_count, "last_at": it.last_at.isoformat(),
        })
    return {"items": out}


@router.post("/wrongbooks/{bid}/items")
def add_wrong_items(bid: int, body: ItemsIn, db: Session = Depends(get_db)):
    b = db.get(WrongBook, bid)
    if not b:
        raise HTTPException(404, "错题集不存在")
    added = 0
    for qid in body.question_ids or []:
        if not db.get(Question, qid):
            continue
        it = db.scalar(select(WrongItem).where(WrongItem.book_id == bid, WrongItem.question_id == qid))
        if it:
            it.wrong_count += 1
            it.last_at = datetime.utcnow()
        else:
            db.add(WrongItem(book_id=bid, question_id=qid))
            added += 1
    db.commit()
    return {"ok": True, "added": added}


@router.delete("/wrongbooks/{bid}/items/{qid}")
def remove_wrong_item(bid: int, qid: int, db: Session = Depends(get_db)):
    it = db.scalar(select(WrongItem).where(WrongItem.book_id == bid, WrongItem.question_id == qid))
    if it:
        db.delete(it)
        db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ 导出日志
class LogIn(BaseModel):
    paper_id: int | None = None
    title: str = ""
    kind: str = "pdf"
    item_count: int = 0
    total_score: float = 0
    versions: list[dict] = []
    file_name: str = ""
    file_size: int = 0
    note: str | None = None


@router.get("/export-logs")
def list_export_logs(paper_id: int | None = None, limit: int = 50, db: Session = Depends(get_db)):
    stmt = select(ExportLog).order_by(ExportLog.id.desc()).limit(max(1, min(200, limit)))
    if paper_id:
        stmt = select(ExportLog).where(ExportLog.paper_id == paper_id) \
            .order_by(ExportLog.id.desc()).limit(max(1, min(200, limit)))
    rows = db.scalars(stmt).all()
    return {"logs": [{
        "id": r.id, "paper_id": r.paper_id, "title": r.title, "kind": r.kind,
        "item_count": r.item_count, "total_score": r.total_score, "versions": r.versions or [],
        "fingerprint": r.fingerprint, "file_name": r.file_name, "file_size": r.file_size,
        "note": r.note, "created_at": r.created_at.isoformat(),
    } for r in rows]}


@router.post("/export-logs")
def add_export_log(body: LogIn, db: Session = Depends(get_db)):
    fp = hashlib.md5(json.dumps(body.versions, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    r = ExportLog(paper_id=body.paper_id, title=(body.title or "")[:200], kind=body.kind,
                  item_count=body.item_count, total_score=body.total_score,
                  versions=body.versions, fingerprint=fp, file_name=body.file_name[:200],
                  file_size=body.file_size, note=body.note)
    db.add(r)
    db.commit()
    return {"ok": True, "id": r.id, "fingerprint": fp}


# ------------------------------------------------------------------ AI 用量 / 额度
def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


@router.get("/ai-quota")
def ai_quota(db: Session = Depends(get_db)):
    """免费模型额度/用量：优先读本机 DSH 的统计，其次读本地调用流水。"""
    home = Path(os.path.expanduser("~")) / ".dsh"
    stats = _read_json(home / "our-free-model" / "stats.json", {}) or {}
    snaps = _read_json(home / "dsh-usage" / "provider-snapshots.json", {}) or {}
    catalog = _read_json(home / "our-free-model" / "catalog.json", {}) or {}

    days = stats.get("days") or {}
    today = datetime.now().strftime("%Y-%m-%d")
    models: dict[str, dict] = {}
    for day, info in sorted(days.items()):
        for name, m in (info.get("models") or {}).items():
            cur = models.setdefault(name, {"name": name, "calls": 0, "failed": 0, "input": 0,
                                           "output": 0, "reasoning": 0, "cacheRead": 0,
                                           "ttftMs": 0, "ttftSamples": 0, "today": 0})
            for k in ("calls", "failed", "input", "output", "reasoning", "cacheRead", "ttftMs", "ttftSamples"):
                cur[k] += int(m.get(k) or 0)
            if day == today:
                cur["today"] += int(m.get("calls") or 0)

    # 目录里标注的上下文/思考上限，方便对比选型
    meta = {}
    for item in (catalog.get("models") or []):
        if isinstance(item, dict) and item.get("id"):
            meta[str(item["id"]).split("/")[-1]] = item

    rows = []
    for name, m in models.items():
        ttft = int(m["ttftMs"] / m["ttftSamples"]) if m["ttftSamples"] else 0
        rows.append({
            "model": name, "calls": m["calls"], "failed": m["failed"], "today_calls": m["today"],
            "input": m["input"], "output": m["output"], "reasoning": m["reasoning"],
            "cache_read": m["cacheRead"], "avg_ttft_ms": ttft,
            "description": (meta.get(name) or {}).get("description") or "",
        })
    rows.sort(key=lambda r: -r["calls"])

    balances = []
    for key, p in (snaps.get("providers") or {}).items():
        bal = p.get("balance") or {}
        if bal.get("totalBalance") is not None:
            balances.append({"provider": p.get("displayName") or key,
                             "currency": bal.get("currency") or "CNY",
                             "balance": bal.get("totalBalance")})

    return {"date": today, "models": rows, "balances": balances,
            "has_gateway": bool(stats), "note": "免费通道没有官方上限接口，这里按本机实测调用量/延迟统计。"}


# ------------------------------------------------------------------ 批量拆题
_LEAD_RE = re.compile(r"^\s*(?:第?\s*\d+\s*[题.、)）]|[(（]\s*\d+\s*[)）]|[A-Da-d]\s*[.、])\s*")


def _norm_stem(text: str) -> str:
    """去前导编号、去所有空白，用于判重。"""
    t = _LEAD_RE.sub("", (text or "").strip())
    t = re.sub(r"\s+", "", t)
    return t


@router.post("/extract")
async def batch_extract(
    files: list[UploadFile] = File(...),
    book_id: int | None = Form(None),
    label: str = Form(""),
    db: Session = Depends(get_db),
):
    """多份 PDF 一次性拆题：每份调 parse_paper，答案缺省由 AI 补。"""
    if not files:
        raise HTTPException(400, "请选择 PDF")
    if book_id is None:
        book = db.scalar(select(Book).order_by(Book.id))
        if not book:
            raise HTTPException(400, "还没有书册，请先在题库里建一本")
        book_id = book.id
    elif not db.get(Book, book_id):
        raise HTTPException(404, "书册不存在")

    out_dir = Path(LIBRARY_DIR) / "extract"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for up in files[:10]:
        raw = await up.read()
        if not raw:
            continue
        safe = (up.filename or "upload.pdf").replace("/", "_").replace("\\", "_")
        path = out_dir / f"{int(time.time())}-{hashlib.md5(raw).hexdigest()[:8]}-{safe}"
        path.write_bytes(raw)
        item = {"file": safe, "questions": 0, "title": "", "error": ""}
        try:
            text = ai_svc.pdf_to_text(path)
            if not text.strip():
                item["error"] = "没有文字层"
                results.append(item)
                continue
            parsed = ai_svc.parse_paper(db, text)
            item["title"] = parsed.get("title") or safe
            created = 0
            for q in parsed.get("questions") or []:
                stem = (q.get("stem") or "").strip()
                if not stem:
                    continue
                norm = _norm_stem(stem)
                h = hashlib.md5(norm[:60].encode("utf-8")).hexdigest()[:32]
                # 判重跨书册：同一道题换个书册也不重复入库
                if db.scalar(select(Question).where(Question.content_hash == h)):
                    continue
                if norm:
                    head = norm[:60]
                    existing = db.scalars(select(Question)).all()
                    if any(_norm_stem(x.stem)[:60] == head for x in existing):
                        continue
                db.add(Question(
                    book_id=book_id, content_hash=h, stem=stem,
                    options=q.get("options") or [], answer=(q.get("answer") or "").strip(),
                    analysis=(q.get("analysis") or "").strip() or None,
                    q_type=(q.get("q_type") or "综合")[:16],
                    difficulty=int(q.get("difficulty") or 2),
                    knowledge_points=q.get("knowledge_points") or [],
                    images=[], tags=[t for t in (q.get("knowledge_points") or [])][:3],
                    source_title=(label or parsed.get("title") or safe)[:200],
                    source_page=q.get("page") or None,
                    status="draft", scope_status="pending",
                ))
                created += 1
            db.commit()
            item["questions"] = created
        except HTTPException as exc:
            db.rollback()
            item["error"] = str(exc.detail)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            item["error"] = f"{type(exc).__name__}: {exc}"
        results.append(item)
    total = sum(r["questions"] for r in results)
    return {"ok": True, "book_id": book_id, "total": total, "files": results}


# ------------------------------------------------------------------ 从试题篮建卷
class BasketItemIn(BaseModel):
    question_id: int
    score: float = 10
    align: str = "left"          # left | center | right
    font_size: float = 0
    color: str = ""


class BasketPaperIn(BaseModel):
    title: str = ""
    duration_minutes: int = 90
    subject: str = "化学"
    items: list[BasketItemIn]
    style: dict = {}


def _q_version(db: Session, qid: int) -> dict:
    v = db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == qid)
                  .order_by(QuestionVersion.version_no.desc()))
    if v:
        return {"version": v.version_no, "ver_id": v.id, "updated_at": v.created_at.isoformat()}
    return {"version": 1, "ver_id": None, "updated_at": None}


@router.post("/papers")
def create_paper_from_basket(body: BasketPaperIn, db: Session = Depends(get_db)):
    if not body.items:
        raise HTTPException(400, "试题篮是空的")
    title = (body.title or "").strip() or f"试题篮组卷 {datetime.now().strftime('%m-%d %H:%M')}"
    total = round(sum(float(i.score or 0) for i in body.items), 1) or 100
    paper = Paper(title=title[:200], subject=body.subject or "化学", grade=12, semester="全",
                  total_score=total, duration_minutes=int(body.duration_minutes or 90),
                  include_extension=False, config={"source": "basket", "style": body.style or {}})
    db.add(paper)
    db.flush()
    for seq, item in enumerate(body.items, 1):
        q = db.get(Question, item.question_id)
        if not q:
            continue
        vinfo = _q_version(db, q.id)
        snap = {
            "stem": q.stem, "options": q.options or [], "answer": q.answer,
            "analysis": q.analysis, "q_type": q.q_type, "difficulty": q.difficulty,
            "knowledge_points": q.knowledge_points or [], "images": q.images or [],
            "source_book_title": q.source_title or "", "unit_name": "",
            "tags": q.tags or [],
            "align": item.align, "font_size": item.font_size, "color": item.color,
            "_version": vinfo,
        }
        db.add(PaperQuestion(paper_id=paper.id, question_id=q.id, seq=seq,
                             score=round(float(item.score or 10), 1), snapshot=snap))
    db.flush()
    db.commit()
    return {"ok": True, "id": paper.id, "title": paper.title, "total_score": total}


@router.get("/papers/{paper_id}/versions")
def paper_versions(paper_id: int, db: Session = Depends(get_db)):
    paper = db.get(Paper, paper_id)
    if not paper:
        raise HTTPException(404, "试卷不存在")
    out = []
    for it in paper.items:
        v = (it.snapshot or {}).get("_version") or {}
        out.append({"question_id": it.question_id, "seq": it.seq,
                    "title": (it.snapshot or {}).get("stem", "")[:40],
                    "version": v.get("version") or 1, "updated_at": v.get("updated_at")})
    return {"paper_id": paper_id, "items": out}


@router.post("/papers/{paper_id}/log-export")
def log_paper_export(paper_id: int, body: dict, db: Session = Depends(get_db)):
    """导出后记一笔：哪一版题目生成了哪一版文件。"""
    paper = db.get(Paper, paper_id)
    if not paper:
        raise HTTPException(404, "试卷不存在")
    items = []
    for it in paper.items:
        v = (it.snapshot or {}).get("_version") or {}
        items.append({"question_id": it.question_id, "seq": it.seq,
                      "version": v.get("version") or 1, "ver_id": v.get("ver_id"),
                      "updated_at": v.get("updated_at")})
    fp = hashlib.md5(json.dumps(items, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    r = ExportLog(paper_id=paper_id, title=paper.title, kind=str(body.get("kind") or "pdf"),
                  item_count=len(items), total_score=paper.total_score,
                  versions=items, fingerprint=fp,
                  file_name=str(body.get("file_name") or ""), file_size=int(body.get("file_size") or 0),
                  note=body.get("note"))
    db.add(r)
    db.commit()
    return {"ok": True, "id": r.id, "fingerprint": fp, "versions": items}
