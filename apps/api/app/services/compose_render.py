"""把「出题画布」的版面 JSON 渲染成一页 A4 图片。

版面格式（前端 Authoring.vue 生成，单位 pt）::

    {
      "width": 595, "height": 842,
      "blocks": [
        {"type": "image", "path": "questions/2026-10/xxx.png", "x": 48, "y": 60, "w": 480},
        {"type": "text",  "text": "设 $f(x)=x^2$，求……", "x": 48, "y": 300, "w": 480,
         "size": 12, "align": "left", "bold": false, "line": 1.5}
      ],
      "meta": {                       # 可选：页眉页脚模板
        "header": {"left": "", "center": "XX 大学 2026 期中考试", "right": ""},
        "footer": {"left": "", "center": "第 1 页", "right": ""},
        "name_line": true             # 是否画「姓名 / 班级 / 得分」栏
      }
    }

图片块可带 ``"rotate": 90``（0/90/180/270），顺时针旋转。
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


def _hex_color(value, default=(0.06, 0.07, 0.09)):
    """把 '#rrggbb' 转成 PyMuPDF 需要的 0~1 RGB；非法值回退默认色。"""
    try:
        v = str(value or "").strip().lstrip("#")
        if len(v) == 3:
            v = "".join(c * 2 for c in v)
        if len(v) != 6:
            return default
        return tuple(int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except Exception:
        return default


def _draw_styled_text(page: pymupdf.Page, block: dict) -> float:
    """逐字符绘制带 runs 的文字块（每个字可单独字号/颜色）。

    runs 形如 ``[{"t": "abc", "s": 14, "c": "#ff0000"}]``；没有 s/c 的段落继承块级设置。
    """
    size = max(6.0, min(48.0, float(block.get("size") or 12)))
    x = max(0.0, min(PAGE_W - 24, float(block.get("x") or 48)))
    y = float(block.get("y") or 48)
    width = max(60.0, min(PAGE_W - 2 * x, float(block.get("w") or (PAGE_W - 2 * x))))
    line = float(block.get("line") or 1.55)
    bold = bool(block.get("bold"))
    font_name = "cnb" if bold else "cn"
    font = pymupdf.Font(fontfile=str(FONT_B if bold else FONT))
    base_color = _hex_color(block.get("color"))

    cx = x
    base = y + size * 0.98
    line_max = size
    bottom = base

    def newline() -> None:
        nonlocal cx, base, line_max, bottom
        base += line_max * line
        cx = x
        line_max = size
        bottom = max(bottom, base)

    for run in block.get("runs") or []:
        if not isinstance(run, dict):
            continue
        try:
            rs = float(run.get("s") or size)
        except (TypeError, ValueError):
            rs = size
        rs = max(6.0, min(48.0, rs))
        color = _hex_color(run.get("c") or block.get("color"), base_color)
        for ch in str(run.get("t") or ""):
            if ch == "\n":
                newline()
                continue
            cw = font.text_length(ch, fontsize=rs)
            if cx + cw > x + width and cx > x:
                newline()
            page.insert_text((cx, base), ch, fontname=font_name, fontsize=rs, color=color)
            cx += cw
            line_max = max(line_max, rs)
            bottom = max(bottom, base)
    return bottom + max(0.0, line_max - size) * 0.4 + 2


def _draw_text(page: pymupdf.Page, block: dict) -> float:
    runs = block.get("runs")
    if isinstance(runs, list) and any(isinstance(r, dict) and str(r.get("t") or "")
                                      for r in runs):
        return _draw_styled_text(page, block)
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
                                     fontsize=size, color=_hex_color(block.get("color")))
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
    return y


def _band(page, parts: dict, y: float, size: float = 9.0, color=(0.35, 0.37, 0.42)) -> None:
    """画一条三段式信息栏（左/中/右）。"""
    font = pymupdf.Font(fontfile=str(FONT))
    margin = 48.0
    for key, text in (parts or {}).items():
        text = (text or "").strip()
        if not text:
            continue
        w = _text_width(font, text, size)
        if key == "center":
            x = (PAGE_W - w) / 2
        elif key == "right":
            x = PAGE_W - margin - w
        else:
            x = margin
        page.insert_text((x, y), text, fontname="cn", fontsize=size, color=color)


def _name_line(page, y: float, size: float = 10.0) -> None:
    """姓名 / 班级 / 得分 栏（带下划线）。"""
    font = pymupdf.Font(fontfile=str(FONT))
    labels = ["姓名", "班级", "得分"]
    slots = 3
    margin = 48.0
    gap = 24.0
    slot_w = (PAGE_W - 2 * margin - gap * (slots - 1)) / slots
    for i, label in enumerate(labels):
        x0 = margin + i * (slot_w + gap)
        text = f"{label}："
        tw = _text_width(font, text, size)
        page.insert_text((x0, y), text, fontname="cn", fontsize=size, color=(0.25, 0.27, 0.32))
        page.draw_line(pymupdf.Point(x0 + tw, y + 2), pymupdf.Point(x0 + slot_w - 6, y + 2),
                       color=(0.55, 0.58, 0.64), width=0.8)


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
    page_h = float((layout or {}).get("height") or PAGE_H)
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=page_h)
    page.insert_font(fontname="cn", fontfile=str(FONT))
    page.insert_font(fontname="cnb", fontfile=str(FONT_B))

    meta = (layout or {}).get("meta") or {}
    _band(page, meta.get("header") or {}, y=44, size=9.5, color=(0.28, 0.30, 0.36))
    if meta.get("name_line"):
        _name_line(page, y=70, size=10.0)
    if (meta.get("header") or {}) or meta.get("name_line"):
        page.draw_line(pymupdf.Point(48, 84), pymupdf.Point(PAGE_W - 48, 84),
                       color=(0.75, 0.78, 0.82), width=0.8)
    if meta.get("footer"):
        _band(page, meta.get("footer") or {}, y=page_h - 30, size=9.0)

    # 有页眉/姓名栏时，正文整体下移，避免和页眉重叠
    dy = 0.0
    has_band = bool(meta.get("header")) or bool(meta.get("name_line"))
    if has_band:
        ys = [float(b.get("y") or 0) for b in (layout or {}).get("blocks", [])
              if b.get("type") in ("image", "text")]
        if ys and min(ys) < 100:
            dy = 100.0 - min(ys)

    content_bottom = 0.0
    for block in (layout or {}).get("blocks", []):
        kind = block.get("type")
        if kind == "image":
            p = image_path(block.get("path"))
            if not p:
                continue
            x = float(block.get("x") or 40)
            y = float(block.get("y") or 40) + dy
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
            rotate = int(block.get("rotate") or 0) % 360
            page.insert_image(rect, filename=str(p), keep_proportion=True,
                              rotate=rotate if rotate else 0)
            content_bottom = max(content_bottom, y + h)
        elif kind == "text":
            shifted = dict(block)
            shifted["y"] = float(block.get("y") or 0) + dy
            content_bottom = max(content_bottom, _draw_text(page, shifted))

    trim = bool((layout or {}).get("trim"))
    if trim:
        pad = float((layout or {}).get("trim_pad") or 24)
        floor_y = 84.0 if (meta.get("header") or meta.get("name_line")) else 0.0
        if meta.get("footer"):
            keep_h = page_h
        else:
            keep_h = min(page_h, max(120.0, max(content_bottom, floor_y) + pad))
        if keep_h < page_h - 1:
            page.set_cropbox(pymupdf.Rect(0, 0, PAGE_W, keep_h))

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
    """版面指纹：块 + 页眉页脚模板都参与，改了模板也算新题目。"""
    layout = layout or {}
    blocks = sorted(layout.get("blocks", []), key=lambda b: (b.get("type"), b.get("y")))
    raw = repr((blocks, layout.get("meta") or {}))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
