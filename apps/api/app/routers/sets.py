"""A：整卷 / 届次管理（题库的「试卷」维度）。"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Question, QuestionSet
from ..services.versions import take_snapshot

router = APIRouter(prefix="/api/sets", tags=["sets"])


class SetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    year: Optional[int] = None
    source: Optional[str] = None
    note: Optional[str] = None


class SetPatch(BaseModel):
    name: Optional[str] = None
    year: Optional[int] = None
    source: Optional[str] = None
    note: Optional[str] = None


class AssignIn(BaseModel):
    question_ids: list[int] = Field(default_factory=list)


def _brief(db: Session, s: QuestionSet) -> dict:
    n = db.scalar(select(func.count()).select_from(Question).where(Question.set_id == s.id)) or 0
    return {"id": s.id, "name": s.name, "year": s.year, "source": s.source,
            "note": s.note, "count": n,
            "created_at": s.created_at.isoformat() if s.created_at else None}


@router.get("")
def list_sets(db: Session = Depends(get_db)):
    rows = db.scalars(select(QuestionSet).order_by(QuestionSet.year.desc().nullslast(),
                                                  QuestionSet.id.desc())).all()
    loose = db.scalar(select(func.count()).select_from(Question)
                      .where(Question.set_id.is_(None))) or 0
    return {"items": [_brief(db, s) for s in rows], "unfiled": loose}


@router.post("")
def create_set(body: SetIn, db: Session = Depends(get_db)):
    exists = db.scalar(select(QuestionSet).where(QuestionSet.name == body.name.strip()))
    if exists is not None:
        raise HTTPException(400, f"整卷「{body.name}」已存在")
    s = QuestionSet(name=body.name.strip(), year=body.year,
                    source=body.source, note=body.note)
    db.add(s)
    db.commit()
    db.refresh(s)
    return _brief(db, s)


@router.patch("/{set_id}")
def patch_set(set_id: int, body: SetPatch, db: Session = Depends(get_db)):
    s = db.get(QuestionSet, set_id)
    if s is None:
        raise HTTPException(404, "整卷不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return _brief(db, s)


@router.delete("/{set_id}")
def delete_set(set_id: int, db: Session = Depends(get_db)):
    s = db.get(QuestionSet, set_id)
    if s is None:
        raise HTTPException(404, "整卷不存在")
    for q in list(s.questions):
        q.set_id = None
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.get("/{set_id}/question-ids")
def set_question_ids(set_id: int, db: Session = Depends(get_db)):
    s = db.get(QuestionSet, set_id)
    if s is None:
        raise HTTPException(404, "整卷不存在")
    ids = [q.id for q in db.scalars(
        select(Question).where(Question.set_id == set_id).order_by(Question.id)).all()]
    return {"id": set_id, "name": s.name, "question_ids": ids}


@router.post("/{set_id}/assign")
def assign(set_id: int, body: AssignIn, db: Session = Depends(get_db)):
    s = db.get(QuestionSet, set_id)
    if s is None:
        raise HTTPException(404, "整卷不存在")
    changed = 0
    for qid in body.question_ids:
        q = db.get(Question, qid)
        if q is None:
            continue
        take_snapshot(db, q, note=f"归入整卷「{s.name}」前")
        q.set_id = set_id
        changed += 1
    db.commit()
    return {"ok": True, "assigned": changed, "set": _brief(db, s)}


@router.post("/detach")
def detach(body: AssignIn, db: Session = Depends(get_db)):
    changed = 0
    for qid in body.question_ids:
        q = db.get(Question, qid)
        if q is None or q.set_id is None:
            continue
        take_snapshot(db, q, note="移出整卷前")
        q.set_id = None
        changed += 1
    db.commit()
    return {"ok": True, "detached": changed}
