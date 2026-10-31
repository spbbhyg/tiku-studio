from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BookOut(ORMModel):
    id: int
    role: str
    subject: str
    grade: int
    semester: str
    publisher: str
    edition_year: int
    title: str
    source_file: Optional[str] = None
    description: Optional[str] = None


class LessonOut(ORMModel):
    seq: int
    name: str
    pages: Optional[str] = None


class UnitOut(ORMModel):
    id: int
    seq: int
    name: str
    description: Optional[str] = None
    knowledge_points: list[str] = []
    lessons: list[LessonOut] = []
    question_count: int = 0


class BookDetailOut(BookOut):
    units: list[UnitOut] = []


class QuestionOut(ORMModel):
    id: int
    book_id: int
    unit_id: Optional[int] = None
    stem: str
    options: Optional[list[str]] = None
    answer: str
    analysis: Optional[str] = None
    q_type: str
    difficulty: int
    knowledge_points: list[str] = []
    images: list[dict] = []
    source_page: Optional[str] = None
    source_title: Optional[str] = None
    source_citation: Optional[str] = None
    tags: list[str] = []
    scope_status: str
    scope_reason: Optional[str] = None
    status: str
    book_title: str = ""
    unit_name: Optional[str] = None
    unit_seq: Optional[int] = None
    set_id: Optional[int] = None
    set_name: Optional[str] = None
    set_year: Optional[int] = None


class QuestionPatch(BaseModel):
    stem: Optional[str] = None
    options: Optional[list[str]] = None
    answer: Optional[str] = None
    analysis: Optional[str] = None
    q_type: Optional[str] = None
    difficulty: Optional[int] = Field(None, ge=1, le=3)
    knowledge_points: Optional[list[str]] = None
    unit_id: Optional[int] = None
    scope_status: Optional[str] = None
    scope_reason: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[list[str]] = None
    source_title: Optional[str] = None
    source_citation: Optional[str] = None
    set_id: Optional[int] = None


class PaperGenerate(BaseModel):
    textbook_id: Optional[int] = None
    unit_from: int = 1
    unit_to: Optional[int] = None
    title: Optional[str] = None
    total_score: float = 100
    duration_minutes: int = 60
    include_extension: bool = False
    counts: dict[str, int] = Field(default_factory=dict, description="按题型要求数量，如 {\"口算\":10,\"填空\":8}")
    per_type_score: dict[str, float] = Field(default_factory=dict, description="覆盖每种题型每题分值")
    seed: Optional[int] = None
    question_ids: list[int] = Field(default_factory=list, description="手动选题模式：按给定顺序组卷")


class PaperQuestionOut(BaseModel):
    id: int
    seq: int
    score: float
    question_id: Optional[int] = None
    snapshot: dict[str, Any]


class PaperOut(BaseModel):
    id: int
    title: str
    subject: str
    grade: int
    semester: str
    textbook_id: Optional[int] = None
    unit_from: Optional[int] = None
    unit_to: Optional[int] = None
    unit_ids: list[int] = []
    total_score: float
    duration_minutes: int
    include_extension: bool
    created_at: datetime
    config: dict = {}
    items: list[PaperQuestionOut] = []


class PaperBrief(BaseModel):
    id: int
    title: str
    subject: str
    grade: int
    semester: str
    total_score: float
    duration_minutes: int
    question_count: int
    created_at: datetime


class StatsOut(BaseModel):
    books: int
    textbooks: int
    questions: int
    confirmed_questions: int
    draft_questions: int
    suspect_questions: int
    extension_questions: int
    papers: int
    by_subject: dict[str, int] = {}
    by_type: dict[str, int] = {}
