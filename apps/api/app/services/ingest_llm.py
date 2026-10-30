"""内置 LLM 拆解引擎：网页上传 PDF → 转图 → 视觉模型拆解 → 校验后返回 bundle。

与本地 Agent 模式共用 packages/schema 下的同一套 schema，产物可互换。
实验性功能：拆解结果一律进入待审核队列，确认前不进入组卷池。
"""
from __future__ import annotations

import base64
import json
import re

import fitz  # PyMuPDF
import httpx
from sqlalchemy.orm import Session

from ..models import Setting
from ..services.bundle_import import validate_bundle

PROVIDER_BASE: dict[str, str] = {
    "glm": "https://open.bigmodel.cn/api/paas/v4",
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
}
DEFAULT_MODEL: dict[str, str] = {
    "glm": "glm-4v-plus",
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o",
}
SYSTEM_PROMPT = """你是小学教辅拆解助手。用户会给出一份课本/教辅/试卷的页面图片和元信息，\
请把它们拆解成 JSON bundle（题目级，遵循 question-bundle 结构）：
{"schema_version":1,"book":{...},"source_textbook_title":"...","questions":[...]}
要求：
1. book 字段用用户提供的元信息补全；role 按内容判断：课本=textbook，教辅=workbook，试卷集=paper_set。
2. 每道题独立拆出：stem 题干（图形用 ●▲★■○ 等符号在文本中表达）、options（选择题候选项）、
   answer（参考答案）、analysis（解析，若有）、q_type、difficulty(1基础/2中等/3提优)、
   knowledge_points（知识点名，尽量与同版本教材单元知识点一致）。
3. 教材范围内的题不加 scope_status；明确超纲/超前的提优题加 scope_status="extension" 并写 scope_reason。
4. 只输出 JSON 本体，不要 markdown 代码块，不要解释。"""


def get_settings(session: Session) -> dict:
    row = session.get(Setting, "llm")
    base = {"llm_provider": "glm", "llm_base_url": "", "llm_api_key": "",
            "llm_model": "", "max_pages": 30}
    if row:
        base.update(row.value or {})
    return base


def save_settings(session: Session, patch: dict) -> dict:
    current = get_settings(session)
    current.update({k: v for k, v in patch.items() if v is not None})
    row = session.get(Setting, "llm")
    if row is None:
        row = Setting(key="llm", value=current)
        session.add(row)
    else:
        row.value = current
    session.commit()
    return current


def pdf_to_images(data: bytes, max_pages: int = 30, zoom: float = 2.0) -> list[str]:
    """PDF 每页转 base64 PNG data_url。"""
    out: list[str] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for i in range(min(len(doc), max_pages)):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            b64 = base64.b64encode(pix.tobytes("png")).decode()
            out.append(f"data:image/png;base64,{b64}")
    return out


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if m:
        text = m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型未返回 JSON")
    return json.loads(text[start:end + 1])


def parse_pdf(session: Session, data: bytes, meta: dict) -> dict:
    """调视觉模型拆解 PDF，返回通过 schema 校验的 bundle（不入库，由调用方决定）。"""
    cfg = get_settings(session)
    if not cfg.get("llm_api_key"):
        raise RuntimeError("尚未配置 LLM API key，请先到『设置』页填写；或改用本地 Agent 拆解模式。")
    base_url = cfg.get("llm_base_url") or PROVIDER_BASE.get(cfg["llm_provider"], "")
    model = cfg.get("llm_model") or DEFAULT_MODEL.get(cfg["llm_provider"], "")
    if not base_url:
        raise RuntimeError("自定义供应商需要填写 base_url")

    pages = pdf_to_images(data, max_pages=int(cfg.get("max_pages", 30)))
    if not pages:
        raise ValueError("PDF 没有可读页面")

    user_content: list[dict] = [
        {"type": "text", "text": "元信息：" + json.dumps(meta, ensure_ascii=False)
         + "\n以下是每一页图片，请按系统提示拆解为题目级 bundle。"}
    ]
    for url in pages:
        user_content.append({"type": "image_url", "image_url": {"url": url}})

    resp = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {cfg['llm_api_key']}"},
        json={"model": model, "temperature": 0.1,
              "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                           {"role": "user", "content": user_content}]},
        timeout=300,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    bundle = _extract_json(content)
    if not bundle.get("book"):
        bundle["book"] = meta
    validate_bundle(bundle)  # 不过则抛 ValueError
    return bundle
