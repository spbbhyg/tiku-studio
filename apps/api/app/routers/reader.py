"""精读页接口：重点句/章节导航、句子批注（译文 + 个人批注）、标注版 PDF 导出。

能力来源（吸收两份原型的长处）：
- 重点句 + 章节导航：本地算法（services/key_sentences.py），打开文献即出，不烧 token；
- 译文 / 批注：逐句生成并落库，刷新页面不丢；整篇可一键「翻译全部」；
- 标注版 PDF：在原文上画下划线 + 圈号，再附「重点句译文附录」，按阅读设置排版；
- 「据此出题」：把选中的重点句直接交给 AI 命题，或跳转到智能出题页。
"""
from __future__ import annotations

from urllib.parse import quote

import fitz
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import LIBRARY_DIR
from ..db import get_db
from ..models import ReaderNote
from ..services import ai as ai_svc
from ..services import key_sentences as ks

router = APIRouter(prefix="/api/reader", tags=["reader"])

MARK_COLOR = (0.145, 0.388, 0.922)          # #2563eb
TEXT_COLOR = {"ink": (0.06, 0.09, 0.165), "blue": (0.114, 0.306, 0.847),
              "gray": (0.278, 0.333, 0.412)}
FONT_SIZE_PT = {"sm": 9.5, "md": 11.0, "lg": 13.0}


