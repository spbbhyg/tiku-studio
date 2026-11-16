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

from ..config import DATA_DIR, LIBRARY_DIR, ROOT
from ..db import get_db
from ..services.bundle_import import _question_hash
from ..models import Book, Paper, PaperQuestion, Question, QuestionVersion
from ..models_wb import CategoryDim, CategoryValue, ExportLog, WrongBook, WrongItem
from ..services import ai as ai_svc
from ..services import paper_parse as pp
from ..services import versions as ver_svc

def _stem_key(text: str) -> str:
    """规范化题干，用于判重：去题号、去空白、取前 60 字。"""
    t = re.sub(r"^\s*\d+\s*[.、．)]\s*", "", (text or "").strip())
    t = re.sub(r"\s+", "", t)
    return t[:60]


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


class BulkDeleteIn(BaseModel):
    ids: list[int] = []


@router.post("/questions/reclassify")
def reclassify_questions(payload: dict | None = None, db: Session = Depends(get_db)) -> dict:
    """给已入库的题目补「专题」分类：文字题直接判定，纯图片题先 OCR 再判定。"""
    from ..models import Question as _Q  # noqa: PLC0415
    from ..services import topic_classify as tc  # noqa: PLC0415
    from ..services import ai as ai_svc  # noqa: PLC0415
    import json as _json  # noqa: PLC0415

    ids = (payload or {}).get("ids") or []
    force = bool((payload or {}).get("force"))
    q = select(_Q)
    if ids:
        q = q.where(_Q.id.in_(ids))
    rows = db.execute(q).scalars().all()

    done = skipped = 0
    changed = []
    for row in rows:
        # 已有专题的不覆盖（OCR 关键词判定对同一张图两次可能给出不同结果）；
        # 需要重判时传 force=True
        if not force and row.knowledge_points:
            skipped += 1
            continue
        text = " ".join([row.stem or "", row.answer or ""])[:6000]
        # 题干只有「第 N 题 xxx」这种标题时，关键词判不出来 → 再退回 OCR 题目图
        topic = tc.classify(text) if len(re.sub(r"\s", "", text)) >= 12 else ""
        if not topic:
            imgs = row.images or []
            if isinstance(imgs, str):
                try:
                    imgs = _json.loads(imgs)
                except Exception:  # noqa: BLE001
                    imgs = []
            rel = ""
            if imgs:
                first = imgs[0]
                rel = first.get("path") if isinstance(first, dict) else str(first)
            if rel:
                try:
                    lines = ai_svc._ocr_image_file(rel)
                    text = " ".join(x.get("text", "") for x in lines)
                except Exception:  # noqa: BLE001
                    text = ""
                topic = tc.classify(text) if text else ""
        if not topic:
            skipped += 1
            continue
        row.knowledge_points = [topic]
        done += 1
        changed.append({"id": row.id, "topic": topic})
    db.commit()
    return {"ok": True, "classified": done, "skipped": skipped, "items": changed}


@router.post("/questions/delete")
def questions_bulk_delete(body: BulkDeleteIn, db: Session = Depends(get_db)):
    """批量删除题目（同时清掉错题集里的引用）。"""
    ids = [int(i) for i in (body.ids or []) if i]
    if not ids:
        return {"ok": True, "deleted": 0}
    from ..models_wb import WrongItem
    db.query(WrongItem).filter(WrongItem.question_id.in_(ids)).delete(synchronize_session=False)
    n = db.query(Question).filter(Question.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "deleted": int(n)}


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
            "answer_layout": q.answer_layout or {},
            "source_book_title": q.source_title or "", "unit_name": "",
            "tags": q.tags or [],
            "align": item.align, "font_size": item.font_size, "color": item.color,
            "percent": item.percent, "layout": q.layout or None,
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


# ---------- 试卷 / 答案 → 结构化预览（不写库） ----------

import re as _re


def _norm(s: str) -> str:
    return "".join((s or "").split())


def _crop_question_images(pdf_path: str, items: list[dict]) -> None:
    """按「第 N 题」标题定位，把每道题的原文区域截成 PNG（跨页可多张），写进 item["crops"]。"""
    import time as _t
    import fitz  # PyMuPDF
    from ..config import LIBRARY_DIR

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return
    try:
        marks: list[tuple[int, float] | None] = []
        for it in items:
            no = int(it.get("no") or 0)
            hit = None
            for pat in (f"第{no} 题", f"第{no}题", f"第 {no} 题", f"第 {no}题"):
                for pi, page in enumerate(doc):
                    rects = page.search_for(pat)
                    if rects:
                        hit = (pi, rects[0].y0)
                        break
                if hit:
                    break
            marks.append(hit)

        stamp = _t.strftime("%Y%m%d-%H%M%S")
        out_dir = LIBRARY_DIR / "split" / stamp
        out_dir.mkdir(parents=True, exist_ok=True)
        last = len(doc) - 1

        for idx, it in enumerate(items):
            crops: list[str] = []
            m = marks[idx]
            if m is None:
                it["crops"] = crops
                continue
            p0, y0 = m
            nxt = next((x for x in marks[idx + 1:] if x), None)
            p1, y1 = (nxt if nxt else (last, 10 ** 6))
            if (p1, y1) <= (p0, y0):
                p1, y1 = p0, 10 ** 6
            for pi in range(p0, min(p1, p0 + 3) + 1):
                page = doc[pi]
                top = max(page.rect.y0 + 20, (y0 - 14) if pi == p0 else page.rect.y0 + 20)
                bot = min(page.rect.y1 - 20, (y1 - 4) if pi == p1 else page.rect.y1 - 20)
                if bot - top < 40:
                    continue
                clip = fitz.Rect(page.rect.x0 + 36, top, page.rect.x1 - 36, bot)
                pix = page.get_pixmap(clip=clip, dpi=150)
                name = f"q{idx + 1:02d}_{pi - p0 + 1}.png"
                pix.save(str(out_dir / name))
                crops.append(f"split/{stamp}/{name}")
            it["crops"] = crops
    finally:
        doc.close()


@router.get("/cleanup/scan")
def cleanup_scan(db: Session = Depends(get_db)):
    r = _scan_cleanup(db)
    return {"ok": True, **r["stats"]}


@router.post("/cleanup")
def cleanup_run(db: Session = Depends(get_db)):
    r = _scan_cleanup(db)
    removed = 0
    for p in r["orphan_images"] + r["old_uploads"] + r["old_exports"]:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return {"ok": True, "removed": removed, "freed_bytes": r["stats"]["total_bytes"]}


@router.post("/export-logs/delete")
def export_logs_delete(body: dict, db: Session = Depends(get_db)):
    ids = [int(i) for i in (body.get("ids") or [])]
    if not ids:
        return {"ok": True, "removed": 0}
    rows = db.execute(select(ExportLog).where(ExportLog.id.in_(ids))).scalars().all()
    for r in rows:
        db.delete(r)
    db.commit()
    return {"ok": True, "removed": len(rows)}
