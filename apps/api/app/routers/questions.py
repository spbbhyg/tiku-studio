from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Book, Question, Unit
from ..schemas import QuestionOut, QuestionPatch

router = APIRouter(prefix="/api/questions", tags=["questions"])


def _serialize(q: Question) -> QuestionOut:
    out = QuestionOut.model_validate(q)
    out.book_title = q.book.title if q.book else ""
    if q.unit:
        out.unit_name = q.unit.name
        out.unit_seq = q.unit.seq
    return out


@router.get("")
def list_questions(
    db: Session = Depends(get_db),
    book_id: int | None = None,
    unit_id: int | None = None,
    textbook_id: int | None = None,
    subject: str | None = None,
    grade: int | None = None,
    semester: str | None = None,
    q_type: str | None = None,
    difficulty: int | None = None,
    kp: str | None = None,
    scope_status: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    stmt = select(Question).join(Book, Question.book_id == Book.id)
    count_stmt = select(func.count()).select_from(Question).join(Book, Question.book_id == Book.id)
    conds = []
    if book_id:
        conds.append(Question.book_id == book_id)
    if unit_id:
        conds.append(Question.unit_id == unit_id)
    if textbook_id:
        conds.append(Question.source_textbook_id == textbook_id)
    if subject:
        conds.append(Book.subject == subject)
    if grade is not None:
        conds.append(Book.grade == grade)
    if semester:
        conds.append(Book.semester == semester)
    if q_type:
        conds.append(Question.q_type == q_type)
    if difficulty:
        conds.append(Question.difficulty == difficulty)
    if scope_status:
        conds.append(Question.scope_status == scope_status)
    if status:
        conds.append(Question.status == status)
    if tag:  # 标签在 JSON 数组里，用 json_each 精确匹配
        conds.append(text(
            "EXISTS (SELECT 1 FROM json_each(questions.tags) WHERE json_each.value = :tag)"
        ).bindparams(tag=tag))
    if q:
        like = f"%{q}%"
        conds.append(Question.stem.like(like) | Question.answer.like(like))
    if conds:
        stmt = stmt.where(*conds)
        count_stmt = count_stmt.where(*conds)

    if kp:  # JSON 数组包含：先粗筛再精确过滤
        ids = {r[0] for r in db.execute(text(
            "SELECT q.id FROM questions q, json_each(q.knowledge_points) je WHERE je.value = :kp"),
            {"kp": kp}).all()}
        if not ids:
            return {"items": [], "total": 0}
        stmt = stmt.where(Question.id.in_(ids))
        count_stmt = select(func.count()).select_from(Question).where(Question.id.in_(ids))

    total = db.scalar(count_stmt)
    rows = db.scalars(
        stmt.order_by(Question.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"items": [_serialize(r) for r in rows], "total": total}


@router.get("/knowledge-points")
def knowledge_points(textbook_id: int, db: Session = Depends(get_db)):
    """某本教材纲的全部知识点（附单元信息），供筛选与组卷用。"""
    textbook = db.get(Book, textbook_id)
    if textbook is None:
        raise HTTPException(404, "textbook 不存在")
    out = []
    for u in sorted(textbook.units, key=lambda x: x.seq):
        for kp in u.knowledge_points or []:
            out.append({"name": kp, "unit_id": u.id, "unit_seq": u.seq, "unit_name": u.name})
    return out


@router.get("/type-stats")
def type_stats(textbook_id: int, db: Session = Depends(get_db)):
    """各题型已确认题量（与该纲同学科/年级/学期、非疑似超纲）：组卷页据此提示可用量。"""
    textbook = db.get(Book, textbook_id)
    if textbook is None:
        raise HTTPException(404, "textbook 不存在")
    rows = db.execute(
        select(Question.q_type, func.count())
        .join(Book, Question.book_id == Book.id)
        .where(Question.status == "confirmed",
               Question.scope_status != "suspect",
               Book.subject == textbook.subject,
               Book.grade == textbook.grade,
               Book.semester == textbook.semester)
        .group_by(Question.q_type)).all()
    return {t: c for t, c in rows}


@router.get("/{question_id}", response_model=QuestionOut)
def get_question(question_id: int, db: Session = Depends(get_db)):
    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(404, "题目不存在")
    return _serialize(q)


@router.patch("/{question_id}", response_model=QuestionOut)
def patch_question(question_id: int, patch: QuestionPatch, db: Session = Depends(get_db)):
    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(404, "题目不存在")
    data = patch.model_dump(exclude_unset=True)
    if data.get("unit_id") is not None:
        unit = db.get(Unit, data["unit_id"])
        if unit is None:
            raise HTTPException(400, "目标单元不存在")
    for k, v in data.items():
        setattr(q, k, v)
    db.commit()
    db.refresh(q)
    return _serialize(q)


@router.delete("/{question_id}")
def delete_question(question_id: int, db: Session = Depends(get_db)):
    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(404, "题目不存在")
    db.delete(q)
    db.commit()
    return {"ok": True}
