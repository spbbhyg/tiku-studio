"""工作台相关表：分类维度、错题集、导出日志。

与 models.py / models_v2.py 分开，避免动到老表结构。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class CategoryDim(Base):
    """一个分类维度，比如「专题」「章节」「来源」。key 稳定，name 可改。"""
    __tablename__ = "wb_category_dims"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CategoryValue(Base):
    """维度下的取值，题库侧栏按它列条目。"""
    __tablename__ = "wb_category_values"
    __table_args__ = (UniqueConstraint("dim_key", "name", name="uq_wb_category_value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dim_key: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(80))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WrongBook(Base):
    """错题集：用户可以建多个、自己命名。"""
    __tablename__ = "wb_wrongbooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WrongItem(Base):
    __tablename__ = "wb_wrong_items"
    __table_args__ = (UniqueConstraint("book_id", "question_id", name="uq_wb_wrong_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("wb_wrongbooks.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    wrong_count: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ExportLog(Base):
    """导出日志：哪一版题目、生成了哪一版文件。"""
    __tablename__ = "wb_export_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(16), default="pdf")   # pdf | html | md | docx
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    total_score: Mapped[float] = mapped_column(Float, default=0)
    versions: Mapped[list] = mapped_column(JSON, default=list)     # [{qid,title,version,ver_id,updated_at}]
    fingerprint: Mapped[str] = mapped_column(String(32), default="")  # 内容指纹（题目版本集合）
    file_name: Mapped[str] = mapped_column(String(200), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    """键值设置（助手人格、AI 说明折叠状态等）。"""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
