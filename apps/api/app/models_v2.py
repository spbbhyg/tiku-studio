"""v2 数据模型：题组/小问 + 化学对象 + 素材库 + 版本 + 组卷篮。

设计要点（对齐用户 8 条要求）
- 小问（SubQuestion）是一等实体：题干、答案、评分细则、分值、标签、难度、题型都在它身上；
  题组（QuestionGroup）只承担“大题公共题干/来源/共用素材/状态”的角色。
- 题干/答案/细则统一存 Tiptap JSON（blocks），化学内容不塞进 JSON：只放 {"type":"chem","id":N} 引用，
  真正的 LaTeX / MOL / SMILES / 渲染图 存在 ChemObject 里，方便复用、统一 Clean Up。
- 素材（Material）一等实体：题目用 material_id 引用，避免重复上传。
- 状态机 draft → review → approved → published 落在 status 字段，任何状态都允许草稿残缺保存。
- 版本快照按（实体类型, 实体 ID）存，回滚/对比用。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .models import utcnow

STATUS_FLOW = ("draft", "review", "approved", "published")


class ChemObject(Base):
    """化学对象：公式（LaTeX）或结构式（MOL/SMILES）+ 渲染结果。"""
    __tablename__ = "chem_objects"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))                 # formula | structure | reaction
    latex: Mapped[str | None] = mapped_column(Text, nullable=True)
    smiles: Mapped[str | None] = mapped_column(Text, nullable=True)
    molfile: Mapped[str | None] = mapped_column(Text, nullable=True)
    svg: Mapped[str | None] = mapped_column(Text, nullable=True)   # 渲染结果（内联 SVG 源码）
    svg_path: Mapped[str | None] = mapped_column(String(300), nullable=True)  # 或落盘的 png/svg
    renderer: Mapped[str | None] = mapped_column(String(32), nullable=True)   # ketcher | marvin | katex | rdkit
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # Clean Up 时间
    hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Material(Base):
    """素材（图/PDF 页/表格/数据文件）：题库与素材库共用，题目按 ID 引用。"""
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="image")  # image | pdf_page | table | file
    path: Mapped[str] = mapped_column(String(400))                  # 相对 data/library
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    caption: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sha1: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class QuestionGroup(Base):
    """题组 / 大题：承载公共题干、来源、共用素材、状态与标签。"""
    __tablename__ = "question_groups"
    __table_args__ = (Index("ix_qg_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int | None] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)     # 大题标题，如“一、有机合成”
    stem: Mapped[dict] = mapped_column(JSON, default=dict)                    # Tiptap doc：大题公共题干
    source_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_citation: Mapped[str | None] = mapped_column(Text, nullable=True)
    locator: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    material_ids: Mapped[list] = mapped_column(JSON, default=list)            # 大图/共用素材
    meta: Mapped[dict] = mapped_column(JSON, default=dict)                    # 其余元数据（专题、年份、难度自适应等）
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    subquestions: Mapped[list["SubQuestion"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", order_by="SubQuestion.seq")


class SubQuestion(Base):
    """小问：一等实体，独立题干/答案/评分细则/分值/标签，可单独组卷与审校。"""
    __tablename__ = "subquestions"
    __table_args__ = (
        UniqueConstraint("group_id", "seq", name="uq_subq_seq"),
        Index("ix_subq_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("question_groups.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=1)                     # 组内序号，自动编号 (1)(2)…
    label: Mapped[str | None] = mapped_column(String(16), nullable=True)     # 自定义编号，空则自动
    stem: Mapped[dict] = mapped_column(JSON, default=dict)                   # Tiptap doc
    answer: Mapped[dict] = mapped_column(JSON, default=dict)                 # Tiptap doc
    rubric: Mapped[dict] = mapped_column(JSON, default=dict)                 # 评分细则 Tiptap doc
    score: Mapped[float] = mapped_column(Float, default=0.0)                 # 分值
    q_type: Mapped[str] = mapped_column(String(24), default="简答")
    difficulty: Mapped[int] = mapped_column(Integer, default=2)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    material_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[dict] = mapped_column(JSON, default=dict)                 # 小问级来源/位置
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    group: Mapped[QuestionGroup] = relationship(back_populates="subquestions")


class EntityVersion(Base):
    """版本快照：实体类型 + 实体 ID，回滚与对比用。"""
    __tablename__ = "entity_versions"
    __table_args__ = (Index("ix_ev_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(24))     # group | subquestion | paper
    entity_id: Mapped[int] = mapped_column(Integer)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PaperTemplate(Base):
    """试卷模板：版式与内容分离（页眉页脚、分值区、答题区、字号、纸张）。"""
    __tablename__ = "paper_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    spec: Mapped[dict] = mapped_column(JSON, default=dict)   # {"page":"A4","margins":{...},"header":{...},"answerMode":"blank"}
    is_default: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BasketItem(Base):
    """组卷篮：按小问粒度行进篮，记录顺序、题号、分值（与题库内容分离）。"""
    __tablename__ = "basket_items"
    __table_args__ = (UniqueConstraint("paper_id", "subquestion_id", name="uq_basket_subq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(Integer, index=True)      # 逻辑指向 papers
    subquestion_id: Mapped[int] = mapped_column(ForeignKey("subquestions.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer, default=1)
    number: Mapped[str | None] = mapped_column(String(24), nullable=True)  # 显示题号，如“2(1)”
    score: Mapped[float] = mapped_column(Float, default=0.0)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)     # 入篮时的内容快照
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
