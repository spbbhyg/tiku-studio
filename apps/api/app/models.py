from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Book(Base):
    """一册书：课本（纲）、教辅、试卷集或自编卷。"""
    __tablename__ = "books"
    __table_args__ = (
        UniqueConstraint("title", "publisher", "subject", "grade", "semester", "edition_year", name="uq_book"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(16))          # textbook | workbook | paper_set | self_made
    subject: Mapped[str] = mapped_column(String(16))
    grade: Mapped[int] = mapped_column(Integer)
    semester: Mapped[str] = mapped_column(String(8))
    publisher: Mapped[str] = mapped_column(String(32))
    edition_year: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    source_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    units: Mapped[list["Unit"]] = relationship(back_populates="book", cascade="all, delete-orphan")
    questions: Mapped[list["Question"]] = relationship(
        back_populates="book", cascade="all, delete-orphan",
        foreign_keys="Question.book_id")


class Unit(Base):
    """单元/章。课本单元的 knowledge_points 构成『纲』。"""
    __tablename__ = "units"
    __table_args__ = (UniqueConstraint("book_id", "seq", name="uq_unit"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    knowledge_points: Mapped[list] = mapped_column(JSON, default=list)

    book: Mapped[Book] = relationship(back_populates="units")
    lessons: Mapped[list["Lesson"]] = relationship(back_populates="unit", cascade="all, delete-orphan")


class Lesson(Base):
    __tablename__ = "lessons"
    __table_args__ = (UniqueConstraint("unit_id", "seq", name="uq_lesson"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    pages: Mapped[str | None] = mapped_column(String(64), nullable=True)

    unit: Mapped[Unit] = relationship(back_populates="lessons")


class PageAsset(Base):
    """文件级索引：扫描页/试卷页/课本页。"""
    __tablename__ = "page_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.id", ondelete="SET NULL"), nullable=True)
    lesson_id: Mapped[int | None] = mapped_column(ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True)
    path: Mapped[str] = mapped_column(Text)                # 相对 data/library 的路径
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="scan")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Question(Base):
    """一道题。scope_status 是对纲校验结论：ok=范围内 extension=提优拓展 suspect=疑似超纲/版本不符 pending=待定"""
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("book_id", "content_hash", name="uq_question"),
        Index("ix_questions_status", "status"),
        Index("ix_questions_scope", "scope_status"),
        Index("ix_questions_type", "q_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    source_textbook_id: Mapped[int | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"), nullable=True)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.id", ondelete="SET NULL"), nullable=True)

    content_hash: Mapped[str] = mapped_column(String(32))
    stem: Mapped[str] = mapped_column(Text)
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    answer: Mapped[str] = mapped_column(Text)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    q_type: Mapped[str] = mapped_column(String(16))
    difficulty: Mapped[int] = mapped_column(Integer, default=2)   # 1基础 2中等 3提优
    knowledge_points: Mapped[list] = mapped_column(JSON, default=list)
    images: Mapped[list] = mapped_column(JSON, default=list)      # [{path, caption?}]
    source_page: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 文献出处：source_title=文献/教材名，source_citation=作者·期刊·年份
    source_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_citation: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    # 大题版面（出题页画布的 JSON 快照），便于再次打开继续排版
    layout: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    scope_status: Mapped[str] = mapped_column(String(16), default="pending")
    scope_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | confirmed | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    book: Mapped[Book] = relationship(back_populates="questions", foreign_keys=[book_id])
    unit: Mapped[Unit | None] = relationship()


class Paper(Base):
    """一次组卷的记录与配置。"""
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    textbook_id: Mapped[int | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"), nullable=True)
    subject: Mapped[str] = mapped_column(String(16))
    grade: Mapped[int] = mapped_column(Integer)
    semester: Mapped[str] = mapped_column(String(8))
    unit_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_ids: Mapped[list] = mapped_column(JSON, default=list)
    total_score: Mapped[float] = mapped_column(Float, default=100)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    include_extension: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    items: Mapped[list["PaperQuestion"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", order_by="PaperQuestion.seq"
    )


class PaperQuestion(Base):
    """卷中题。snapshot 固化题面，题库后续修改不影响已出试卷。"""
    __tablename__ = "paper_questions"
    __table_args__ = (UniqueConstraint("paper_id", "seq", name="uq_paper_question"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    question_id: Mapped[int | None] = mapped_column(ForeignKey("questions.id", ondelete="SET NULL"), nullable=True)
    seq: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)

    paper: Mapped[Paper] = relationship(back_populates="items")


class Setting(Base):
    """键值设置（LLM 供应商、key 等）。"""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class AuthoringTag(Base):
    """标签库：默认灌入 Chemy 的九大专题，用户可增删改。"""
    __tablename__ = "authoring_tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    group: Mapped[str] = mapped_column(String(40), default="专题")
    color: Mapped[str] = mapped_column(String(16), default="")
    seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

