"""把纯文本题目渲染成题图（题库里跟 Chemy 一样直接看图）。"""
from __future__ import annotations

import math

from sqlalchemy.orm import Session

from ..models import Question
from . import compose_render, versions as version_svc

MARGIN = 48.0
WIDTH = 595.0


def _lines(text: str, size: float) -> int:
    per = max(8, int((WIDTH - 2 * MARGIN) / (size * 0.98)))
    total = 0
    for seg in (text or "").split("\n"):
        total += max(1, math.ceil(len(seg) / per))
    return total


def _layout_for(q: Question) -> dict:
    """按题干 + 选项估一个合适的页高，生成版面 JSON。"""
    stem = (q.stem or "").strip()
    opts = [str(o).strip() for o in (q.options or []) if str(o).strip()]
    body = stem + ("\n" + "\n".join(opts) if opts else "")
    size = 12.0
    n = _lines(body, size)
    top = 26.0
    height = min(760.0, max(120.0, top + n * size * 1.62 + 26.0))
    return {
        "width": WIDTH,
        "height": height,
        "blocks": [
            {"type": "text", "text": body, "x": MARGIN, "y": MARGIN,
             "w": WIDTH - 2 * MARGIN, "size": size, "align": "left", "bold": False},
        ],
    }


def render_question(session: Session, q: Question, note: str = "生成题图",
                    force: bool = False) -> dict:
    """给题目渲染一张题图并写回 images；已有题干图则跳过（force=True 时重做）。"""
    if not (q.stem or "").strip() and not (q.options or []):
        return {"id": q.id, "skipped": True, "reason": "纯图片题，无题干文本"}

    imgs = list(q.images or [])
    has_stem = any((i.get("caption") or "") != "答案" for i in imgs)
    if has_stem and not force:
        return {"id": q.id, "skipped": True}
    if has_stem and force:
        imgs = [i for i in imgs if (i.get("caption") or "") == "答案"]

    layout = _layout_for(q)
    layout["trim"] = True
    layout["trim_pad"] = 18
    out = compose_render.render_layout(layout, dpi=200)
    version_svc.take_snapshot(session, q, note=note)
    imgs.append({"path": out["path"], "caption": "题干"})
    q.images = imgs
    q.layout = layout
    return {"id": q.id, "skipped": False, "image": out["path"],
            "width": out["width"], "height": out["height"]}
