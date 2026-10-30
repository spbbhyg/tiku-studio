"""把「出题画布」的版面 JSON 渲染成一页 A4 图片。

版面格式（前端 Authoring.vue 生成，单位 pt）::

    {
      "width": 595, "height": 842,
      "blocks": [
        {"type": "image", "path": "questions/2026-10/xxx.png", "x": 48, "y": 60, "w": 480},
        {"type": "text",  "text": "设 $f(x)=x^2$，求……", "x": 48, "y": 300, "w": 480,
         "size": 12, "align": "left", "bold": false, "line": 1.5}
      ]
    }
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from pathlib import Path

import pymupdf

from ..config import LIBRARY_DIR
from .authoring_export import image_path, math_png, rich_segments

PAGE_W, PAGE_H = 595.0, 842.0
FONT = Path(r"C:\Windows\Fonts\Deng.ttf")
FONT_B = Path(r"C:\Windows\Fonts\Dengb.ttf")

_NUM = (int, float)


def _text_width(font: pymupdf.Font, text: str, size: float) -> float:
    return font.text_length(text, fontsize=size) if text else 0.0


def _wrap_runs(runs: list[tuple[str, object]], font: pymupdf.Font, size: float,
               avail: float) -> list[list[tuple[str, object, float]]]:
    """按可用宽度把 runs 折行，返回 [[(kind, payload, width), …], …]。"""
    lines: list[list[tuple[str, object, float]]] = [[]]
    used = 0.0
    for kind, payload in runs:
        if kind == "text":
            chars = list(str(payload))
            buf = ""
            for ch in chars:
                w = _text_width(font, ch, size)
                if used + w > avail and (buf or lines[-1]):
                    if buf:
                        bw = _text_width(font, buf, size)
                        lines[-1].append(("text", buf, bw))
                        used += bw
                        buf = ""
                    lines.append([])
                    used = 0.0
                if ch == "\n":
                    if buf:
                        bw = _text_width(font, buf, size)
                        lines[-1].append(("text", buf, bw))
                        used += bw
                        buf = ""
                    lines.append([])
                    used = 0.0
                    continue
                buf += ch
            if buf:
                bw = _text_width(font, buf, size)
                lines[-1].append(("text", buf, bw))
                used += bw
        else:  # math
            r = math_png(str(payload), size)
            if not r:
                txt = str(payload)
                tw = _text_width(font, txt, size)
                if used + tw > avail and lines[-1]:
                    lines.append([])
                    used = 0.0
                lines[-1].append(("text", txt, tw))
                used += tw
                continue
            _, ratio, h_pt = r
            w = h_pt * ratio
            if used + w > avail and lines[-1]:
                lines.append([])
                used = 0.0
            lines[-1].append(("math", (str(payload), w, h_pt), w))
            used += w
    return [ln for ln in lines if ln] or [[]]


def _draw_text(page: pymupdf.Page, block: dict) -> None:
    size = float(block.get("size") or 12)
    size = max(6.0, min(48.0, size))
    x = float(block.get("x") or 48)
    y = float(block.get("y") or 48)
    x = max(0.0, min(PAGE_W - 24, x))
    width = float(block.get("w") or (PAGE_W - 2 * x))
    width = max(60.0, min(PAGE_W - 2 * x, width))
    align = block.get("align") if block.get("align") in ("left", "center", "right") else "left"
    line_h = size * float(block.get("line") or 1.55)
    bold = bool(block.get("bold"))
    font_name = "cnb" if bold else "cn"
    font = pymupdf.Font(fontfile=str(FONT_B if bold else FONT))

    runs = rich_segments(block.get("text") or "")
    for line in _wrap_runs(runs, font, size, width):
        total = sum(w for _, _, w in line)
        if align == "center":
            cx = x + max(0.0, (width - total) / 2)
        elif align == "right":
            cx = x + max(0.0, width - total)
        else:
            cx = x
        base = y + size * 0.98
        for kind, payload, w in line:
            if kind == "text":
                if payload:
                    page.insert_text((cx, base), payload, fontname=font_name,
                                     fontsize=size, color=(0.06, 0.07, 0.09))
            else:
                latex, iw, ih = payload
                r = math_png(latex, size)
                if r:
                    png, ratio, h_pt = r
                    top = base - h_pt + size * 0.22
                    rect = pymupdf.Rect(cx, top, cx + h_pt * ratio, top + h_pt)
                    page.insert_image(rect, stream=png)
            cx += w
        y += line_h
    block = dict(block)
    block["_bottom"] = y


def _block_bottom(block: dict) -> float:
    if block.get("type") == "image":
        w = float(block.get("w") or 400)
        h = float(block.get("h") or 0)
        if not h:
            p = image_path(block.get("path"))
            if p:
                try:
                    from PIL import Image as PILImage

                    with PILImage.open(p) as im:
                        h = w * im.height / max(1, im.width)
                except Exception:
                    h = w * 0.4
            else:
                h = w * 0.4
        return float(block.get("y") or 0) + h
    text = block.get("text") or ""
    size = float(block.get("size") or 12)
    width = float(block.get("w") or (PAGE_W - 2 * float(block.get("x") or 48)))
    approx = max(1, int(len(text) / max(1.0, width / (size * 0.98))) + 1)
    return float(block.get("y") or 0) + approx * size * 1.55


def render_layout(layout: dict, dpi: int = 200) -> dict:
    """渲染版面 → {path(相对 data/library), abs, width, height}。"""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_font(fontname="cn", fontfile=str(FONT))
    page.insert_font(fontname="cnb", fontfile=str(FONT_B))

    for block in (layout or {}).get("blocks", []):
        kind = block.get("type")
        if kind == "image":
            p = image_path(block.get("path"))
            if not p:
                continue
            x = float(block.get("x") or 40)
            y = float(block.get("y") or 40)
            w = float(block.get("w") or 400)
            h = float(block.get("h") or 0)
            if not h:
                try:
                    from PIL import Image as PILImage

                    with PILImage.open(p) as im:
                        h = w * im.height / max(1, im.width)
                except Exception:
                    h = w * 0.4
            rect = pymupdf.Rect(x, y, x + w, y + h)
            page.insert_image(rect, filename=str(p), keep_proportion=True)
        elif kind == "text":
            _draw_text(page, block)

    stamp = datetime.now().strftime("%H%M%S")
    sub = datetime.now().strftime("%Y-%m")
    rel_dir = Path("questions") / sub
    (LIBRARY_DIR / rel_dir).mkdir(parents=True, exist_ok=True)
    name = f"{stamp}-{uuid.uuid4().hex[:8]}.png"
    abs_path = LIBRARY_DIR / rel_dir / name
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    pix.save(str(abs_path))
    doc.close()
    return {
        "path": str(rel_dir / name).replace("\\", "/"),
        "abs": str(abs_path),
        "width": pix.width,
        "height": pix.height,
    }


def layout_hash(layout: dict) -> str:
    raw = repr(sorted((layout or {}).get("blocks", []), key=lambda b: (b.get("type"), b.get("y"))))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
