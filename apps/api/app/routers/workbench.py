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
            "answer_layout": q.answer_layout or {},
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
    """多份 PDF 一次性拆题：题目卷与答案卷一起选时按大题号把答案配到题目。"""
    if not files:
        raise HTTPException(400, "请选择 PDF")
    if book_id is None:
        book = db.scalar(select(Book).order_by(Book.id))
        if not book:
            raise HTTPException(400, "还没有书册，请先在题库里建一本")
        book_id = book.id
    elif not db.get(Book, book_id):
        raise HTTPException(404, "书册不存在")

    from .manual import _clean_filename

    def _role(name: str) -> str:
        low = (name or "").lower()
        return "answer" if ("答案" in (name or "") or "answer" in low or "ans" in low) else "question"

    out_dir = Path(LIBRARY_DIR) / "extract"
    out_dir.mkdir(parents=True, exist_ok=True)
    uploads = []
    for up in files[:10]:
        raw = await up.read()
        if not raw:
            continue
        safe = _clean_filename(up.filename) or "upload.pdf"
        safe = safe.replace("/", "_").replace("\\", "_")
        path = out_dir / f"{int(time.time())}-{hashlib.md5(raw).hexdigest()[:8]}-{safe}"
        path.write_bytes(raw)
        uploads.append((safe, path, _role(safe)))

    # 先解析答案卷：按大题号收集答案文本
    answers: dict[int, dict] = {}
    results = []
    for safe, path, role in uploads:
        if role != "answer":
            continue
        item = {"file": safe, "questions": 0, "title": "", "error": ""}
        try:
            text = ai_svc.pdf_to_text(path)
            if not text.strip():
                item["error"] = "没有文字层"
                results.append(item)
                continue
            # 先按纯文字切段兜底（扫描件 OCR 后也能拿到答案文本）
            for main, section in _answer_sections_from_text(text).items():
                answers[main] = {"answer": section, "analysis": ""}
            parsed = ai_svc.parse_paper(db, text)
            for q in _merge_by_main(parsed.get("questions") or []):
                main = _main_qnum_of_question((q.get("stem") or "") + "\n" + (q.get("answer") or ""))
                if not main:
                    continue
                ans = (q.get("answer") or "").strip()
                if ans:
                    prev = (answers.get(main) or {}).get("answer") or ""
                    answers[main] = {"answer": (prev + "\n\n" + ans).strip() if prev else ans,
                                     "analysis": (q.get("analysis") or "").strip()}
            item["title"] = parsed.get("title") or safe
        except Exception as exc:  # noqa: BLE001
            item["error"] = f"{type(exc).__name__}: {exc}"
        results.append(item)

    for safe, path, role in uploads:
        if role == "answer":
            continue
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
            for q in _merge_by_main(parsed.get("questions") or []):
                stem = (q.get("stem") or "").strip()
                if not stem:
                    continue
                main = _main_qnum_of_question(stem)
                ans = answers.get(main) or {}
                if not (q.get("answer") or "").strip() and ans.get("answer"):
                    q["answer"] = ans["answer"]
                if not (q.get("analysis") or "").strip() and ans.get("analysis"):
                    q["analysis"] = ans["analysis"]
                norm = _norm_stem(stem)
                h = hashlib.md5(norm[:60].encode("utf-8")).hexdigest()[:32]
                if db.scalar(select(Question).where(Question.content_hash == h)):
                    continue
                if norm:
                    head = norm[:60]
                    existing = db.scalars(select(Question)).all()
                    if any(_norm_stem(x.stem)[:60] == head for x in existing):
                        continue
                kps = [str(x) for x in (q.get("knowledge_points") or []) if str(x).strip()]
                if not kps:
                    guess = ai_svc.heuristic_classify(stem, topics=ai_svc.CHEM_TOPICS)
                    kps = (guess.get("topics") or [])[:2]
                if kps:
                    try:
                        from .authoring import _ensure_topic_tags
                        _ensure_topic_tags(db, kps)
                    except Exception:  # noqa: BLE001
                        pass
                db.add(Question(
                    book_id=book_id, content_hash=h, stem=stem,
                    options=q.get("options") or [], answer=(q.get("answer") or "").strip(),
                    analysis=(q.get("analysis") or "").strip() or None,
                    q_type=(q.get("q_type") or "综合")[:16],
                    difficulty=int(q.get("difficulty") or 2),
                    knowledge_points=kps,
                    images=[], tags=kps[:3],
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
    return {"ok": True, "book_id": book_id, "total": total, "files": results,
            "answers_matched": len(answers)}


# ------------------------------------------------------------------ 按题号裁图拆题
_QNUM_RES = (
    re.compile(r"^\s*第\s*(\d{1,2})\s*题"),
    re.compile(r"^\s*(\d{1,2})\s*[-－—]\s*\d+"),
    re.compile(r"^\s*(\d{1,2})\s*[.、．](?!\d)\s*"),
)


def _qnum_of(text: str) -> int:
    """取大题号：1、1-1、1-2、第1题 都返回 1。"""
    line = (text or "").splitlines()[0] if (text or "").strip() else ""
    for rx in _QNUM_RES:
        m = rx.match(line)
        if m:
            try:
                n = int(m.group(1))
            except Exception:  # noqa: BLE001
                return 0
            return n if 1 <= n <= 99 else 0
    return 0


def _main_qnum_of_question(text: str) -> int:
    """从整段题目里找大题号；优先 1-1 这种小问号，其次 第N题 / N.。"""
    text = text or ""
    m = re.search(r"(?:^|\n)\s*(\d{1,2})\s*[-－—]\s*\d+", text)
    if not m:
        m = re.search(r"(?:^|\n)\s*第\s*(\d{1,2})\s*题", text)
    if not m:
        m = re.search(r"(?:^|\n)\s*(\d{1,2})\s*[.、．]\s*", text)
    if not m:
        return 0
    try:
        n = int(m.group(1))
    except Exception:  # noqa: BLE001
        return 0
    return n if 1 <= n <= 99 else 0


def _merge_by_main(questions: list[dict]) -> list[dict]:
    """把 1-1、1-2、1-3… 合成一道大题，保留第一个题的题干/答案，后续内容追加。"""
    merged: list[dict] = []
    by_main: dict[int, dict] = {}
    for q in questions or []:
        stem = (q.get("stem") or "").strip()
        if not stem:
            continue
        main = _main_qnum_of_question(stem)
        if not main:
            merged.append(q)
            continue
        first = by_main.get(main)
        if first is None:
            by_main[main] = q
            merged.append(q)
            continue
        follow = re.sub(r"^\s*第\s*\d{1,2}\s*题[^\n]*\n?", "", stem).strip()
        if follow:
            first["stem"] = (first.get("stem") or "").rstrip() + "\n\n" + follow
        ans = (q.get("answer") or "").strip()
        if ans:
            first["answer"] = ((first.get("answer") or "").strip() + "\n\n" + ans).strip()
        ana = (q.get("analysis") or "").strip()
        if ana:
            first["analysis"] = ((first.get("analysis") or "").strip() + "\n\n" + ana).strip()
        kp = [x for x in (q.get("knowledge_points") or []) if x not in (first.get("knowledge_points") or [])]
        if kp:
            first["knowledge_points"] = (first.get("knowledge_points") or []) + kp
    return merged


def _answer_sections_from_text(text: str) -> dict[int, str]:
    """从答案卷纯文字里按大题号切段；AI 没抽到答案时用它兜底。"""
    out: dict[int, list[str]] = {}
    cur = 0
    for line in (text or "").splitlines():
        t = line.strip()
        if not t or t.startswith("==="):
            continue
        m = re.match(r"^\s*(?:第\s*)?(\d{1,2})\s*(?:[-－—]\s*\d+|[.、．](?!\d))\s*", line)
        if m:
            try:
                cur = int(m.group(1))
            except Exception:  # noqa: BLE001
                cur = 0
            if 1 <= cur <= 99:
                out.setdefault(cur, []).append(t)
            continue
        if cur:
            out[cur].append(t)
    return {k: "\n".join(v).strip() for k, v in out.items() if v}


def _trim_png(raw: bytes):
    from io import BytesIO

    from PIL import Image, ImageChops
    im = Image.open(BytesIO(raw)).convert("RGB")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    box = ImageChops.difference(im, bg).getbbox()
    if box:
        _x0, y0, _x1, y1 = box
        pad = 8
        # 只裁上下空白，保留整栏宽度：题目里的反应式/结构式依赖横向位置
        im = im.crop((0, max(0, y0 - pad), im.width, min(im.height, y1 + pad)))
    out = BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def _save_question_png(raw: bytes) -> str:
    from .manual import _save_png
    return _save_png(raw)["path"]


@router.post("/extract-images")
async def extract_images(
    files: list[UploadFile] = File(...),
    book_id: int | None = Form(None),
    label: str = Form(""),
    db: Session = Depends(get_db),
):
    """按题号把扫描/文字 PDF 裁成题图，题干和答案各一张，自动去白边。

    不依赖大模型：先看 PDF 文字层，扫描页再用本机 OCR 找题号。
    适合「题目卷 + 答案卷」两份 PDF，答案按题号跟随题目入库。
    """
    import fitz

    if not files:
        raise HTTPException(400, "请选择 PDF")
    answers: dict[int, list] = {}
    results = []
    created_total = 0
    old_questions = db.scalars(select(Question)).all()
    seen = {(_norm_stem(q.stem) + "|" + str(q.content_hash)) for q in old_questions}

    def _file_role(name: str) -> str:
        low = (name or "").lower()
        return "answer" if ("答案" in (name or "") or "answer" in low or "ans" in low) else "question"

    def _page_crops(path, role: str):
        out = []
        doc = fitz.open(str(path))
        try:
            for pno in range(min(len(doc), 60)):
                page = doc[pno]
                lines, W, H = ai_svc._page_lines(page)
                if not lines:
                    lines = ai_svc._ocr_page(page)
                # 文字层给的是绝对坐标；OCR 给的是 0-1 归一化坐标，这里统一成绝对坐标
                ymax = max((float(ln.get("y1") or 0) for ln in lines), default=0.0)
                unit = H if ymax <= 1.5 else 1.0
                starts = []
                for ln in lines:
                    n = _qnum_of(ln.get("text") or "")
                    if not n:
                        continue
                    yy = float(ln.get("y0") or 0) * unit
                    # 页脚「第 N 题 / 共 N 页」之类不是新题
                    if yy > H * 0.94:
                        continue
                    starts.append((n, yy, (ln.get("text") or "").strip()))
                starts.sort(key=lambda x: x[1])
                if not starts:
                    continue
                # 同一大题号只取第一次出现的位置，1-1、1-2… 合成一整段
                groups = []
                for item in starts:
                    if groups and groups[-1][0] == item[0]:
                        continue
                    groups.append(item)
                for i, (num, y0, head) in enumerate(groups):
                    y1 = groups[i + 1][1] - 6 if i + 1 < len(groups) else H * 0.985
                    if y1 - y0 < 24:
                        continue
                    clip = fitz.Rect(0, max(0.0, y0 - 8), W, min(H, y1))
                    pix = page.get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), clip=clip, alpha=False)
                    raw = _trim_png(pix.tobytes("png"))
                    out.append((pno + 1, num, raw, head))
        finally:
            doc.close()
        return out

    # 先处理答案卷，按题号存图；再处理题目卷
    ordered = sorted(files, key=lambda u: 0 if _file_role(u.filename or "") == "answer" else 1)
    for up in ordered[:10]:
        safe = (up.filename or "upload.pdf").replace("/", "_").replace("\\", "_")
        role = _file_role(safe)
        item = {"file": safe, "questions": 0, "title": "", "error": ""}
        try:
            raw = await up.read()
            if not raw:
                continue
            tmp = Path(LIBRARY_DIR) / "extract"
            tmp.mkdir(parents=True, exist_ok=True)
            path = tmp / f"{int(time.time())}-{hashlib.md5(raw).hexdigest()[:8]}.pdf"
            path.write_bytes(raw)
            if role == "answer":
                for _pno, num, png, _t in _page_crops(path, role):
                    answers.setdefault(num, []).append(_save_question_png(png))
                item["title"] = safe
                results.append(item)
                continue

            book = db.get(Book, book_id) if book_id else None
            if book is None:
                book = db.scalar(select(Book).where(Book.title == safe))
                if book is None:
                    book = Book(title=safe[:120], role="paper_set", subject="化学", grade=12,
                                semester="全", publisher="AI 裁图拆题", edition_year=datetime.now().year)
                    db.add(book)
                    db.flush()
            item["title"] = book.title
            created = 0
            # 同一大题可能跨页：按大题号把各页裁图收成一道题，避免 1-3 被拆成新题
            by_main: dict[int, list] = {}
            heads: dict[int, tuple] = {}
            for pno, num, png, head in _page_crops(path, role):
                by_main.setdefault(num, []).append((pno, png))
                heads.setdefault(num, (pno, head))
            for num in sorted(by_main):
                paths = [_save_question_png(png) for _pno, png in by_main[num]]
                blob = b"".join(png for _pno, png in by_main[num])
                h = hashlib.md5(blob).hexdigest()[:32]
                if db.scalar(select(Question).where(Question.content_hash == h)):
                    continue
                images = [{"path": rel, "caption": "题干"} for rel in paths]
                for extra in answers.get(num) or []:
                    images.append({"path": extra, "caption": "参考答案"})
                first_pno = heads[num][0]
                kps = (ai_svc.heuristic_classify(heads[num][1] or f"第{num}题", topics=ai_svc.CHEM_TOPICS).get("topics") or [])[:2]
                if kps:
                    try:
                        from .authoring import _ensure_topic_tags
                        _ensure_topic_tags(db, kps)
                    except Exception:  # noqa: BLE001
                        pass
                db.add(Question(
                    book_id=book.id, content_hash=h,
                    stem=f"第{num}题",
                    options=[], answer="", analysis=None,
                    q_type="综合", difficulty=2, knowledge_points=kps,
                    images=images, tags=kps,
                    source_title=(label or safe)[:200], source_page=str(first_pno),
                    status="draft", scope_status="pending",
                ))
                created += 1
            db.commit()
            item["questions"] = created
            created_total += created
        except HTTPException as exc:
            db.rollback()
            item["error"] = str(exc.detail)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            item["error"] = f"{type(exc).__name__}: {exc}"
        results.append(item)
    return {"ok": True, "total": created_total, "files": results}


