"""AI 接口：DeepSeek 配置 / 连接测试 / 翻译 / 逐页精读 / 自动归类 / 解析 / 生成答案。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..services import ai, site_guide

router = APIRouter(prefix="/api/ai", tags=["ai"])


class AiConfigIn(BaseModel):
    api_key: str | None = None          # 留空表示不修改；配合 clear_key 可删除
    base_url: str | None = None
    model: str | None = None
    clear_key: bool = False
    free_gateway: bool = False          # 使用本机 DSH 免费模型网关（无需填 Key）


class TranslateIn(BaseModel):
    text: str = Field(min_length=1)
    target: str = "简体中文"


class SummaryIn(BaseModel):
    doc_id: str
    page_no: int = 1


class AnswerIn(BaseModel):
    stem: str = ""
    q_type: str = ""
    answer_hint: str = ""
    image_path: str | None = None


class OcrImageIn(BaseModel):
    path: str = Field(min_length=1)


class ClassifyIn(BaseModel):
    text: str = Field(min_length=1)
    q_types: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    default_q_type: str = "简答"


class AnalysisIn(BaseModel):
    stem: str = Field(min_length=1)
    answer: str = ""
    q_type: str = ""


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    page: str = ""
    search: bool = False


@router.get("/status")
def status(db: Session = Depends(get_db)):
    return ai.public_config(db)


class SmartQuestionsIn(BaseModel):
    """按「级别 + 知识点 + 题型 + 难度 + 额外要求」命题（化竞智卷式面板）。"""
    source: str = ""
    doc_id: str | None = None
    count: int = Field(default=3, ge=1, le=12)
    level: str = ""
    topics: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    difficulty: int = Field(default=3, ge=1, le=5)
    extra: str = ""

class DocBriefIn(BaseModel):
    """文献解读：摘要 + 要点 + 对照段落 + 改编题。"""
    source: str = ""
    doc_id: str | None = None
    count: int = Field(default=2, ge=0, le=8)
    level: str = ""
    focus: str = ""

@router.get("/config")
def get_config(db: Session = Depends(get_db)):
    return ai.public_config(db)


@router.post("/config")
def set_config(body: AiConfigIn, db: Session = Depends(get_db)):
    return ai.save_config(db, api_key=body.api_key, base_url=body.base_url,
                          model=body.model, clear_key=body.clear_key,
                          free_gateway=body.free_gateway)


@router.get("/free-models")
def free_models():
    """列出 DeepSeek Harness 本机免费模型（需插件 + DSH 在运行）。"""
    return ai.list_free_models()


@router.post("/test")
def test_connection(db: Session = Depends(get_db)):
    return ai.test_connection(db)


@router.post("/chat")
def site_chat(body: ChatIn, db: Session = Depends(get_db)):
    """首页 AI 对话（非流式）：站内资料 + 通用问答，可选联网搜索。"""
    return site_guide.chat(db, [m.model_dump() for m in body.messages], body.page, body.search)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
def site_chat_stream(body: ChatIn):
    """首页 AI 对话（流式）：SSE 增量输出，可选联网搜索。"""
    payload = [m.model_dump() for m in body.messages]

    def gen():
        with SessionLocal() as db:
            for ev in site_guide.stream_chat(db, payload, body.page, body.search):
                kind = ev.pop("type")
                yield _sse(kind, ev)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/translate")
def translate(body: TranslateIn, db: Session = Depends(get_db)):
    return ai.translate(db, body.text, body.target)


@router.post("/summarize-page")
def summarize_page(body: SummaryIn, db: Session = Depends(get_db)):
    return ai.summarize_page(db, body.doc_id, body.page_no)


@router.post("/answer")
def answer(body: AnswerIn, db: Session = Depends(get_db)):
    return ai.generate_answer(db, body.stem, body.q_type, body.answer_hint, body.image_path or "")


@router.post("/ocr-image")
def ocr_image(body: OcrImageIn):
    return ai.ocr_image(body.path)


@router.post("/classify")
def classify(body: ClassifyIn, db: Session = Depends(get_db)):
    return ai.classify_question(
        db, body.text, q_types=body.q_types or None, topics=body.topics or None,
        default_q_type=body.default_q_type or "简答")


@router.post("/analysis")
def analysis(body: AnalysisIn, db: Session = Depends(get_db)):
    return ai.question_analysis(db, body.stem, body.answer, body.q_type)


@router.post("/smart-questions")
def smart_questions(body: SmartQuestionsIn, db: Session = Depends(get_db)):
    """按条件出题：素材可以直接粘贴，也可以选中一篇已有 PDF。"""
    source = (body.source or "").strip()
    if not source and body.doc_id:
        source = ai.pdf_to_text(ai._doc_file(body.doc_id))
    try:
        return ai.smart_questions(db, source, body.count, level=body.level,
                                  topics=body.topics, types=body.types,
                                  difficulty=body.difficulty, extra=body.extra)
    except HTTPException:
        raise
    except Exception as exc:                                 # noqa: BLE001
        raise HTTPException(502, f"AI 命题失败：{exc}") from exc


@router.post("/doc-brief")
def doc_brief(body: DocBriefIn, db: Session = Depends(get_db)):
    """文献解读：标题 / 摘要 / 要点 / 关键段落对照 / 改编题。"""
    source = (body.source or "").strip()
    if not source and body.doc_id:
        source = ai.pdf_to_text(ai._doc_file(body.doc_id))
    try:
        return ai.document_brief(db, source, body.count, level=body.level,
                                 focus=body.focus)
    except HTTPException:
        raise
    except Exception as exc:                                 # noqa: BLE001
        raise HTTPException(502, f"文献解读失败：{exc}") from exc
