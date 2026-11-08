"""人工整卷：题目树存取、预览、导出、导出前校验。

一套卷 = papers 一行 + paper_questions 若干行（每行一个大题，snapshot 存小问树）。
paper.config = {"kind": "manual", "template": {...}, "exam": {...}}
"""
from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Paper, PaperQuestion
from ..services import exam_render

router = APIRouter(prefix="/api/exam", tags=["exam"])

PART_LABEL = {"questions": "题目卷", "answers": "答案卷", "all": "合卷"}


# ---------------------------------------------------------------- 转换

def paper_status(paper: Paper) -> str:
    """人工卷状态：draft（草稿，没编辑完）/ done（已出完，进试卷列表）。"""
    cfg = paper.config or {}
    st = str((cfg.get("exam") or {}).get("status") or "").strip()
    if st in ("draft", "done"):
        return st
    # 老数据没有 status 字段：有题目的当已出完，空卷当草稿
    return "done" if list(paper.items or []) else "draft"


def _set_status(db: Session, paper: Paper, status: str) -> None:
    cfg = dict(paper.config or {})
    cfg["kind"] = "manual"
    exam = dict(cfg.get("exam") or {})
    exam["status"] = status
    cfg["exam"] = exam
    paper.config = cfg
    db.commit()


def doc_from_paper(paper: Paper) -> dict:
    cfg = paper.config or {}
    items = []
    for pq in sorted(paper.items, key=lambda x: (x.seq or 0, x.id)):
        snap = dict(pq.snapshot or {})
        snap["number"] = pq.seq or (len(items) + 1)
        snap["score"] = pq.score
        items.append(snap)
    return {
        "id": paper.id,
        "title": paper.title,
        "subtitle": (cfg.get("exam") or {}).get("subtitle") or "",
        "duration": paper.duration_minutes,
        "total_score": paper.total_score,
        "template": cfg.get("template"),
        "items": items,
        "status": paper_status(paper),
        "created_at": str(paper.created_at or ""),
    }


def apply_doc(db: Session, paper: Paper, doc: dict) -> Paper:
    """整卷覆盖保存：标题 / 时长 / 模板 / 大题列表。"""
    title = str(doc.get("title") or paper.title or "").strip() or "未命名整卷"
    paper.title = title[:200]
    try:
        paper.duration_minutes = int(doc.get("duration") or paper.duration_minutes or 120)
    except (TypeError, ValueError):
        pass
    cfg = dict(paper.config or {})
    cfg["kind"] = "manual"
    if "template" in doc:
        cfg["template"] = doc.get("template") or {}
    exam = dict(cfg.get("exam") or {})
    if "subtitle" in doc:
        exam["subtitle"] = doc.get("subtitle") or ""
    if doc.get("status") in ("draft", "done"):
        exam["status"] = doc["status"]
    exam.setdefault("status", "draft")
    cfg["exam"] = exam
    paper.config = cfg

    items = [it for it in (doc.get("items") or []) if isinstance(it, dict)]
    total = 0.0
    clean: list[tuple[dict, float]] = []
    for i, item in enumerate(items, 1):
        try:
            sc = round(float(item.get("score") or 0), 2)
        except (TypeError, ValueError):
            sc = 0.0
        total += sc
        snap = {k: v for k, v in item.items() if k not in ("number",)}
        snap["number"] = i
        clean.append((snap, sc))
    paper.total_score = round(total, 2)

    for pq in list(paper.items):
        db.delete(pq)
    db.flush()
    for i, (snap, sc) in enumerate(clean, 1):
        db.add(PaperQuestion(paper_id=paper.id, question_id=None, seq=i, score=sc, snapshot=snap))
    db.flush()
    return paper


def _paper_or_404(db: Session, paper_id: int) -> Paper:
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    return paper


# ---------------------------------------------------------------- 存取

@router.post("/papers")
def create_paper(body: dict, db: Session = Depends(get_db)):
    title = str(body.get("title") or "").strip() or "新建人工整卷"
    paper = Paper(title=title[:200], subject=str(body.get("subject") or "化学"),
                  grade=int(body.get("grade") or 12), semester=str(body.get("semester") or "全"),
                  total_score=0, duration_minutes=int(body.get("duration") or 120),
                  include_extension=False,
                  config={"kind": "manual", "source": "manual_exam",
                          "template": body.get("template") or {},
                          "exam": {"status": "draft"}})
    db.add(paper)
    db.commit()
    db.refresh(paper)
    return {"id": paper.id, "doc": doc_from_paper(paper)}


