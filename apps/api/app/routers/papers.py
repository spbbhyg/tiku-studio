from urllib.parse import quote

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Paper, PaperQuestion
from ..schemas import AiPaperBuildIn, AiPaperIn, MergePapersIn, PaperBrief, PaperGenerate, PaperOut
from ..services import ai
from ..services import paper_builder
from ..services import pdf_render

router = APIRouter(prefix="/api/papers", tags=["papers"])


def _snapshot_item(q: dict, score: float | None = None) -> dict:
    return {
        "stem": q.get("stem", ""),
        "options": q.get("options") or [],
        "answer": q.get("answer", ""),
        "analysis": q.get("analysis", ""),
        "q_type": q.get("q_type") or "综合",
        "difficulty": q.get("difficulty") or 3,
        "knowledge_points": q.get("knowledge_points") or [],
        "images": [],
        "source_book_title": q.get("source_book_title") or "AI 生成",
        "unit_name": "",
        "_score": score,
    }


def _create_paper(db: Session, *, title: str, items: list[tuple[dict, float]], config: dict,
                  subject: str = "化学", grade: int = 12, semester: str = "全",
                  duration_minutes: int = 90) -> Paper:
    clean = []
    for snap, score in items:
        s = round(float(score or 0), 1)
        if s <= 0:
            s = 10.0
        clean.append((snap, s))
    total = round(sum(s for _snap, s in clean), 1) or 100
    paper = Paper(title=title[:200], subject=subject or "化学", grade=int(grade or 12),
                  semester=semester or "全", total_score=total,
                  duration_minutes=int(duration_minutes or 90), include_extension=False,
                  config=config)
    db.add(paper)
    db.flush()
    for i, (snap, score) in enumerate(clean, 1):
        db.add(PaperQuestion(paper_id=paper.id, question_id=None, seq=i, score=score,
                             snapshot={k: v for k, v in snap.items() if k != "_score"}))
    db.flush()
    return paper


def _shift_number(text: str, offset: int) -> str:
    """把大题号整体后移：1-2 → 2-2、第1题 → 第2题（只动大题号，不动小问后缀）。"""
    if not offset or not text:
        return text

    def lead(m):
        return f"{m.group(1)}{int(m.group(2)) + offset}"

    out = re.sub(r"(?m)^(\s*)(\d+)(?=\s*[-－—.．、])", lead, text)
    out = re.sub(r"第(\d+)题", lambda m: f"第{int(m.group(1)) + offset}题", out)
    return out



@router.post("", response_model=PaperOut)
def generate(cfg: PaperGenerate, db: Session = Depends(get_db)):
    try:
        paper = paper_builder.generate_paper(db, cfg)
    except paper_builder.NotEnoughQuestions as e:
        raise HTTPException(422, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    db.refresh(paper)
    return paper


@router.post("/ai", response_model=PaperOut)
def generate_ai(body: AiPaperIn, db: Session = Depends(get_db)):
    """AI 按素材/文献直接命题，结果只进试卷，不写题库。"""
    source = (body.source or "").strip()
    doc_title = ""
    if not source and body.doc_id:
        source = ai.pdf_to_text(ai._doc_file(body.doc_id))
        doc_title = "文献"
    if not source:
        raise HTTPException(400, "请提供命题素材（题干文字或选择一份 PDF）")
    try:
        gen = ai.generate_questions(db, source, body.count, body.q_type, body.difficulty)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"AI 命题失败：{exc}") from exc
    items = [(_snapshot_item(q, q.get("score")), float(q.get("score") or 10)) for q in gen["questions"]]
    title = (body.title or "").strip() or f"AI 命题 · {doc_title or '素材'} · {len(items)} 题"
    paper = _create_paper(db, title=title, items=items,
                          config={"source": "ai", "count": len(items), "model": gen.get("model")},
                          subject=body.subject, grade=body.grade, semester=body.semester,
                          duration_minutes=body.duration_minutes)
    db.commit()
    db.refresh(paper)
    return paper


@router.post("/ai/preview")
def preview_ai(body: AiPaperIn, db: Session = Depends(get_db)) -> dict:
    """分步出题第一步：只生成题目快照，不建卷、不进题库。"""
    source = (body.source or "").strip()
    if not source and body.doc_id:
        source = ai.pdf_to_text(ai._doc_file(body.doc_id))
    if not source:
        raise HTTPException(400, "请提供命题素材（题干文字或选择一份 PDF）")
    try:
        gen = ai.generate_questions(db, source, body.count, body.q_type, body.difficulty)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"AI 命题失败：{exc}") from exc
    items = []
    for q in gen["questions"]:
        score = float(q.get("score") or 10)
        items.append({"snapshot": _snapshot_item(q, score), "score": score})
    title = (body.title or "").strip() or f"AI 命题 · {len(items)} 题"
    return {"ok": True, "title": title, "items": items, "model": gen.get("model"),
            "usage": gen.get("usage")}


