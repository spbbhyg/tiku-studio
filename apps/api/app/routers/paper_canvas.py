"""试卷画布：把整张卷子当画布编排（与出题画布同一套渲染器）。

- POST /api/papers/canvas      新建/更新一张「画布试卷」
- GET  /api/papers/{id}/canvas 读回版面，供继续编辑
- GET  /api/papers/{id}/export?fmt=pdf  画布试卷导出（按 842pt 切页）
"""
from __future__ import annotations

from urllib.parse import quote

import pymupdf
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import LIBRARY_DIR
from ..db import get_db
from ..models import Paper, PaperQuestion, Question
from ..services import compose_render

router = APIRouter(prefix="/api/papers", tags=["paper-canvas"])

PAGE_H = 842.0


class CanvasIn(BaseModel):
    paper_id: int | None = None
    title: str = "试卷草稿"
    duration_minutes: int = 90
    total_score: float = 0
    layout: dict
    question_ids: list[int] = []


def _render_image(layout: dict) -> dict:
    return compose_render.render_layout(layout, dpi=200)


@router.post("/canvas")
def save_canvas(body: CanvasIn, db: Session = Depends(get_db)):
    blocks = (body.layout or {}).get("blocks") or []
    if not blocks:
        raise HTTPException(400, "画布是空的：先插入题目图或写点文字")
    if body.layout.get("height"):
        body.layout["height"] = min(float(body.layout["height"]), PAGE_H * 6)

    info = _render_image(body.layout)

    paper = db.get(Paper, body.paper_id) if body.paper_id else None
    if paper is None:
        paper = Paper(title=body.title or "试卷草稿", subject="综合", grade=0, semester="")
        db.add(paper)
        db.flush()

    cfg = dict(paper.config or {})
    cfg.update({"source": "canvas", "layout": body.layout,
                "image": info["path"], "width": info["width"], "height": info["height"],
                "height_pt": float(body.layout.get("height") or PAGE_H),
                "trim": bool(body.layout.get("trim")), "trim_pad": body.layout.get("trim_pad")})
    paper.config = cfg
    paper.title = body.title or paper.title
    paper.duration_minutes = body.duration_minutes
    paper.total_score = body.total_score or paper.total_score

    # 记录用到的题目（用于题数统计），保留顺序
    ids = [i for i in (body.question_ids or [])]
    if ids:
        db.query(PaperQuestion).filter(PaperQuestion.paper_id == paper.id).delete()
        rows = {q.id: q for q in db.query(Question).filter(Question.id.in_(ids)).all()}
        for seq, qid in enumerate(ids, start=1):
            q = rows.get(qid)
            if not q:
                continue
            db.add(PaperQuestion(paper_id=paper.id, question_id=q.id, seq=seq, score=0,
                                 snapshot={"stem": q.stem, "q_type": q.q_type,
                                           "images": q.images or []}))
    db.commit()
    db.refresh(paper)
    return {"id": paper.id, "created": body.paper_id is None, "image": info["path"],
            "width": info["width"], "height": info["height"]}


@router.get("/{paper_id}/canvas")
def get_canvas(paper_id: int, db: Session = Depends(get_db)):
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    cfg = paper.config or {}
    qids = [pq.question_id for pq in sorted(paper.items, key=lambda x: x.seq) if pq.question_id]
    return {"id": paper.id, "title": paper.title,
            "duration_minutes": paper.duration_minutes,
            "total_score": paper.total_score,
            "layout": cfg.get("layout") or {}, "question_ids": qids,
            "is_canvas": cfg.get("source") == "canvas"}


@router.get("/{paper_id}/export-canvas")
def export_canvas(paper_id: int, db: Session = Depends(get_db)):
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    cfg = paper.config or {}
    rel = cfg.get("image")
    if not rel:
        raise HTTPException(400, "这张试卷还没有画布版面，先在「画布编排」里保存一次")
    src = (LIBRARY_DIR / rel).resolve()
    if not src.is_file():
        raise HTTPException(404, "整卷图片丢了，重新保存一次")

    from io import BytesIO

    from PIL import Image

    im = Image.open(src).convert("RGB")
    px_per_pt = im.width / 595.0          # 版面宽固定 595pt，直接按像素宽反推
    height_pt = float(cfg.get("height_pt") or (im.height / px_per_pt))
    page_px = max(1, int(round(PAGE_H * px_per_pt)))

    doc = pymupdf.open()
    top = 0
    while top < im.height:
        chunk = im.crop((0, top, im.width, min(im.height, top + page_px)))
        buf = BytesIO()
        chunk.save(buf, format="PNG")
        page = doc.new_page(width=595, height=PAGE_H)
        h_pt = chunk.height / px_per_pt
        page.insert_image(pymupdf.Rect(0, 0, 595, min(h_pt, PAGE_H)),
                          stream=buf.getvalue(), keep_proportion=True)
        top += page_px

    out = BytesIO()
    doc.save(out, garbage=0, deflate=True)
    doc.close()
    data = out.getvalue()
    filename = (paper.title or "试卷") + ".pdf"
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})
