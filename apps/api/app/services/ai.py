"""DeepSeek AI 能力：翻译、逐页精读（摘要 + 机理/重点）、参考答案生成、题型归类/解析。

配置存放在 SQLite 的 settings 表（key = deepseek），也支持环境变量覆盖：
- DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL

只把文字发给模型（不传图片/PDF 原件）。扫描页/题图用本机 RapidOCR 先转成文字，
没有安装 OCR 时会明确提示。没有配置 Key 时接口返回 400，前端据此引导去「设置 → AI 模型」。
"""
from __future__ import annotations

import json
import logging
import importlib.util
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fitz
import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import LIBRARY_DIR
from ..models import Setting

logger = logging.getLogger("tiku.ai")

SETTING_KEY = "deepseek"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DOC_ID_RE = re.compile(r"^[0-9a-f]{12}$")

# 本机 DSH 免费模型网关（OpenAI 兼容）。
# 插件把 Key 放在 ~/.dsh/openai-gateway/api-key，可能轮换，所以库里只存标记，
# 每次调用时再读文件，不把 Key 复制进项目的 SQLite。
DSH_GATEWAY_URL = (os.environ.get("DSH_GATEWAY_URL") or "http://127.0.0.1:8326/v1").rstrip("/")
DSH_DEFAULT_MODEL = "our-free-model/deepseek-v4.1-flash-free"


def dsh_key_path() -> Path:
    return Path.home() / ".dsh" / "openai-gateway" / "api-key"


def dsh_gateway_key() -> str:
    try:
        return dsh_key_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""




# 一页正文最多取这么多行送给模型，避免超长页把 token 撑爆
MAX_LINES_PER_PAGE = 120
MAX_CHARS_PER_LINE = 400
MAX_PAGES = 6

# 与 authoring.py 的 Chemy 专题保持一致（避免循环 import，这里各存一份）
CHEM_TOPICS = [
    "有机化学", "高分子化学", "晶体化学", "无机综合与结构推断", "方程式与元素化学",
    "热力学与化学平衡", "电化学", "动力学", "分析化学",
]


# ---------------------------------------------------------------- 配置
def _saved(db: Session) -> dict:
    row = db.scalar(select(Setting).where(Setting.key == SETTING_KEY))
    value = row.value if row else None
    return value if isinstance(value, dict) else {}


def _full_config(db: Session) -> dict:
    saved = _saved(db)
    env_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    env_base = (os.environ.get("DEEPSEEK_BASE_URL") or "").strip()
    env_model = (os.environ.get("DEEPSEEK_MODEL") or "").strip()
    paid_key = str(saved.get("api_key") or "").strip()
    paid_base = str(saved.get("base_url") or DEFAULT_BASE_URL).strip().rstrip("/")
    paid_model = str(saved.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    free_enabled = bool(saved.get("use_free_gateway"))
    free_key = dsh_gateway_key() if free_enabled else ""
    if env_key:
        # 环境变量优先级最高；同时视为不用免费通道
        api_key, base_url, model = env_key, (env_base or paid_base), (env_model or paid_model)
        free_gateway = False
    elif free_key:
        # 每次读取本机网关 Key，插件轮换后仍然可用；付费 Key 原样保留
        api_key = free_key
        base_url = str(saved.get("free_base_url") or DSH_GATEWAY_URL).strip().rstrip("/")
        model = str(saved.get("free_model") or DSH_DEFAULT_MODEL).strip() or DSH_DEFAULT_MODEL
        free_gateway = True
    else:
        api_key, base_url, model = paid_key, paid_base, paid_model
        free_gateway = False
    source = "env" if env_key else ("dsh_gateway" if free_gateway else ("db" if api_key else ""))
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "source": source,
        "env_override": bool(env_key),
        "free_gateway": free_gateway,
        "free_enabled": free_enabled,
        "paid_key": paid_key,
        "paid_base_url": paid_base,
        "paid_model": paid_model,
    }


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return key[:2] + "…" + key[-2:]
    return key[:5] + "…" + key[-4:]


def _ocr_available() -> bool:
    return importlib.util.find_spec("rapidocr_onnxruntime") is not None


def public_config(db: Session) -> dict:
    cfg = _full_config(db)
    return {
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "has_key": bool(cfg["api_key"]),
        "key_hint": "本机免费模型（DSH 网关）" if cfg.get("free_gateway") else _mask(cfg["api_key"]),
        "source": cfg["source"],
        "env_override": cfg["env_override"],
        "configured": bool(cfg["api_key"]),
        "free_gateway": bool(cfg.get("free_gateway")),
        "free_enabled": bool(cfg.get("free_enabled")),
        "paid_configured": bool(cfg.get("paid_key")),
        "paid_base_url": cfg.get("paid_base_url") or DEFAULT_BASE_URL,
        "paid_model": cfg.get("paid_model") or DEFAULT_MODEL,
        "gateway_url": DSH_GATEWAY_URL,
        "gateway_key_file": str(dsh_key_path()),
        "gateway_key_found": bool(dsh_gateway_key()),
        "ocr_available": _ocr_available(),
    }


def save_config(db: Session, *, api_key: str | None = None, base_url: str | None = None,
                model: str | None = None, clear_key: bool = False,
                free_gateway: bool = False) -> dict:
    saved = _saved(db)
    if free_gateway:
        # 只切换通道，付费 Key / Base URL / 模型原样保留，随时可以切回
        saved["use_free_gateway"] = True
        saved["free_base_url"] = (base_url or DSH_GATEWAY_URL).strip().rstrip("/") or DSH_GATEWAY_URL
        saved["free_model"] = (model or DSH_DEFAULT_MODEL).strip() or DSH_DEFAULT_MODEL
        row = db.scalar(select(Setting).where(Setting.key == SETTING_KEY))
        if row:
            row.value = saved
        else:
            db.add(Setting(key=SETTING_KEY, value=saved))
        db.commit()
        return public_config(db)
    saved.pop("use_free_gateway", None)
    if clear_key:
        saved.pop("api_key", None)
    elif api_key is not None and api_key.strip():
        saved["api_key"] = api_key.strip()
    if base_url is not None and base_url.strip():
        saved["base_url"] = base_url.strip().rstrip("/")
    if model is not None and model.strip():
        saved["model"] = model.strip()
    row = db.scalar(select(Setting).where(Setting.key == SETTING_KEY))
    if row:
        row.value = saved
    else:
        db.add(Setting(key=SETTING_KEY, value=saved))
    db.commit()
    return public_config(db)