# ------------------------------------------------------------------ 从试题篮建卷
class BasketItemIn(BaseModel):
    question_id: int
    score: float = 10
    percent: float | None = None  # 手填占比（%，只用于导出的「占 X%」；空则按分值自动算）
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


@router.post("/parse-preview")
async def parse_preview(
    paper: UploadFile = File(..., description="题目卷 PDF"),
    answer: UploadFile | None = File(None, description="答案卷 PDF（可选）"),
):
    """把题目卷（+ 答案卷）按「第N题」切开，同题号配对答案，返回预览。"""
    import tempfile

    def pdf_text(f: UploadFile) -> str:
        data = f.file.read()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.write(data)
        tmp.close()
        try:
            return ai_svc.pdf_to_text(tmp.name, max_pages=60)
        finally:
            try:
                import os
                os.unlink(tmp.name)
            except OSError:
                pass

    import os
    import tempfile

    def save_tmp(f: UploadFile) -> str:
        data = f.file.read()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.write(data)
        tmp.close()
        return tmp.name

    qpath = save_tmp(paper)
    apath = save_tmp(answer) if answer is not None else None
    try:
        qt = ai_svc.pdf_to_text(qpath, max_pages=60)
        at = ai_svc.pdf_to_text(apath, max_pages=60) if apath else ""
    finally:
        for p_ in (apath,):
            if p_:
                try:
                    os.unlink(p_)
                except OSError:
                    pass
    if not qt.strip():
        try:
            os.unlink(qpath)
        except OSError:
            pass
        raise HTTPException(400, "题目卷没有文字层（可能是扫描件），请用「裁剪」手动做题图")
    items = pp.pair(qt, at)
    _crop_question_images(qpath, items)
    try:
        os.unlink(qpath)
    except OSError:
        pass
    return {
        "ok": True,
        "count": len(items),
        "matched": sum(1 for x in items if x["answer"].strip()),
        "items": items,
    }