@router.post("/ai/build", response_model=PaperOut)
def build_ai(body: AiPaperBuildIn, db: Session = Depends(get_db)):
    """分步出题最后一步：把逐题（可人工改过）的快照建成一套试卷。"""
    pairs: list[tuple[dict, float]] = []
    for raw in body.items:
        snap = raw.get("snapshot") if isinstance(raw.get("snapshot"), dict) else raw
        if not isinstance(snap, dict) or not str(snap.get("stem") or "").strip():
            continue
        pairs.append((snap, float(raw.get("score") or snap.get("score") or 10)))
    if not pairs:
        raise HTTPException(400, "没有可成卷的题目")
    title = (body.title or "").strip() or f"AI 命题 · {len(pairs)} 题"
    paper = _create_paper(db, title=title, items=pairs,
                          config={"source": "ai", "count": len(pairs)},
                          subject=body.subject, grade=body.grade, semester=body.semester,
                          duration_minutes=body.duration_minutes)
    db.commit()
    db.refresh(paper)
    return paper


@router.post("/merge", response_model=PaperOut)
def merge(body: MergePapersIn, db: Session = Depends(get_db)):
    """把多套试卷合成一套，并自动顺延大题号（小问号不变）。"""
    papers = []
    for pid in body.paper_ids:
        p = db.get(Paper, pid)
        if p is None:
            raise HTTPException(404, f"试卷 #{pid} 不存在")
        papers.append(p)
    items: list[tuple[dict, float]] = []
    offset = 0
    for p in papers:
        for it in p.items:
            snap = dict(it.snapshot or {})
            if offset:
                for key in ("stem", "answer", "analysis"):
                    snap[key] = _shift_number(snap.get(key) or "", offset)
            items.append((snap, it.score))
        offset += max(1, len(p.items))
    title = (body.title or "").strip() or " + ".join(p.title for p in papers) + "（融合卷）"
    first = papers[0]
    paper = _create_paper(db, title=title, items=items,
                          config={"source": "merged", "from": [p.id for p in papers]},
                          subject=first.subject, grade=first.grade, semester=first.semester,
                          duration_minutes=max(p.duration_minutes or 90 for p in papers))
    db.commit()
    db.refresh(paper)
    return paper


@router.get("", response_model=list[PaperBrief])
def list_papers(db: Session = Depends(get_db)):
    papers = db.scalars(select(Paper).order_by(Paper.id.desc())).all()
    out = []
    for p in papers:
        n = db.scalar(select(func.count()).select_from(PaperQuestion).where(PaperQuestion.paper_id == p.id))
        out.append(PaperBrief(
            id=p.id, title=p.title, subject=p.subject, grade=p.grade, semester=p.semester,
            total_score=p.total_score, duration_minutes=p.duration_minutes,
            question_count=n, created_at=p.created_at))
    return out


@router.get("/{paper_id}", response_model=PaperOut)
def get_paper(paper_id: int, db: Session = Depends(get_db)):
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    return paper


@router.patch("/{paper_id}", response_model=PaperOut)
def rename_paper(paper_id: int, body: dict, db: Session = Depends(get_db)):
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    if body.get("title"):
        paper.title = body["title"]
    db.commit()
    db.refresh(paper)
    return paper


@router.delete("/{paper_id}")
def delete_paper(paper_id: int, db: Session = Depends(get_db)):
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    db.delete(paper)
    db.commit()
    return {"ok": True}


@router.post("/{paper_id}/replace", response_model=PaperOut)
def replace_question(paper_id: int, body: dict, db: Session = Depends(get_db)):
    pq_id = body.get("paper_question_id")
    if not pq_id:
        raise HTTPException(400, "缺少 paper_question_id")
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    try:
        new_item = paper_builder.replace_question(db, paper, pq_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    if new_item is None:
        raise HTTPException(422, "该题型已没有可替换的新题，请缩小范围或先补题库")
    db.refresh(paper)
    return paper


@router.patch("/{paper_id}/items", response_model=PaperOut)
def update_paper_items(paper_id: int, body: dict, db: Session = Depends(get_db)):
    """打印视图里直接改标题/题干/答案后保存。"""
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    title = str(body.get("title") or "").strip()
    if title:
        paper.title = title[:200]
    by_id = {it.id: it for it in paper.items}
    for raw in body.get("items") or []:
        try:
            pq = by_id.get(int(raw.get("id")))
        except (TypeError, ValueError):
            continue
        if pq is None:
            continue
        snap = dict(pq.snapshot or {})
        for key in ("stem", "answer", "analysis"):
            if key in raw and isinstance(raw[key], str):
                snap[key] = raw[key]
        pq.snapshot = snap
    db.commit()
    db.refresh(paper)
    return paper


@router.get("/{paper_id}/view", response_class=HTMLResponse)
def view_paper(paper_id: int, part: str = "all", db: Session = Depends(get_db)):
    """网页打印视图：可在页面上直接改题干/答案，再打印或另存 PDF。"""
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    if part not in ("all", "questions", "answers"):
        part = "all"
    return HTMLResponse(pdf_render.render_paper_html(db, paper, part))


@router.get("/{paper_id}/export")
def export_paper(paper_id: int, part: str = "all", db: Session = Depends(get_db)):
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    if part not in ("all", "questions", "answers"):
        part = "all"
    try:
        pdf = pdf_render.export_pdf(db, paper, part)
    except RuntimeError as e:
        raise HTTPException(501, str(e))
    suffix = {"questions": "_题目卷", "answers": "_答案卷"}.get(part, "")
    filename = paper.title.replace(" ", "_") + suffix + ".pdf"
    return Response(
        pdf, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})