FREE_MODEL_ORDER = [
    "our-free-model/deepseek-v4.1-flash-free",
    "our-free-model/glm-5.3-flash-free",
    "our-free-model/mimo-v2.6-flash-free",
    "our-free-model/beam-free",
    "our-free-model/flint-40b-free",
]


def list_free_models() -> dict:
    """探测本机 DSH 网关的免费模型列表（不修改配置）。"""
    key = dsh_gateway_key()
    if not key:
        return {
            "ok": False,
            "base_url": DSH_GATEWAY_URL,
            "models": [],
            "recommended": "",
            "gateway_key_found": False,
            "error": f"没有找到 DSH 网关密钥：{dsh_key_path()}。请确认免费模型插件已启用、"
                     "DeepSeek Harness 正在运行。",
        }
    try:
        with httpx.Client(timeout=8) as client:
            resp = client.get(DSH_GATEWAY_URL + "/models",
                              headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        return {
            "ok": False, "base_url": DSH_GATEWAY_URL, "models": [], "recommended": "",
            "gateway_key_found": True,
            "error": f"连不上本机网关 {DSH_GATEWAY_URL}：{exc}。请确认 DeepSeek Harness 正在运行。",
        }
    if resp.status_code == 401:
        return {
            "ok": False, "base_url": DSH_GATEWAY_URL, "models": [], "recommended": "",
            "gateway_key_found": True,
            "error": "网关拒绝了这个本机 Key（401），插件可能刚轮换了 Key，重启 DeepSeek Harness 后再试。",
        }
    if resp.status_code >= 400:
        return {
            "ok": False, "base_url": DSH_GATEWAY_URL, "models": [], "recommended": "",
            "gateway_key_found": True,
            "error": f"网关返回 {resp.status_code}。",
        }
    try:
        data = resp.json()
        all_ids = [str(m.get("id") or "") for m in (data.get("data") or []) if m.get("id")]
    except Exception as exc:                                 # noqa: BLE001
        return {
            "ok": False, "base_url": DSH_GATEWAY_URL, "models": [], "recommended": "",
            "gateway_key_found": True, "error": f"网关返回格式异常：{exc}",
        }
    free_ids = [i for i in all_ids if i.startswith("our-free-model/")]
    ids = free_ids or all_ids
    rank = {name: n for n, name in enumerate(FREE_MODEL_ORDER)}
    ids.sort(key=lambda name: (rank.get(name, 999), name))
    recommended = ids[0] if ids else ""
    return {
        "ok": bool(ids),
        "base_url": DSH_GATEWAY_URL,
        "models": ids,
        "recommended": recommended,
        "gateway_key_found": True,
        "all_count": len(all_ids),
        "free_count": len(free_ids),
    }


# ---------------------------------------------------------------- HTTP 调用
def _endpoint(base_url: str) -> str:
    base = (base_url or DEFAULT_BASE_URL).rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _chat(db: Session, messages: list[dict], *, temperature: float = 0.2,
          max_tokens: int = 2000, json_mode: bool = False,
          timeout: float = 120.0) -> tuple[str, dict]:
    cfg = _full_config(db)
    if not cfg["api_key"]:
        raise HTTPException(
            400, "还没有配置 DeepSeek API Key：请到「设置 → AI 模型」填写，"
                 "或设置环境变量 DEEPSEEK_API_KEY 后重启服务。")
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}
    started = time.time()
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(_endpoint(cfg["base_url"]), headers=headers, json=payload)
    except httpx.TimeoutException:
        raise HTTPException(504, "DeepSeek 超时了，请稍后重试（或检查网络 / 代理）")
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"连接 DeepSeek 失败：{exc}")
    if resp.status_code == 401:
        raise HTTPException(400, "DeepSeek 拒绝了这个 API Key（401），请到「设置 → AI 模型」检查")
    if resp.status_code == 402:
        raise HTTPException(400, "DeepSeek 账户余额不足（402），请充值后再试")
    if resp.status_code == 429:
        raise HTTPException(429, "DeepSeek 限流了（429），请稍后再试")
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = (resp.json() or {}).get("error", {}).get("message", "")
        except Exception:                                    # noqa: BLE001
            detail = resp.text[:300]
        raise HTTPException(502, f"DeepSeek 返回 {resp.status_code}：{detail or '未知错误'}")
    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as exc:                                 # noqa: BLE001
        raise HTTPException(502, f"DeepSeek 返回格式异常：{exc}")
    usage = dict(data.get("usage") or {})
    usage["elapsed_ms"] = int((time.time() - started) * 1000)
    usage["model"] = data.get("model") or cfg["model"]
    return (content or "").strip(), usage


def _chat_stream(db: Session, messages: list[dict], *, temperature: float = 0.2,
                 max_tokens: int = 2000, json_mode: bool = False):
    """流式调用：逐块产出 content 文本。上游仍走同一个 OpenAI 兼容接口。"""
    cfg = _full_config(db)
    if not cfg["api_key"]:
        raise HTTPException(
            400, "还没有配置 DeepSeek API Key：请到「设置 → AI 模型」填写，"
                 "或设置环境变量 DEEPSEEK_API_KEY 后重启服务。")
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=httpx.Timeout(180, connect=20)) as client:
            with client.stream("POST", _endpoint(cfg["base_url"]), headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", "replace")
                    detail = ""
                    try:
                        detail = (json.loads(body) or {}).get("error", {}).get("message", "")
                    except Exception:                        # noqa: BLE001
                        detail = body[:300]
                    if resp.status_code == 401:
                        raise HTTPException(400, "DeepSeek 拒绝了这个 API Key（401），请到「设置 → AI 模型」检查")
                    if resp.status_code == 402:
                        raise HTTPException(400, "DeepSeek 账户余额不足（402），请充值后再试")
                    if resp.status_code == 429:
                        raise HTTPException(429, "DeepSeek 限流了（429），请稍后再试")
                    raise HTTPException(502, f"AI 返回 {resp.status_code}：{detail or '未知错误'}")
                for line in resp.iter_lines():
                    line = (line or "").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except Exception:                        # noqa: BLE001
                        continue
                    choices = chunk.get("choices") or [{}]
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if text:
                        yield str(text)
    except httpx.TimeoutException:
        raise HTTPException(504, "AI 超时了，请稍后重试（或检查网络 / 代理）")
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"连接 AI 失败：{exc}")