@router.get("/drafts")
def list_drafts(db: Session = Depends(get_db)):
    """「出整卷」里的草稿列表：只列没编辑完的人工卷。"""
    rows = db.execute(select(Paper).where(Paper.config.isnot(None)).order_by(Paper.id.desc())).scalars()
    out = []
    for p in rows:
        cfg = p.config or {}
        if cfg.get("kind") != "manual" or paper_status(p) != "draft":
            continue
        items = list(p.items or [])
        subs = 0
        for pq in items:
            subs += _count_subs((pq.snapshot or {}).get("subs") or [])
        out.append({"id": p.id, "title": p.title, "questions": len(items), "subs": subs,
                    "total_score": p.total_score, "duration": p.duration_minutes,
                    "created_at": str(p.created_at or ""), "status": "draft"})
    return {"drafts": out, "count": len(out)}


def _count_subs(subs: list) -> int:
    n = 0
    for s in subs:
        if isinstance(s, dict):
            n += 1 + _count_subs(s.get("subs") or [])
    return n


@router.post("/papers/{paper_id}/finish")
def finish_paper(paper_id: int, db: Session = Depends(get_db)):
    """完成出卷：草稿 → 正式卷（进试卷列表）。"""
    paper = _paper_or_404(db, paper_id)
    _set_status(db, paper, "done")
    db.refresh(paper)
    return {"id": paper.id, "status": "done"}


@router.post("/papers/{paper_id}/reopen")
def reopen_paper(paper_id: int, db: Session = Depends(get_db)):
    """打回草稿：正式卷 → 继续改。"""
    paper = _paper_or_404(db, paper_id)
    _set_status(db, paper, "draft")
    db.refresh(paper)
    return {"id": paper.id, "status": "draft"}


@router.get("/papers/{paper_id}")
def get_doc(paper_id: int, db: Session = Depends(get_db)):
    paper = _paper_or_404(db, paper_id)
    doc = doc_from_paper(paper)
    return {"doc": doc, "issues": exam_render.check_issues(doc)}


@router.put("/papers/{paper_id}")
def save_doc(paper_id: int, body: dict, db: Session = Depends(get_db)):
    paper = _paper_or_404(db, paper_id)
    doc = body.get("doc") if isinstance(body.get("doc"), dict) else body
    apply_doc(db, paper, doc)
    db.commit()
    db.refresh(paper)
    fresh = doc_from_paper(paper)
    return {"id": paper.id, "doc": fresh, "issues": exam_render.check_issues(fresh),
            "saved_at": str(paper.created_at or "")}


@router.post("/papers/{paper_id}/duplicate")
def duplicate_paper(paper_id: int, body: dict | None = None, db: Session = Depends(get_db)):
    src = _paper_or_404(db, paper_id)
    body = body or {}
    doc = doc_from_paper(src)
    doc["title"] = str(body.get("title") or (src.title + "（副本）"))[:200]
    for item in doc["items"]:
        item.pop("id", None)
    paper = Paper(title=doc["title"], subject=src.subject, grade=src.grade, semester=src.semester,
                  total_score=src.total_score, duration_minutes=src.duration_minutes,
                  include_extension=False, config=dict(src.config or {}))
    db.add(paper)
    db.flush()
    apply_doc(db, paper, doc)
    db.commit()
    db.refresh(paper)
    return {"id": paper.id, "doc": doc_from_paper(paper)}


