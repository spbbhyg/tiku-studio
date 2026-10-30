from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Paper, PaperQuestion
from ..schemas import PaperBrief, PaperGenerate, PaperOut
from ..services import paper_builder
from ..services import pdf_render

router = APIRouter(prefix="/api/papers", tags=["papers"])


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


@router.get("/{paper_id}/view", response_class=HTMLResponse)
def view_paper(paper_id: int, db: Session = Depends(get_db)):
    """网页打印视图：浏览器打开后可直接 Ctrl+P 另存 PDF（无需 Playwright）。"""
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    return HTMLResponse(pdf_render.render_paper_html(db, paper))


@router.get("/{paper_id}/export")
def export_paper(paper_id: int, db: Session = Depends(get_db)):
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    try:
        pdf = pdf_render.export_pdf(db, paper)
    except RuntimeError as e:
        raise HTTPException(501, str(e))
    filename = paper.title.replace(" ", "_") + ".pdf"
    return Response(
        pdf, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})
