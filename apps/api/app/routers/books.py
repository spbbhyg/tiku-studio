from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Book, Question, Unit
from ..schemas import BookDetailOut, BookOut, UnitOut

router = APIRouter(prefix="/api/books", tags=["books"])


@router.get("", response_model=list[BookOut])
def list_books(role: str | None = None, subject: str | None = None,
               grade: int | None = None, semester: str | None = None,
               db: Session = Depends(get_db)):
    stmt = select(Book)
    if role:
        stmt = stmt.where(Book.role == role)
    if subject:
        stmt = stmt.where(Book.subject == subject)
    if grade is not None:
        stmt = stmt.where(Book.grade == grade)
    if semester:
        stmt = stmt.where(Book.semester == semester)
    books = db.scalars(stmt.order_by(Book.subject, Book.grade, Book.id)).all()
    return books


@router.get("/textbooks", response_model=list[BookOut])
def list_textbooks(subject: str | None = None, grade: int | None = None,
                   semester: str | None = None, db: Session = Depends(get_db)):
    """纲清单：组卷与对纲校验的基准课本。"""
    stmt = select(Book).where(Book.role == "textbook")
    if subject:
        stmt = stmt.where(Book.subject == subject)
    if grade is not None:
        stmt = stmt.where(Book.grade == grade)
    if semester:
        stmt = stmt.where(Book.semester == semester)
    return db.scalars(stmt.order_by(Book.subject, Book.grade)).all()


@router.delete("/{book_id}")
def delete_book(book_id: int, db: Session = Depends(get_db)):
    """删除误导入的书册（连同其题目/单元）。课本纲被组卷引用时拒绝删除。"""
    from ..models import Paper

    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(404, "book 不存在")
    if book.role == "textbook":
        used = db.scalar(select(func.count()).select_from(Paper).where(Paper.textbook_id == book_id))
        if used:
            raise HTTPException(400, "该课本纲已被组卷使用，请先删除相关试卷")
    db.delete(book)
    db.commit()
    return {"ok": True}


@router.get("/{book_id}", response_model=BookDetailOut)
def get_book(book_id: int, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(404, "book 不存在")
    counts: dict = {}
    if book.units:
        counts = dict(db.execute(
            select(Question.unit_id, func.count())
            .where(Question.unit_id.in_([u.id for u in book.units]))
            .group_by(Question.unit_id)).all())
    detail = BookDetailOut.model_validate(book)
    detail.units = [
        UnitOut(
            id=u.id, seq=u.seq, name=u.name, description=u.description,
            knowledge_points=u.knowledge_points or [],
            lessons=sorted(u.lessons, key=lambda l: l.seq),
            question_count=counts.get(u.id, 0),
        )
        for u in sorted(book.units, key=lambda x: x.seq)
    ]
    return detail