class ParseImportIn(BaseModel):
    book_id: int
    items: list[dict]
    source_title: str | None = None
    label: str | None = None


@router.post("/parse-import")
def parse_import(body: ParseImportIn, db: Session = Depends(get_db)):
    """把预览里选中的题写进题库（草稿）。"""
    created: list[int] = []
    skipped = 0
    for it in body.items:
        stem = (it.get("stem") or "").strip()
        if not stem:
            continue
        key = _stem_key(stem)
        names = {_stem_key(x) for x in db.execute(
            select(Question.stem).where(Question.book_id == body.book_id)).scalars().all() if x}
        if key and key in names:
            skipped += 1
            continue
        answer_text = (it.get("answer") or "").strip()
        q = Question(
            book_id=body.book_id,
            content_hash=_question_hash(body.book_id, {
                "stem": stem, "q_type": "简答", "options": None,
                "answer": answer_text, "knowledge_points": None, "source_page": None,
            }),
            stem=stem,
            answer=answer_text or None,
            analysis=None,
            q_type="简答",
            difficulty=3,
            status="draft",
            scope_status="pending",
            source_title=body.source_title or (body.label or "AI 拆题"),
            source_page=None,
        )
        db.add(q)
        db.flush()
        created.append(q.id)
    db.commit()
    return {"ok": True, "created": len(created), "skipped": skipped, "ids": created}