def _doc_path(doc_id: str):
    try:
        return ks._doc_file(doc_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


# ---------------------------------------------------------------- 重点句 / 章节
@router.get("/doc/{doc_id}/outline")
def doc_outline(doc_id: str, db: Session = Depends(get_db)):
    """整篇的重点句、章节结构与已存批注（本地识别，不走 AI）。"""
    _doc_path(doc_id)
    try:
        data = ks.outline(doc_id)
    except HTTPException:
        raise
    except Exception as exc:                                 # noqa: BLE001
        raise HTTPException(500, f"重点句识别失败：{exc}") from exc
    rows = db.scalars(select(ReaderNote).where(ReaderNote.doc_id == doc_id)).all()
    data["sections"] = data.get("sections") or []
    data["sentences"] = data.get("sentences") or []
    data["notes"] = {r.sentence_key: {
        "translation": r.translation or "",
        "note": r.note or "",
        "page_no": r.page_no,
    } for r in rows}
    return data


# ---------------------------------------------------------------- 批注
class NoteIn(BaseModel):
    doc_id: str = Field(min_length=1, max_length=64)
    sentence_key: str = Field(min_length=1, max_length=64)
    page_no: int | None = None
    source: str = ""
    translation: str = ""
    note: str = ""


class TranslateSentenceIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    target: str = "简体中文"


@router.get("/doc/{doc_id}/notes")
def list_notes(doc_id: str, db: Session = Depends(get_db)):
    """这篇文献已保存的译文与批注（按句子键索引）。"""
    rows = db.scalars(select(ReaderNote).where(ReaderNote.doc_id == doc_id)).all()
    return {"notes": {r.sentence_key: {
        "translation": r.translation or "",
        "note": r.note or "",
        "page_no": r.page_no,
    } for r in rows}}


@router.post("/notes")
def save_note(body: NoteIn, db: Session = Depends(get_db)):
    """保存一句的译文 / 批注（upsert）。"""
    row = db.scalar(select(ReaderNote).where(
        ReaderNote.doc_id == body.doc_id, ReaderNote.sentence_key == body.sentence_key))
    if row is None:
        row = ReaderNote(doc_id=body.doc_id, sentence_key=body.sentence_key)
        db.add(row)
    row.page_no = body.page_no
    row.source = (body.source or "")[:4000] or row.source
    row.translation = body.translation or ""
    row.note = body.note or ""
    db.commit()
    return {"ok": True}


@router.delete("/notes")
def clear_notes(doc_id: str, db: Session = Depends(get_db)):
    for row in db.scalars(select(ReaderNote).where(ReaderNote.doc_id == doc_id)).all():
        db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/translate")
def translate_sentence(body: TranslateSentenceIn, db: Session = Depends(get_db)):
    """把一句（或一段选中文字）译成中文，返回译文；不落库，由前端决定存不存。"""
    return ai_svc.translate(db, body.text, body.target)


# ---------------------------------------------------------------- 标注版 PDF
class ExportIn(BaseModel):
    doc_id: str
    items: list[dict] = Field(default_factory=list)
    font_size: str = "md"
    color: str = "ink"
    line_height: float = 1.6
    underline: bool = True
    appendix: bool = True


def _wrap(text: str, font: "fitz.Font", size: float, width: float) -> list[str]:
    """按字符宽度折行；中英文混排都按字形宽度累计。"""
    lines: list[str] = []
    current = ""
    for ch in text:
        if ch == "\n":
            lines.append(current)
            current = ""
            continue
        if font.text_length(current + ch, fontsize=size) > width and current:
            lines.append(current)
            current = ch
        else:
            current += ch
    if current:
        lines.append(current)
    return lines


@router.post("/export")
def export_annotated(body: ExportIn):
    """在原 PDF 上标注重点句，并（可选）追加译文附录，返回 PDF 字节流。"""
    path = _doc_path(body.doc_id)
    if not body.items:
        raise HTTPException(400, "还没有重点句可导出")
    size = FONT_SIZE_PT.get(body.font_size, FONT_SIZE_PT["md"])
    color = TEXT_COLOR.get(body.color, TEXT_COLOR["ink"])
    try:
        doc = fitz.open(str(path))
    except Exception as exc:                                 # noqa: BLE001
        raise HTTPException(400, f"无法打开 PDF：{exc}") from exc

    with doc:
        badges: dict[int, list[tuple["fitz.Rect", str]]] = {}
        for item in body.items[:200]:
            try:
                page_no = int(item.get("page") or 0)
            except (TypeError, ValueError):
                continue
            if not 1 <= page_no <= len(doc):
                continue
            page = doc.load_page(page_no - 1)
            width, height = page.rect.width, page.rect.height
            rects = item.get("rects") or []
            first = None
            for raw in rects:
                try:
                    x0, y0, x1, y1 = [float(v) for v in raw[:4]]
                except (TypeError, ValueError):
                    continue
                rect = fitz.Rect(x0 * width, y0 * height, x1 * width, y1 * height)
                if first is None:
                    first = rect
                if body.underline:
                    page.draw_line(fitz.Point(rect.x0, rect.y1 - 0.6),
                                   fitz.Point(rect.x1, rect.y1 - 0.6),
                                   color=MARK_COLOR, width=1.2)
            number = item.get("number")
            if first is not None and number is not None:
                badges.setdefault(page_no, []).append((first, str(number)))

        for page_no, marks in badges.items():
            _draw_badges(doc.load_page(page_no - 1), marks)

        if body.appendix:
            entries = [i for i in body.items if str(i.get("translation") or "").strip()]
            if entries:
                _append_pages(doc, entries, size, color, body.line_height)

        data = doc.tobytes(garbage=3, deflate=True)

    name = f"{path.stem}-标注版.pdf"
    return Response(
        data, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


def font_size_label(text: str) -> float:
    return 2.2 * len(text)


# 中文附录优先嵌入系统字体：PyMuPDF 内置的 china-s 会把拉丁字符按全角排版，
# 中英混排会溢出页面，实测 msyh / simsun 才与 text_length 的度量一致。
CJK_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/Deng.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]


def _cjk_font() -> tuple["fitz.Font", str | None]:
    """返回 (字体对象, 字体文件路径)；找不到系统字体时退回内置 china-s。"""
    import os
    for path in CJK_FONT_CANDIDATES:
        if os.path.isfile(path):
            try:
                return fitz.Font(fontfile=path), path
            except Exception:                                # noqa: BLE001
                continue
    return fitz.Font("china-s"), None


def _draw_badges(page: "fitz.Page", marks: list[tuple["fitz.Rect", str]]) -> None:
    """圈号统一画在左侧页边空白，用细引线指到句子起点；同一行有多句时上下错开，
    避免盖住正文（原来的做法是压在句首文字上，句子密时会糊成一团）。"""
    if not marks:
        return
    page_h = page.rect.height
    marks = sorted(marks, key=lambda m: (round(m[0].y0, 1), m[0].x0))
    rows: list[list[tuple["fitz.Rect", str]]] = []
    for mark in marks:
        if rows and abs(rows[-1][0][0].y0 - mark[0].y0) < 3:
            rows[-1].append(mark)
        else:
            rows.append([mark])

    radius = 6.2
    cx = 34.0
    for row in rows:
        mid = sum((m[0].y0 + m[0].y1) / 2 for m in row) / len(row)
        gap = 14.0
        for i, (rect, label) in enumerate(row):
            cy = mid + (i - (len(row) - 1) / 2) * gap
            cy = min(max(cy, radius + 6), page_h - radius - 6)
            tx = max(cx + radius + 3, rect.x0 - 3)
            try:
                page.draw_line(fitz.Point(cx + radius, cy), fitz.Point(tx, mid),
                               color=(0.62, 0.72, 0.92), width=0.6)
            except Exception:                              # noqa: BLE001
                pass
            page.draw_circle(fitz.Point(cx, cy), radius, color=MARK_COLOR, fill=MARK_COLOR)
            half = fitz.get_text_length(label, fontname="helv", fontsize=7) / 2
            page.insert_text(fitz.Point(cx - half, cy + 2.4), label,
                             fontsize=7, fontname="helv", color=(1, 1, 1))


def _append_pages(doc: "fitz.Document", entries: list[dict], size: float,
                  color: tuple[float, float, float], line_height: float) -> None:
    """追加「重点句译文附录」：标题 + 蓝线 + 每条的编号/原文/译文，自动分页。"""
    font, font_path = _cjk_font()
    fontname = "appfont"
    page_w, page_h = 595.28, 841.89
    margin_x, margin_top, margin_bottom = 56.0, 64.0, 64.0
    content_w = page_w - margin_x * 2

    def new_page() -> "fitz.Page":
        nonlocal fontname
        page = doc.new_page(width=page_w, height=page_h)
        if font_path:
            page.insert_font(fontname=fontname, fontfile=font_path)
        else:
            fontname = "china-s"
        return page

    page = new_page()
    y = margin_top
    page.insert_text(fitz.Point(margin_x, y), "重点句译文附录",
                     fontsize=size * 1.35, fontname=fontname, color=(0.06, 0.09, 0.165))
    y += size * 1.9
    page.draw_line(fitz.Point(margin_x, y), fitz.Point(page_w - margin_x, y),
                   color=MARK_COLOR, width=2.4)
    y += size * 1.8

    def ensure(space: float) -> None:
        nonlocal page, y
        if y + space <= page_h - margin_bottom:
            return
        page = new_page()
        y = margin_top

    def write(lines: list[str], pt: float, rgb: tuple[float, float, float]) -> None:
        nonlocal y
        step = pt * line_height
        for line in lines:
            ensure(step)
            page.insert_text(fitz.Point(margin_x, y), line, fontsize=pt,
                             fontname=fontname, color=rgb)
            y += step

    for entry in entries[:200]:
        label = str(entry.get("label") or f"[{entry.get('number')}]")
        label += f"　第 {entry.get('page')} 页"
        source = str(entry.get("source") or "")
        translation = str(entry.get("translation") or "")
        ensure(size * 4)
        write([label], size * 0.92, MARK_COLOR)
        if source:
            write(_wrap(source, font, size * 0.82, content_w), size * 0.82, (0.39, 0.45, 0.55))
        write(_wrap(translation, font, size, content_w), size, color)
        y += size * 0.9