@router.post("/draft/accept")
def accept_draft(body: dict, db: Session = Depends(get_db)):
    """AI 草稿 → 入卷。草稿只在人工点「采用」后才落进卷，避免 AI 垃圾直接进成品。

    body = {paper_id, draft: {stem, answer, analysis, score, q_type},
            source: {doc_id, page, kind, quote, title}, mode: "item"|"sub",
            item_id: 追加小问时挂到哪个大题}
    """
    pid = body.get("paper_id")
    paper = _paper_or_404(db, int(pid)) if pid else None
    if paper is None:
        raise HTTPException(400, "缺少 paper_id")
    draft = body.get("draft") or {}
    stem = str(draft.get("stem") or "").strip()
    if not stem:
        raise HTTPException(400, "草稿没有题干")
    src = dict(body.get("source") or {})
    src.setdefault("kind", "text")
    src["ai"] = True
    doc = doc_from_paper(paper)
    mode = str(body.get("mode") or "sub")
    score = draft.get("score") or 2
    try:
        score = max(1, int(float(score)))
    except (TypeError, ValueError):
        score = 2
    sub = {"id": "s" + uuid.uuid4().hex[:8], "stem": stem, "score": score,
           "answer": str(draft.get("answer") or "").strip(),
           "points": [], "images": [], "answer_images": [], "subs": [],
           "source": src, "status": "accepted"}
    if mode == "item" or not doc.get("items"):
        doc.setdefault("items", []).append({
            "id": "q" + uuid.uuid4().hex[:8],
            "title": str(draft.get("q_type") or "AI 改编题").strip() or "AI 改编题",
            "score": score, "percent": 0, "intro": stem, "images": [], "tables": [],
            "answer": str(draft.get("answer") or "").strip(), "answer_images": [],
            "subs": [], "source": src, "status": "accepted"})
        no = len(doc["items"])
    else:
        target = None
        want = str(body.get("item_id") or "")
        for it in doc.get("items") or []:
            if str(it.get("id")) == want:
                target = it
                break
        if target is None:
            target = doc["items"][-1]
        target.setdefault("subs", []).append(sub)
        no = f"{doc['items'].index(target) + 1}-{len(target['subs'])}"
    apply_doc(db, paper, doc)
    db.commit()
    db.refresh(paper)
    fresh = doc_from_paper(paper)
    return {"id": paper.id, "doc": fresh, "issues": exam_render.check_issues(fresh),
            "added": {"mode": mode, "no": no}}


# ---------------------------------------------------------------- 预览 / 校验

@router.post("/preview")
def preview(body: dict, request: Request):
    doc = body.get("doc") if isinstance(body.get("doc"), dict) else body
    if not isinstance(doc, dict):
        raise HTTPException(400, "缺少 doc")
    part = str(body.get("part") or "questions")
    token = exam_render.cache_put(exam_render.build_doc_html(doc, part))
    return {"token": token, "url": f"/api/exam/html/{token}",
            "issues": exam_render.check_issues(doc),
            "pages_hint": len(doc.get("items") or [])}


@router.post("/check")
def check(body: dict):
    doc = body.get("doc") if isinstance(body.get("doc"), dict) else body
    return {"issues": exam_render.check_issues(doc or {})}


@router.get("/html/{token}", response_class=HTMLResponse)
def get_html(token: str):
    doc = exam_render.cache_get(token)
    if doc is None:
        raise HTTPException(404, "预览已过期，请重新生成")
    return HTMLResponse(doc, headers={"Cache-Control": "no-store"})


@router.get("/papers/{paper_id}/view", response_class=HTMLResponse)
def view_paper(paper_id: int, part: str = "questions", db: Session = Depends(get_db)):
    paper = _paper_or_404(db, paper_id)
    doc = doc_from_paper(paper)
    return HTMLResponse(exam_render.build_doc_html(doc, part))


@router.get("/papers/{paper_id}/export")
def export_paper(paper_id: int, part: str = "questions", request: Request = None,
                 db: Session = Depends(get_db)):
    paper = _paper_or_404(db, paper_id)
    doc = doc_from_paper(paper)
    base = str(request.base_url).rstrip("/") if request is not None else None
    try:
        pdf = exam_render.export_pdf(doc, part, base)
    except RuntimeError as e:
        raise HTTPException(501, str(e))
    label = PART_LABEL.get(part, "")
    filename = f"{paper.title}_{label}.pdf"
    # 导出成功即视为「出完了」：草稿自动转正式卷，进试卷列表（可再打回草稿）
    if paper_status(paper) != "done":
        _set_status(db, paper, "done")
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


# ---------------------------------------------------------------- 单题 PDF（结构化题）

@router.get("/question/{question_id}/export")
def export_question(question_id: int, part: str = "questions", request: Request = None,
                    db: Session = Depends(get_db)):
    """单题 PDF（题目卷 / 答案卷）。

    结构化题和画布题都走 paper_doc 统一转换：画布题的整页图按正文宽度缩放后贴进 A4，
    不再像以前那样直接 400 让前端绕「建临时卷 → v2 导出」那条老路。
    """
    from ..models import Question
    from ..services import paper_doc

    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(404, "题目不存在")
    lay = dict(q.layout or {})
    meta = dict(lay.get("meta") or {})
    score = meta.get("score") or 10
    doc = paper_doc.build_doc_from_question(q, score=score)
    base = str(request.base_url).rstrip("/") if request is not None else None
    try:
        pdf = exam_render.export_pdf(doc, part, base)
    except RuntimeError as e:
        raise HTTPException(501, str(e))
    name = f"{doc.get('title') or '试题'}_{PART_LABEL.get(part, '')}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