# ---------------------------------------------------------------- 存储清理
def _all_image_refs(db: Session) -> str:
    """把题库里所有可能引用图片的字段拼成一个大字符串，用于子串判活。"""
    chunks: list[str] = []
    for q in db.execute(select(Question)).scalars():
        chunks.append(q.stem or "")
        chunks.append(q.answer or "")
        chunks.append(q.analysis or "")
        for col in (q.images, q.layout, q.answer_layout):
            if col:
                chunks.append(json.dumps(col, ensure_ascii=False))
    return "\n".join(chunks)


def _scan_cleanup(db: Session) -> dict:
    refs = _all_image_refs(db)
    qdir = LIBRARY_DIR / "questions"
    orphan_img: list[Path] = []
    if qdir.exists():
        for p in qdir.rglob("*.png"):
            # 只清 1 天前的：避免删掉正在编辑、还没保存进库的图
            if p.name not in refs and time.time() - p.stat().st_mtime > 86400:
                orphan_img.append(p)
    up = DATA_DIR / "uploads"
    old_uploads = [p for p in up.glob("*") if p.is_file() and time.time() - p.stat().st_mtime > 7 * 86400] if up.exists() else []
    ex = DATA_DIR / "exports"
    old_exports = [p for p in ex.glob("*") if p.is_file() and time.time() - p.stat().st_mtime > 7 * 86400] if ex.exists() else []
    size = lambda xs: sum(p.stat().st_size for p in xs if p.exists())
    return {
        "orphan_images": [p for p in orphan_img],
        "old_uploads": old_uploads,
        "old_exports": old_exports,
        "stats": {
            "orphan_images": {"count": len(orphan_img), "bytes": size(orphan_img)},
            "old_uploads": {"count": len(old_uploads), "bytes": size(old_uploads)},
            "old_exports": {"count": len(old_exports), "bytes": size(old_exports)},
            "total_bytes": size(orphan_img) + size(old_uploads) + size(old_exports),
        },
    }


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