def _json_from_text(text: str) -> dict:
    """模型偶尔会包 ```json 围栏，这里做一次稳妥解析。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:                                        # noqa: BLE001
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:                                # noqa: BLE001
                pass
    raise HTTPException(502, f"AI 返回的不是有效 JSON：{raw[:200]}")


# ---------------------------------------------------------------- 测试连接 / 翻译
def test_connection(db: Session) -> dict:
    content, usage = _chat(
        db,
        [{"role": "system", "content": "你是连通性测试助手。"},
         {"role": "user", "content": "只回复两个字符：ok"}],
        temperature=0, max_tokens=8,
    )
    return {"ok": True, "reply": content[:40], "usage": usage}


def translate(db: Session, text: str, target: str = "简体中文") -> dict:
    src = (text or "").strip()
    if not src:
        raise HTTPException(400, "没有可翻译的文字")
    if len(src) > 12000:
        raise HTTPException(400, "一次最多翻译 12000 个字符，请分几次选中")
    target = (target or "简体中文").strip()[:20]
    content, usage = _chat(
        db,
        [
            {"role": "system", "content": (
                "你是学术文献翻译助手，服务对象是高校教师。把用户给出的英文片段翻译成"
                f"{target}。要求：术语准确、语气正式、语句通顺；数学公式、变量名、"
                "文献编号、图表编号保持原样；不要添加解释，只输出译文。"
            )},
            {"role": "user", "content": src},
        ],
        temperature=0.2,
        max_tokens=min(4000, max(600, len(src) * 2)),
    )
    return {"text": content, "source_chars": len(src), "usage": usage,
            "model": usage.get("model")}


# ---------------------------------------------------------------- 文章终点识别
def _doc_file(doc_id: str):
    if not DOC_ID_RE.match(doc_id or ""):
        raise HTTPException(400, "非法 doc_id")
    path = LIBRARY_DIR / "uploads" / f"{doc_id}.pdf"
    if not path.is_file():
        raise HTTPException(404, "文档不存在或已被清理，请重新上传")
    return path


def _page_lines(page) -> tuple[list[dict], float, float]:
    """提取一页的文本行（过滤页眉页脚留白区），返回 (lines, width, height)。"""
    rect = page.rect
    width, height = float(rect.width), float(rect.height)
    out: list[dict] = []
    try:
        data = page.get_text("dict")
    except Exception:                                        # noqa: BLE001
        return out, width, height
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            text = "".join(s.get("text") or "" for s in spans).strip()
            if not text:
                continue
            bbox = line.get("bbox") or (0, 0, 0, 0)
            # 去掉页面最上/最下的页眉页脚（页码、期刊名、DOI 条）
            if bbox[1] < height * 0.035 or bbox[3] > height * 0.965:
                continue
            size = max((float(s.get("size") or 0) for s in spans), default=0)
            out.append({
                "text": text[:MAX_CHARS_PER_LINE],
                "x0": bbox[0], "y0": bbox[1], "x1": bbox[2], "y1": bbox[3],
                "size": round(size, 1),
            })
    out.sort(key=lambda ln: (round(ln["y0"], 1), ln["x0"]))
    return out[:MAX_LINES_PER_PAGE], width, height


# ---------- 扫描件 OCR（本机、可选） ----------
_OCR_ENGINE = None


def _ocr_engine():
    """惰性加载 RapidOCR：没装 OCR 时其余功能照常可用。"""
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415

        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _ocr_array(arr, width: int, height: int) -> list[dict]:
    """把 RapidOCR 的识别结果转成与 _page_lines 相同的行结构。"""
    try:
        import numpy as np  # noqa: F401, PLC0415
        engine = _ocr_engine()
    except Exception:  # noqa: BLE001
        return []
    try:
        raw = engine(arr)
    except Exception:  # noqa: BLE001
        return []
    result = raw[0] if isinstance(raw, tuple) else raw
    if not result:
        return []
    lines: list[dict] = []
    for item in result:
        try:
            box, text, score = item[0], str(item[1]).strip(), float(item[2])
        except Exception:  # noqa: BLE001
            continue
        if not text or score < 0.35:
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
        except Exception:  # noqa: BLE001
            continue
        if not xs or not ys:
            continue
        lines.append({
            "text": text[:MAX_CHARS_PER_LINE],
            "x0": round(min(xs) / max(1, width), 5),
            "y0": round(min(ys) / max(1, height), 5),
            "x1": round(max(xs) / max(1, width), 5),
            "y1": round(max(ys) / max(1, height), 5),
            "size": round((max(ys) - min(ys)) * 0.75, 1),
        })
    lines.sort(key=lambda ln: (round(ln["y0"], 1), ln["x0"]))
    return lines[:MAX_LINES_PER_PAGE]


def _ocr_page(page) -> list[dict]:
    """渲染 PDF 页并 OCR；dpi 太高会慢，200 对 A4 足够。"""
    try:
        import numpy as np  # noqa: PLC0415

        pix = page.get_pixmap(dpi=200)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n >= 3:
            arr = arr[:, :, :3][:, :, ::-1]  # RGB → BGR（RapidOCR 与 cv2 一致）
        return _ocr_array(arr, pix.width, pix.height)
    except Exception:  # noqa: BLE001
        return []


def _library_path(rel: str):
    from pathlib import Path  # noqa: PLC0415

    p = Path(str(rel or "")).expanduser()
    if not p.is_absolute():
        p = LIBRARY_DIR / p
    p = p.resolve()
    try:
        p.relative_to(LIBRARY_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "图片路径不在资料库里，无法读取") from None
    if not p.exists():
        raise HTTPException(404, "题目图片不存在，请重新裁剪后再试")
    return p


def _ocr_image_file(rel: str) -> list[dict]:
    """对画布里的题图做 OCR（用于「AI 生成答案」时题目只有图片的情况）。"""
    from PIL import Image  # noqa: PLC0415

    path = _library_path(rel)
    try:
        import numpy as np  # noqa: PLC0415

        im = Image.open(path).convert("RGB")
        arr = np.array(im)[:, :, ::-1]
        return _ocr_array(arr, im.width, im.height)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"题目图片 OCR 失败：{exc}") from exc


def ocr_image(rel: str) -> dict:
    """把画布里的裁剪图识别成文字，供出题页「图片转文字」使用。"""
    lines = _ocr_image_file(rel)
    text = "\n".join(str(x.get("text", "")).strip() for x in lines if str(x.get("text", "")).strip())
    return {"path": rel, "count": len(lines), "text": text, "lines": lines}


def summarize_page(db: Session, doc_id: str, page_no: int = 1) -> dict:
    """精读一页：先概括本页内容，再挑出机理/重点解释相关的原文行。"""
    path = _doc_file(doc_id)
    with fitz.open(str(path)) as doc:
        total = len(doc)
        if page_no < 1 or page_no > total:
            raise HTTPException(400, "页码超出文档范围")
        page = doc.load_page(page_no - 1)
        lines, width, height = _page_lines(page)
        ocr_used = False
        if not lines:
            lines = _ocr_page(page)
            ocr_used = bool(lines)
        page_count = total

    if not lines:
        return {
            "page_no": page_no, "total_pages": page_count, "has_text": False, "ocr_used": False,
            "summary": "", "key_points": [], "usage": {}, "model": None,
            "message": "这页没有文字层，本机 OCR 也没识别出文字；可以直接框选裁剪成图片。"
                       "（如需自动读扫描件，运行 pip install rapidocr_onnxruntime）",
        }

    numbered = "\n".join(f"{i}. {ln['text']}" for i, ln in enumerate(lines, 1))
    system = (
        "你是一名化学文献精读助手，服务对象是高校化学教师。请分两步处理这一页：\n"
        "第一步，用 2-4 句中文概括本页讲了什么：研究对象、反应/方法、关键数据或结论。\n"
        "第二步，从给出的行里挑出与『机理研究、反应路径、结构解释、关键结论』有关的句子，"
        "最多 5 条；每条给出 line_no 和一句中文说明为什么重要。"
        "只挑真正解释“为什么/怎么发生”的句子，不要挑标题、作者、致谢、参考文献。\n"
        "只输出 JSON，不要输出任何其他文字。"
    )
    user = (
        f"这是论文第 {page_no} 页的文字，每行前面是行号（本页从 1 开始）。\n"
        '请输出：{"summary": "本页中文概括", "key_points": '
        '[{"line_no": 3, "note": "这一行为什么是机理/重点"}]}\n'
        "key_points 最多 5 条；line_no 必须来自下面给出的行号；没有就返回空数组。\n\n"
        + numbered
    )
    content, usage = _chat(db, [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], temperature=0, max_tokens=900, json_mode=True)

    data = _json_from_text(content)
    result = {
        "page_no": page_no,
        "total_pages": page_count,
        "has_text": True,
        "ocr_used": ocr_used,
        "summary": str(data.get("summary") or "").strip()[:800],
        "key_points": [],
        "usage": usage,
        "model": usage.get("model"),
        "width": width,
        "height": height,
    }

    raw = data.get("key_points")
    if isinstance(raw, list):
        for item in raw[:6]:
            if not isinstance(item, dict):
                continue
            try:
                lno = int(item.get("line_no"))
            except Exception:                                # noqa: BLE001
                continue
            if not 1 <= lno <= len(lines):
                continue
            line = lines[lno - 1]
            h = height or 1
            result["key_points"].append({
                "line_no": lno,
                "y": round(min(0.995, max(0.005,
                            ((line["y0"] + line["y1"]) / 2) / h)), 4),
                "text": line["text"],
                "note": str(item.get("note") or "").strip()[:200],
            })
    if not result["summary"] and not result["key_points"]:
        result["message"] = "模型没有返回本页概括，可以稍后重试。"
    return result


def _answer_payload(content: str) -> dict:
    """解析「答案 / 解析 / 参考文献」JSON；模型跑偏时整段当答案。"""
    try:
        data = _json_from_text(content)
    except HTTPException:
        data = None
    if not isinstance(data, dict):
        return {"answer": (content or "").strip(), "analysis": "", "references": []}
    answer = str(data.get("answer") or data.get("text") or "").strip()
    analysis = str(data.get("analysis") or "").strip()
    raw_refs = data.get("references") or []
    if isinstance(raw_refs, str):
        refs = [raw_refs.strip()] if raw_refs.strip() else []
    else:
        refs = [str(r).strip() for r in raw_refs if str(r).strip()]
    return {"answer": answer, "analysis": analysis, "references": refs}


def generate_answer(db: Session, stem: str, q_type: str = "", answer_hint: str = "",
                    image_path: str = "", verify: bool = True) -> dict:
    """按题干生成参考答案 + 解析 + 参考文献，并做一遍自检复核。"""
    text = (stem or "").strip()
    ocr_used = False
    if not text and image_path:
        ocr_lines = _ocr_image_file(image_path)
        text = "\n".join(ln["text"] for ln in ocr_lines).strip()
        ocr_used = bool(text)
    if not text:
        raise HTTPException(
            400,
            "题目画布还没有可读文字：先用「＋ 文本」写题干，或在「题干文字」里粘贴题目；"
            "图片题也可以先裁剪进画布，让本机 OCR 识别（需安装 rapidocr_onnxruntime）。",
        )
    if len(text) > 16000:
        text = text[:16000]
    system = (
        "你是一名化学奥林匹克竞赛命题与阅卷专家。请认真解题，只输出一个 JSON 对象：\n"
        '{"answer": "答案正文", "analysis": "解析", "references": ["参考文献1", "参考文献2"]}\n'
        "要求：\n"
        "1. answer：按题干小问编号（1-1、1-2、(1)(2)…）逐条作答，保留编号；结构式用文字描述"
        "（如“苯环对位取代”），反应式写清条件与主要产物；计算题写公式、代入、结果、单位与有效数字。\n"
        "2. 先用题给的所有定量数据（质量分数、失重、物质的量、电动势、速率等）反推并验算中间体与产物；"
        "验算不通过的候选必须舍弃，换成能同时满足全部数据的结构。落笔前再确认元素身份、配平、电荷守恒、"
        "电子数与单位；答案只保留最终结论，不要写试算、自我怀疑或反复修改过程。"
        "答案只保留最终结论，不要写试算、自我怀疑或反复修改过程。\n"
        "3. analysis：写清关键推理、所用公式/判据、易错点，不要整段重复答案；控制在 600 字内。\n"
        "4. references：只列你确定真实存在的教材或文献（作者、书名/期刊、年份）；"
        "没有把握就返回空数组 []，绝对不要编造。\n"
        "5. 信息不足时给出最合理的一种结构与结论，不罗列多个候选；总长 answer ≤1500 字。"
    )
    user = f"题型：{q_type or '综合题'}\n"
    if answer_hint.strip():
        user += f"参考答案要点/评分点（可参考，不要照抄）：{answer_hint.strip()[:3000]}\n"
    user += f"\n题干：\n{text}\n\n请按 JSON 输出答案、解析和参考文献。"
    content, usage = _chat(db, [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], temperature=0.1, max_tokens=16000, json_mode=True)
    payload = _answer_payload(_repair_json(content) or content if content else content)
    if not payload.get("answer"):          # 推理型模型可能把预算全花在思考上，压篇幅再要一次
        content, usage = _chat(db, [
            {"role": "system", "content": "直接给出答案 JSON，不要推理过程：{\"answer\":\"…\",\"analysis\":\"…\",\"references\":[]}"},
            {"role": "user", "content": user + chr(10) + chr(10) + "要求：answer 控制在 800 字内。"},
        ], temperature=0.1, max_tokens=16000, json_mode=True, timeout=240.0)
        payload = _answer_payload(_repair_json(content) or content if content else content)

    if verify and payload.get("answer"):
        check_system = (
            "你是化学奥赛审题人。请复核给定答案：逐项用题给数据复算（质量分数、失重、"
            "计量关系、电动势等），检查元素身份、化学式、配平、电荷、"
            "单位、有效数字、小问是否齐全；发现与题给数据不符就改正。只输出修正后的同一个 JSON 结构"
            '（answer / analysis / references），不要解释复核过程；原答案正确就原样返回。'
            "references 没有把握时返回 []。"
        )
        draft = json.dumps(payload, ensure_ascii=False)
        try:
            fixed, usage2 = _chat(db, [
                {"role": "system", "content": check_system},
                {"role": "user", "content": f"题干：\n{text}\n\n待复核答案：\n{draft}"},
            ], temperature=0.0, max_tokens=16000, json_mode=True)
            checked = _answer_payload(fixed)
            if checked.get("answer"):
                payload = checked
            usage = {**usage, **usage2, "elapsed_ms": (usage.get("elapsed_ms") or 0)
                     + (usage2.get("elapsed_ms") or 0), "verified": True}
        except HTTPException:
            usage = {**usage, "verified": False}

    answer = payload.get("answer") or content.strip()
    analysis = payload.get("analysis") or ""
    refs = payload.get("references") or []
    return {
        "answer": answer,
        "analysis": analysis,
        "references": refs,
        "text": answer,
        "usage": usage,
        "model": usage.get("model"),
        "ocr_used": ocr_used,
    }


# ---------------------------------------------------------------- 整卷生成 / 试卷导入

def _json_ok(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except Exception:  # noqa: BLE001
        return False


def _repair_json(text: str):
    """从可能被截断/带围栏的回复里抠出能解析的 JSON；救不回来返回 None。"""
    t = (text or "").strip()
    if not t:
        return None
    t = re.sub(r"^```(?:json)?", "", t).replace("```", "").strip()
    if _json_ok(t):
        return t
    start = t.find("{")
    if start < 0:
        start = t.find("[")
    if start < 0:
        return None
    body = t[start:]
    for cut in range(len(body), max(len(body) - 4000, 0), -1):
        frag = body[:cut]
        for tail in ("", "]}", "}", "}]}", "}]}"):
            if _json_ok(frag + tail):
                return frag + tail
    return None


def _questions_payload(content: str) -> list[dict]:
    """解析模型返回的 {"questions": [...]}，清洗成快照字段。"""
    try:
        data = _json_from_text(content)
    except HTTPException:
        data = None
    if data is None:
        try:
            fixed = _repair_json(content)
            data = _json_from_text(fixed) if fixed else None
        except HTTPException:
            data = None
    if isinstance(data, dict):
        raw = data.get("questions")
        if raw is None:
            raw = data.get("items")
        if raw is None and data and all(isinstance(v, dict) for v in data.values()):
            raw = list(data.values())
        raw = raw or []
    elif isinstance(data, list):
        raw = data
    else:
        raw = []
    out: list[dict] = []
    for q in raw or []:
        if not isinstance(q, dict):
            continue
        stem = str(q.get("stem") or q.get("question") or "").strip()
        if not stem:
            continue
        kps = q.get("knowledge_points") or q.get("topics") or []
        if isinstance(kps, str):
            kps = [x.strip() for x in re.split(r"[,，、;；]", kps) if x.strip()]
        try:
            score = float(q.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        out.append({
            "q_type": str(q.get("q_type") or q.get("type") or "综合").strip()[:16],
            "stem": stem[:12000],
            "answer": str(q.get("answer") or "").strip()[:12000],
            "analysis": str(q.get("analysis") or "").strip()[:4000],
            "knowledge_points": [str(x).strip()[:40] for x in (kps or []) if str(x).strip()][:6],
            "score": max(0.0, score),
        })
    return out


def _gen_one(db: Session, text: str, q_type: str, difficulty: int, idx: int, total: int) -> dict | None:
    """命制 1 道题（单次调用，篇幅约束写死，避免被 token 上限截断）。"""
    system = (
        "你是一名化学奥林匹克竞赛命题专家。只输出一个 JSON 对象，不要解释、不要推理过程。"
    )
    user = (
        "以给定素材为主题，命制 1 道完整试题。\n"
        "格式：{\"questions\": [{\"q_type\": \"综合\", \"stem\": \"题干（含小问，编号 1-1、1-2…）\", "
        "\"answer\": \"参考答案\", \"analysis\": \"解析\", \"knowledge_points\": [\"专题\"], \"score\": 10}]}\n"
        "要求：\n"
        "1. 题干不超过 320 字，必须自洽（给足数据/条件）。\n"
        "2. 参考答案不超过 200 字，解析不超过 150 字，不要重复题干。\n"
        "3. knowledge_points 只能从以下专题中选 1-2 个：" + "、".join(CHEM_TOPICS) + "。\n"
        "4. score 为 8-20 的整数。\n"
        f"5. 这是第 {idx + 1}/{total} 道，尽量与其它题不同。\n\n素材：\n{text}"
    )
    if q_type.strip():
        user += f"\n题型偏好：{q_type.strip()}"
    if difficulty:
        user += f"\n难度：{difficulty}/5"
    try:
        content, usage = _chat(db, [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ], temperature=0.35, max_tokens=16000, json_mode=True, timeout=260.0)
        got = _questions_payload(_repair_json(content) or content)
        return got[0] if got else None
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001
        return None


def generate_questions(db: Session, source: str, count: int = 5, q_type: str = "",
                       difficulty: int = 0) -> dict:
    """按题目素材/文献片段生成若干道完整竞赛题（直接进试卷，不写题库）。

    并发下单：每题一次独立调用（各用自己的 DB 会话），比「一次要 N 道」快且不容易被截断。
    """
    text = (source or "").strip()
    if not text:
        raise HTTPException(400, "没有可用的题目素材：先在「题干文字」里粘贴题目，或选中一段文献/裁剪题图。")
    if len(text) > 20000:
        text = text[:20000]
    n = max(1, min(12, int(count or 5)))
    from ..db import SessionLocal

    def run(i: int):
        d = SessionLocal()
        try:
            return _gen_one(d, text, q_type, difficulty, i, n)
        finally:
            d.close()

    if n == 1:
        items = [run(0)]
    else:
        with ThreadPoolExecutor(max_workers=min(3, n)) as ex:
            items = list(ex.map(run, range(n)))
    questions = [q for q in items if q]
    if not questions:
        raise HTTPException(502, "AI 没有返回可用的题目，请稍后重试或减少题数")
    questions.sort(key=lambda q: q.get("score") or 0, reverse=True)
    return {"questions": questions[:n], "model": "", "usage": {},
            "requested": n, "returned": len(questions)}


def document_brief(db: Session, source: str, count: int = 2, *, level: str = "",
                   focus: str = "") -> dict:
    """文献解读：标题 + 摘要 + 核心要点 + 关键段落（原文/译文）+ 改编题。

    对应「化竞智卷」的文献面板：先把一篇文献读薄，再据它出题。
    """
    text = (source or "").strip()
    if not text:
        raise HTTPException(400, "请选择一份有文字层的 PDF，或先粘贴文献文字。")
    if len(text) > 24000:
        text = text[:24000]
    n = max(0, min(8, int(count or 0)))
    level_text = (level or "").strip() or "高中联赛（预赛/初赛）"
    system = (
        "你是化学文献分析专家和化学竞赛命题专家，用简体中文输出，公式用 LaTeX（$...$）。"
        "只输出一个 JSON 对象，不要任何解释。"
    )
    user = (
        "请阅读这篇文献并输出：\n"
        "1) title：文献标题（中文译名）；summary：300 字以内中文摘要；"
        "key_points：5-8 条与反应机理、催化循环、关键中间体/过渡态、结构-活性关系、"
        "关键反应条件有关的中文要点；不要列参考文献、致谢、作者单位、补充材料索引；\n"
        "2) excerpts：挑出 3-6 个真正讲机理的段落（含图注/结构描述），original 为原文摘录，"
        "translation 为中文翻译，section 为所在章节（如「结果与讨论」「机理」）；"
        "不要选参考文献列表；\n"
        "2b) mechanism_figures：文中与机理有关的图片/结构图说明，如「Figure 2 催化循环与中间体」，"
        "只写图注/正文里明确提到的，最多 6 条；没有就空数组；\n"
        f"3) questions：基于文献内容改编 {n} 道适合【{level_text}】的原创竞赛题"
        "（含 q_type/knowledge_points/stem/options/answer/analysis/score；"
        "选择题 4 个选项以 \"A. \" 开头，否则 options 为空数组）。\n"
        + (f"重点关注：{focus.strip()[:500]}\n" if focus.strip() else "")
        + '输出格式：{"title": "", "summary": "", "key_points": [""], '
        '"mechanism_figures": [""], '
        '"excerpts": [{"section": "", "original": "", "translation": ""}], '
        '"questions": [{"q_type": "", "knowledge_points": [], "stem": "", "options": [], '
        '"answer": "", "analysis": "", "score": 10}]}\n\n'
        f"文献文字：\n{text}"
    )
    content, usage = _chat(db, [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], temperature=0.3, max_tokens=4500, json_mode=True, timeout=300.0)
    try:
        data = _json_from_text(content)
    except HTTPException:
        data = {}
    if not isinstance(data, dict):
        data = {}
    key_points = data.get("key_points") or []
    if isinstance(key_points, str):
        key_points = [x.strip() for x in re.split(r"[；;\n]", key_points) if x.strip()]
    mech_figs = data.get("mechanism_figures") or []
    if isinstance(mech_figs, str):
        mech_figs = [x.strip() for x in re.split(r"[；;]", mech_figs) if x.strip()]
    excerpts = []
    for item in data.get("excerpts") or []:
        if not isinstance(item, dict):
            continue
        original = str(item.get("original") or "").strip()
        if not original:
            continue
        excerpts.append({
            "section": str(item.get("section") or "").strip()[:40],
            "original": original[:1200],
            "translation": str(item.get("translation") or "").strip()[:1200],
        })
    questions = _questions_payload(json.dumps(
        {"questions": data.get("questions") or []}, ensure_ascii=False))
    return {
        "title": str(data.get("title") or "").strip()[:160],
        "summary": str(data.get("summary") or "").strip()[:1200],
        "key_points": [str(x).strip()[:400] for x in key_points if str(x).strip()][:8],
        "mechanism_figures": [str(x).strip()[:300] for x in mech_figs if str(x).strip()][:6],
        "excerpts": excerpts[:6],
        "questions": questions[:n] if n else [],
        "usage": usage,
        "model": usage.get("model"),
    }


def _local_split_paper(raw: str) -> dict:
    """模型不可用时的兜底：按「1. / 第1题」粗拆，保证整卷导入不直接报错。"""
    lines = [ln.strip() for ln in raw.splitlines()]
    starts: list[tuple[int, str]] = []
    for ln in lines:
        m = re.match(r"^\s*(?:第\s*)?(\d{1,2})\s*(?:题|[.、．])\s*(.*)$", ln)
        if m:
            num = int(m.group(1))
            if 1 <= num <= 99:
                starts.append((num, ln))
    if not starts:
        raise HTTPException(502, "没能从这份文件里认出题号，请换清晰版或改用「按题号裁图」")
    first_line = next((ln for ln in lines if ln), "")
    questions = []
    for i, (num, head) in enumerate(starts):
        idx = lines.index(head)
        nxt = lines.index(starts[i + 1][1]) if i + 1 < len(starts) else len(lines)
        chunk = "\n".join(x for x in lines[idx:nxt] if x)[:6000]
        questions.append({
            "q_type": "综合", "stem": chunk, "answer": "", "analysis": "",
            "knowledge_points": [], "score": 10, "difficulty": 2,
        })
    return {"title": first_line[:120], "questions": questions, "usage": {}, "model": "local"}


def parse_paper(db: Session, text: str) -> dict:
    """把整张试卷的文字（题目 + 答案）拆成结构化题目，并自动分类。

    模型不可用/返回不合法时退化为本地按题号粗拆，避免整卷导入直接失败。
    """
    raw = (text or "").strip()
    if not raw:
        raise HTTPException(400, "文件里没有可读文字（扫描件也识别失败），请换清晰版或先裁剪成图片题。")
    if len(raw) > 24000:
        raw = raw[:24000]
    system = (
        '你是化学竞赛试卷整理助手。请把给定试卷文字拆成逐题结构化数据，题目与答案要对应起来。'
        '只输出 JSON：{"title": "试卷标题", "questions": [{"q_type": "综合", '
        '"stem": "题干（保留小题号）", "answer": "该题答案", "analysis": "", '
        '"knowledge_points": ["有机化学"], "score": 10}]}\n'
        "要求：\n"
        "1. 输入可能是一份合卷，也可能是「题目卷 + 答案卷」多份文档（有 === 文档：… === 分隔）；"
        "按试卷题号拆题、把答案卷里的答案配对到对应题目；同一大题号下的 1-1、1-2、1-3… 必须合并成一道题（保留全部小问），不同大题号才分开。\n"
        "2. 找不到答案的题 answer 留空字符串；不要编造答案。\n"
        "3. knowledge_points 从这些专题里选 1-2 个：" + "、".join(CHEM_TOPICS) + "。\n"
        "4. q_type 用「综合」「计算」「简答」「填空」「选择」等；竞赛大题优先「综合」。\n"
        "5. 忽略页眉页脚、考试说明、分数框等噪声。"
    )
    try:
        content, usage = _chat(db, [
            {"role": "system", "content": system},
            {"role": "user", "content": raw},
        ], temperature=0.1, max_tokens=5000, json_mode=True)
    except HTTPException:
        return _local_split_paper(raw)
    try:
        data = _json_from_text(content)
    except HTTPException:
        data = {}
    questions = _questions_payload(json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else content)
    if not questions:
        return _local_split_paper(raw)
    title = str(data.get("title") or "").strip()[:120] if isinstance(data, dict) else ""
    return {"title": title, "questions": questions, "usage": usage, "model": usage.get("model")}


def pdf_to_text(path, max_pages: int = 30) -> str:
    """导入试卷用：逐页取文字层，扫描页自动 OCR。"""
    parts: list[str] = []
    with fitz.open(str(path)) as doc:
        for i in range(min(len(doc), max_pages)):
            page = doc.load_page(i)
            lines, _w, _h = _page_lines(page)
            if not lines:
                try:
                    lines = _ocr_page(page)
                except Exception:  # noqa: BLE001
                    lines = []
            if lines:
                parts.append(f"\n=== 第 {i + 1} 页 ===\n" + "\n".join(x["text"] for x in lines))
    return "\n".join(parts).strip()


# ---------------------------------------------------------------- 题目归类 / 解析
_TOPIC_WORDS = {
    "有机化学": ["合成", "反应机理", "中间体", "芳香", "取代", "加成", "消除",
                 "手性", "构型", "官能团", "酯", "醇", "醛", "酮", "苯环", "NMR"],
    "高分子化学": ["聚合物", "单体", "聚合", "分子量", "链段", "玻璃化", "交联"],
    "晶体化学": ["晶胞", "晶格", "密堆积", "配位数", "空间群", "XRD", "晶体结构", "衍射"],
    "无机综合与结构推断": ["元素推断", "离子", "配位", "颜色反应", "沉淀", "配合物"],
    "方程式与元素化学": ["配平", "氧化还原", "电极电势", "化学式", "反应式"],
    "热力学与化学平衡": ["焓", "熵", "吉布斯", "平衡常数", "Kp", "相图", "自由能"],
    "电化学": ["电池", "电解", "电动势", "循环伏安", "电极", "充电", "放电"],
    "动力学": ["速率", "活化能", "半衰期", "反应级数", "催化", "过渡态"],
    "分析化学": ["滴定", "误差", "色谱", "分光光度", "标准曲线", "检出限"],
}


def heuristic_classify(text: str, *, q_types: list[str] | None = None,
                       topics: list[str] | None = None,
                       default_q_type: str = "简答") -> dict:
    """没有 Key / AI 失败时的兜底：按关键词猜专题，按提问方式猜题型。"""
    src = (text or "").strip()
    topics = topics or CHEM_TOPICS
    q_types = q_types or ["单选", "多选", "判断", "填空", "简答", "计算", "证明",
                          "论述", "案例分析", "翻译", "文献阅读", "其他"]
    scores = {}
    for topic, words in _TOPIC_WORDS.items():
        if topic not in topics:
            continue
        scores[topic] = sum(src.count(w) for w in words)
    picked = [t for t, n in sorted(scores.items(), key=lambda kv: -kv[1])[:3] if n > 0]

    qtype = default_q_type
    if "证明" in src and "证明" in q_types:
        qtype = "证明"
    elif any(w in src for w in ("计算", "求 ", "求$", "浓度", "产率", "摩尔", "pH")) and "计算" in q_types:
        qtype = "计算"
    elif any(w in src for w in ("实验", "操作", "现象", "装置")) and "简答" in q_types:
        qtype = "简答"
    elif any(w in src for w in ("翻译", "译成", "译为")) and "翻译" in q_types:
        qtype = "翻译"
    elif "____" in src or "填空" in src:
        qtype = "填空" if "填空" in q_types else qtype
    elif ("A." in src and "B." in src and "C." in src) and "单选" in q_types:
        qtype = "多选" if ("多选" in src or "至少" in src) else "单选"
    elif any(w in src for w in ("判断", "是否正确", "对错")) and "判断" in q_types:
        qtype = "判断"

    difficulty = 2
    if len(src) > 260 or any(w in src for w in ("推导", "证明", "合成路线", "机理", "设计实验")):
        difficulty = 3
    elif qtype in ("填空", "判断") and len(src) < 80:
        difficulty = 1
    reason = "按关键词自动归类" + (f"（命中：{'、'.join(picked)}）" if picked else "（未命中专题词）")
    return {"q_type": qtype, "difficulty": difficulty, "topics": picked,
            "reason": reason, "model": "heuristic", "usage": {}}


def classify_question(db: Session, text: str, *, q_types: list[str] | None = None,
                      topics: list[str] | None = None,
                      default_q_type: str = "简答") -> dict:
    """让模型判断题型、难度与化学专题；调用方负责在失败时用 heuristic_classify 兜底。"""
    src = (text or "").strip()
    if not src:
        raise HTTPException(400, "没有可归类的题干文字")
    topic_pool = topics or CHEM_TOPICS
    type_pool = q_types or ["单选", "多选", "判断", "填空", "简答", "计算", "证明",
                            "论述", "案例分析", "翻译", "文献阅读", "其他"]
    system = (
        "你是高校化学题库的教研员。根据题目文字判断：题型、难度（1 基础 / 2 中等 / 3 较难）、"
        "所属化学专题。只输出 JSON，不要解释。"
    )
    user = (
        f"可选题型：{'、'.join(type_pool)}\n"
        f"可选专题：{'、'.join(topic_pool)}\n"
        "输出 JSON：{\"q_type\": \"...\", \"difficulty\": 1/2/3, "
        "\"topics\": [\"最多3个专题\"], \"reason\": \"简短中文理由\"}\n\n"
        "题目文字：\n" + src[:6000]
    )
    content, usage = _chat(db, [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], temperature=0, max_tokens=400, json_mode=True)
    data = _json_from_text(content)
    qtype = str(data.get("q_type") or "").strip()
    if qtype not in type_pool:
        qtype = default_q_type if default_q_type in type_pool else type_pool[0]
    try:
        difficulty = int(data.get("difficulty"))
    except Exception:                                        # noqa: BLE001
        difficulty = 2
    difficulty = max(1, min(3, difficulty))
    picked = []
    raw_topics = data.get("topics")
    if isinstance(raw_topics, list):
        for t in raw_topics:
            t = str(t or "").strip()
            if t in topic_pool and t not in picked:
                picked.append(t)
            if len(picked) >= 3:
                break
    return {"q_type": qtype, "difficulty": difficulty, "topics": picked,
            "reason": str(data.get("reason") or "").strip()[:200],
            "model": usage.get("model"), "usage": usage}


def question_analysis(db: Session, stem: str, answer: str = "", q_type: str = "") -> dict:
    """为一道题生成中文解析：思路 / 解答 / 易错点。"""
    src = (stem or "").strip()
    if not src:
        raise HTTPException(400, "题干为空，无法生成解析")
    extra = f"\n题型：{q_type}" if q_type else ""
    if (answer or "").strip():
        extra += f"\n参考答案：{answer.strip()[:3000]}"
    content, usage = _chat(db, [
        {"role": "system", "content": (
            "你是一名化学竞赛命题教师。请为题目写一段中文解析，依次给出："
            "【思路】解题切入点；【解答】关键步骤与结论；【易错点】1-3 条。"
            "保留公式、单位、化学式，不要输出 Markdown 表格。")},
        {"role": "user", "content": f"题目：{src[:6000]}{extra}"},
    ], temperature=0.3, max_tokens=1800)
    return {"text": content, "usage": usage, "model": usage.get("model")}
