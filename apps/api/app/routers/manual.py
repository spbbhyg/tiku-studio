"""文献页图服务：上传 PDF → 渲染页图 → 按矩形裁剪成题图。

最初是「手动拆解（小学教材）」的后端，现在只保留下与业务无关的通用能力：
上传 / 页图 / 裁剪 / 上传图片 / 删除。文献出题页（Authoring.vue）复用它。
完全本地运行，不依赖 AI。
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import fitz
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import LIBRARY_DIR
from ..db import get_db

router = APIRouter(prefix="/api/manual", tags=["pages"])

UPLOAD_DIR = LIBRARY_DIR / "uploads"
QUESTION_IMG_DIR = LIBRARY_DIR / "questions"
PAGE_CACHE_DIR = LIBRARY_DIR / "page-cache"

DOC_ID_RE = re.compile(r"^[0-9a-f]{12}$")
MAX_UPLOAD_MB = 80
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


# ---------------------------------------------------------------- 工具函数
def _rel(path: Path) -> str:
    """相对 data/library 的 POSIX 路径（用作 /api/images/{path}）。"""
    return path.relative_to(LIBRARY_DIR).as_posix()


def _clean_filename(name: str | None) -> str:
    """浏览器/curl 传上来的中文名常被按 latin-1 解码，这里尽量还原成 GBK/UTF-8。"""
    raw = (name or "").strip()
    if not raw:
        return ""
    for enc in ("gbk", "utf-8"):
        try:
            fixed = raw.encode("latin-1").decode(enc)
            if fixed and not any("\ufffd" == ch for ch in fixed):
                return fixed
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return raw


def _doc_file(doc_id: str) -> Path:
    if not DOC_ID_RE.match(doc_id or ""):
        raise HTTPException(400, "非法 doc_id")
    path = UPLOAD_DIR / f"{doc_id}.pdf"
    if not path.exists():
        raise HTTPException(404, "文档不存在或已被清理，请重新上传")
    return path


def _meta_path(doc_id: str) -> Path:
    return UPLOAD_DIR / f"{doc_id}.json"


def _read_meta(doc_id: str) -> dict:
    """上传时的文件名/页数等信息。老文件没有 sidecar 也能用。"""
    try:
        data = json.loads(_meta_path(doc_id).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:                                        # noqa: BLE001
        return {}


def _write_meta(doc_id: str, filename: str, page_count: int) -> None:
    try:
        _meta_path(doc_id).write_text(json.dumps({
            "doc_id": doc_id,
            "filename": filename,
            "page_count": page_count,
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:                                        # noqa: BLE001
        pass


def _save_png(data: bytes, subdir: str = "questions") -> dict:
    now = datetime.now()
    rel_dir = Path(subdir) / now.strftime("%Y-%m")
    abs_dir = LIBRARY_DIR / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    name = f"{now.strftime('%H%M%S')}-{uuid.uuid4().hex[:8]}.png"
    target = abs_dir / name
    target.write_bytes(data)
    return {"path": _rel(target), "bytes": len(data)}


def _image_pdf_page(data: bytes) -> fitz.Document:
    """把单张图片包成一页 PDF，便于统一走后续渲染/裁剪流程。"""
    pix = fitz.Pixmap(data)
    w_pt = pix.width * 72 / 96
    h_pt = pix.height * 72 / 96
    doc = fitz.open()
    page = doc.new_page(width=w_pt, height=h_pt)
    page.insert_image(page.rect, stream=data)
    return doc


# ---------------------------------------------------------------- 上传
@router.post("/upload")
async def upload_doc(file: UploadFile = File(...)):
    """上传 PDF（或单张图片）→ 返回 doc_id 与页数。"""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件为空")
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"文件超过 {MAX_UPLOAD_MB}MB")

    suffix = Path(file.filename or "").suffix.lower()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if suffix in IMAGE_SUFFIXES:
        try:
            pdf_bytes = _image_pdf_page(raw).tobytes()
        except Exception as e:
            raise HTTPException(400, f"无法解析图片：{e}")
    else:
        try:
            with fitz.open(stream=raw, filetype="pdf") as probe:
                if len(probe) == 0:
                    raise ValueError("PDF 没有页面")
        except Exception as e:
            raise HTTPException(400, f"无法解析 PDF：{e}")
        pdf_bytes = raw

    doc_id = uuid.uuid4().hex[:12]
    (UPLOAD_DIR / f"{doc_id}.pdf").write_bytes(pdf_bytes)

    pages = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for i, page in enumerate(doc):
            pages.append({"page_no": i + 1,
                          "width": round(page.rect.width, 1),
                          "height": round(page.rect.height, 1)})
    filename = _clean_filename(file.filename) or "未命名.pdf"
    _write_meta(doc_id, filename, len(pages))
    return {"doc_id": doc_id, "filename": filename,
            "page_count": len(pages),
            "url": f"/api/manual/doc/{doc_id}/page/1.png?zoom=2",
            "pages": pages}


@router.get("/docs")
def list_docs():
    """已上传的 PDF 列表（刷新页面后还能接着用）。"""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for pdf in UPLOAD_DIR.glob("*.pdf"):
        doc_id = pdf.stem
        if not DOC_ID_RE.match(doc_id):
            continue
        meta = _read_meta(doc_id)
        count = int(meta.get("page_count") or 0)
        if not count:
            try:
                with fitz.open(str(pdf)) as doc:
                    count = len(doc)
            except Exception:                                # noqa: BLE001
                count = 0
        stat = pdf.stat()
        out.append({
            "doc_id": doc_id,
            "filename": meta.get("filename") or f"{doc_id}.pdf",
            "page_count": count,
            "bytes": stat.st_size,
            "uploaded_at": meta.get("uploaded_at") or datetime.fromtimestamp(
                stat.st_mtime).isoformat(timespec="seconds"),
        })
    out.sort(key=lambda d: d.get("uploaded_at") or "", reverse=True)
    return {"docs": out}


# ---------------------------------------------------------------- 页图
@router.get("/doc/{doc_id}/page/{page_no}.png")
def page_image(doc_id: str, page_no: int, zoom: float = 2.0):
    """把 PDF 某页渲染成 PNG（带磁盘缓存）。zoom=2 约 144dpi，足够裁题。"""
    if not 1 <= page_no <= 500:
        raise HTTPException(400, "页码越界")
    zoom = max(0.5, min(4.0, float(zoom or 2.0)))
    path = _doc_file(doc_id)

    rel_dir = Path("page-cache") / doc_id
    cache = LIBRARY_DIR / rel_dir / f"p{page_no}-z{zoom:g}.png"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        with fitz.open(str(path)) as doc:
            if page_no > len(doc):
                raise HTTPException(404, "页码超出文档范围")
            page = doc.load_page(page_no - 1)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            pix.save(str(cache))
    return FileResponse(str(cache), media_type="image/png",
                        headers={"Cache-Control": "private, max-age=600"})


# ---------------------------------------------------------------- 文字层
def _page_lines(page: fitz.Page) -> list[dict]:
    """把一页的文字按「行」拆出来，坐标归一化到 0~1，供前端做原生选区。"""
    rect = page.rect
    w = float(rect.width) or 1.0
    h = float(rect.height) or 1.0
    try:
        raw = page.get_text("dict")
    except Exception:                                        # noqa: BLE001
        return []
    out: list[dict] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:                           # 0 = 文本块，1 = 图片
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            text = "".join(str(s.get("text") or "") for s in spans)
            if not text.strip():
                continue
            try:
                x0, y0, x1, y1 = [float(v) for v in line.get("bbox") or []]
            except Exception:                                # noqa: BLE001
                continue
            if x1 <= x0 or y1 <= y0:
                continue
            size = max((float(s.get("size") or 0) for s in spans), default=0.0)
            out.append({
                "text": text,
                "x0": round(x0 / w, 5), "y0": round(y0 / h, 5),
                "x1": round(min(x1, w) / w, 5), "y1": round(min(y1, h) / h, 5),
                "size": round(size, 2),
            })
    return out


@router.get("/doc/{doc_id}/page/{page_no}/text")
def page_text(doc_id: str, page_no: int):
    """某页的文字层（逐行 + 归一化坐标）。扫描版 PDF 会返回空列表。"""
    path = _doc_file(doc_id)
    with fitz.open(str(path)) as doc:
        if not 1 <= page_no <= len(doc):
            raise HTTPException(404, "页码超出文档范围")
        page = doc.load_page(page_no - 1)
        rect = page.rect
        lines = _page_lines(page)
    return {
        "doc_id": doc_id, "page_no": page_no,
        "width": round(float(rect.width), 1), "height": round(float(rect.height), 1),
        "lines": lines,
        "text": "\n".join(line["text"].strip() for line in lines),
        "has_text": bool(lines),
    }


class ExtractTextIn(BaseModel):
    doc_id: str
    page_no: int = Field(ge=1)
    # 归一化矩形列表（0~1）；前端把浏览器选区拆成若干行矩形发过来
    rects: list[list[float]] = Field(default_factory=list, max_length=400)


def _merge_line_rects(rects: list[list[float]]) -> list[list[float]]:
    """把同一行的碎矩形合并，避免逐字/逐段提取时丢空格。"""
    def norm(r):
        x0, y0, x1, y1 = [float(v) for v in r[:4]]
        x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
        y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
        return [x0, y0, x1, y1]

    merged = [norm(r) for r in rects if len(r) >= 4]
    for _ in range(3):
        changed = False
        out: list[list[float]] = []
        for r in sorted(merged, key=lambda b: (b[1], b[0])):
            for i, m in enumerate(out):
                if r[3] <= m[1] or r[1] >= m[3]:             # 垂直不相交
                    continue
                overlap = min(r[3], m[3]) - max(r[1], m[1])
                shorter = min(r[3] - r[1], m[3] - m[1]) or 1e-9
                gap = max(r[0], m[0]) - min(r[2], m[2])
                if overlap / shorter > 0.5 and gap < 0.02:   # 同一行且相邻
                    out[i] = [min(r[0], m[0]), min(r[1], m[1]),
                              max(r[2], m[2]), max(r[3], m[3])]
                    changed = True
                    break
            else:
                out.append(r)
        merged = out
        if not changed:
            break
    return merged


def _snap_to_lines(lines: list[dict], rects: list[list[float]]) -> list[list[float]]:
    """把浏览器给的碎矩形对齐到 PDF 的整行，避免只框到半行时丢词、丢空格。"""
    out: list[list[float]] = []
    for r in rects:
        hit = []
        rh = max(1e-9, r[3] - r[1])
        for ln in lines:
            oy = min(r[3], ln["y1"]) - max(r[1], ln["y0"])
            if oy > 0 and oy / rh > 0.35:
                hit.append(ln)
        if hit:
            out.append([min([r[0]] + [ln["x0"] for ln in hit]),
                        min([r[1]] + [ln["y0"] for ln in hit]),
                        max([r[2]] + [ln["x1"] for ln in hit]),
                        max([r[3]] + [ln["y1"] for ln in hit])])
        else:
            out.append(r)
    return out


@router.post("/extract-text")
def extract_text(body: ExtractTextIn):
    """按选区矩形从 PDF 里取文字（复制/翻译用）。"""
    path = _doc_file(body.doc_id)
    if not body.rects:
        return {"text": "", "rects": [], "chars": 0}
    with fitz.open(str(path)) as doc:
        if not 1 <= body.page_no <= len(doc):
            raise HTTPException(404, "页码超出文档范围")
        page = doc.load_page(body.page_no - 1)
        w, h = float(page.rect.width), float(page.rect.height)
        rects = _merge_line_rects(_snap_to_lines(_page_lines(page), body.rects))
        parts: list[str] = []
        for x0, y0, x1, y1 in rects:
            clip = fitz.Rect(max(0.0, x0 * w - 0.6), max(0.0, y0 * h - 0.6),
                             min(w, x1 * w + 0.6), min(h, y1 * h + 0.6))
            try:
                piece = page.get_text("text", clip=clip) or ""
            except Exception:                                # noqa: BLE001
                piece = ""
            piece = "\n".join(line.rstrip() for line in piece.splitlines() if line.strip())
            if piece:
                parts.append(piece)
    text = "\n".join(parts)
    return {"text": text, "rects": rects, "chars": len(text)}



# ---------------------------------------------------------------- 裁剪
@router.delete("/doc/{doc_id}")
def delete_doc(doc_id: str):
    """删除已上传的原件（连带页面渲染缓存）。已裁剪出的题图不受影响。"""
    path = _doc_file(doc_id)
    path.unlink()
    _meta_path(doc_id).unlink(missing_ok=True)
    cache = PAGE_CACHE_DIR / doc_id
    if cache.exists():
        shutil.rmtree(cache, ignore_errors=True)
    return {"ok": True}


class CropIn(BaseModel):
    doc_id: str
    page_no: int = Field(ge=1)
    # 归一化坐标（0~1，相对整页宽高），左上→右下
    rect: list[float] = Field(min_length=4, max_length=4)
    zoom: float = Field(default=3.0, ge=1.0, le=6.0)
    caption: str | None = None


@router.post("/crop")
def crop_page(body: CropIn):
    """按归一化矩形裁剪 PDF 页面，保存为 PNG 题图。"""
    path = _doc_file(body.doc_id)
    x0, y0, x1, y1 = body.rect
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    if x1 - x0 < 0.005 or y1 - y0 < 0.005:
        raise HTTPException(400, "选区太小，请重新框选")

    with fitz.open(str(path)) as doc:
        if body.page_no > len(doc):
            raise HTTPException(404, "页码超出文档范围")
        page = doc.load_page(body.page_no - 1)
        r = page.rect
        clip = fitz.Rect(r.x0 + x0 * r.width, r.y0 + y0 * r.height,
                         r.x0 + x1 * r.width, r.y0 + y1 * r.height)
        pix = page.get_pixmap(clip=clip, matrix=fitz.Matrix(body.zoom, body.zoom))

    saved = _save_png(pix.tobytes("png"))
    out = {"path": saved["path"], "width": pix.width, "height": pix.height,
           "doc_id": body.doc_id, "page_no": body.page_no}
    if body.caption:
        out["caption"] = body.caption
    return out


class ImageIn(BaseModel):
    data_url: str


@router.post("/image")
def upload_image(body: ImageIn):
    """上传任意图片（前端编辑后的截图 / 手写答案等），返回图库路径。"""
    m = re.match(r"data:image/(\w+);base64,(.+)", body.data_url or "", re.S)
    if not m:
        raise HTTPException(400, "data_url 格式非法")
    try:
        raw = base64.b64decode(m.group(2))
    except binascii.Error as e:
        raise HTTPException(400, f"base64 解码失败：{e}")
    saved = _save_png(raw)
    return {"path": saved["path"], "bytes": saved["bytes"]}


@router.post("/trim")
def trim_image(body: dict):
    """裁掉纯白边，并估算字号：返回 {path,width,height,cropped,font_pt,px_per_pt}。"""
    body = body or {}
    rel = str(body.get("path") or "")
    if not rel:
        raise HTTPException(400, "缺少 path")
    try:
        px_per_pt = float(body.get("px_per_pt") or 3.0)
    except Exception:
        px_per_pt = 3.0
    src = (LIBRARY_DIR / rel).resolve()
    if not str(src).startswith(str(LIBRARY_DIR.resolve())) or not src.is_file():
        raise HTTPException(404, "图片不存在")
    try:
        from io import BytesIO

        from PIL import Image, ImageChops
    except Exception:
        raise HTTPException(501, "缺少 Pillow，无法处理图片")

    def _font_px(img) -> float:
        """按行投影估算正文字高（像素）。"""
        g = img.convert("L")
        w, h = g.size
        data = list(g.getdata())
        rows = []
        for y in range(h):
            row = data[y * w:(y + 1) * w]
            rows.append(sum(1 for v in row if v < 150))
        bands, inband, start = [], False, 0
        for y, cnt in enumerate(rows):
            on = cnt > max(2, w * 0.004)
            if on and not inband:
                inband, start = True, y
            elif not on and inband:
                inband = False
                if y - start >= 3:
                    bands.append(y - start)
        if inband and h - start >= 3:
            bands.append(h - start)
        if not bands:
            return 0.0
        bands.sort()
        return float(bands[len(bands) // 2])

    im = Image.open(src).convert("RGB")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    box = ImageChops.difference(im, bg).getbbox()
    if not box:
        raise HTTPException(400, "整张图都是空白")

    pad = 10
    x0, y0, x1, y1 = box
    x0 = max(0, x0 - pad); y0 = max(0, y0 - pad)
    x1 = min(im.width, x1 + pad); y1 = min(im.height, y1 + pad)
    out = im.crop((x0, y0, x1, y1))
    font_px = _font_px(im.crop(box))      # 用未加留白的区域估字号
    font_pt = round(font_px / px_per_pt, 2) if px_per_pt > 0 else 0

    if (x1 - x0, y1 - y0) == im.size:
        return {"path": rel, "width": im.width, "height": im.height, "cropped": False,
                "font_pt": font_pt, "px_per_pt": px_per_pt}

    buf = BytesIO()
    out.save(buf, format="PNG")
    info = _save_png(buf.getvalue())
    return {"path": info["path"], "width": out.width, "height": out.height, "cropped": True,
            "font_pt": font_pt, "px_per_pt": px_per_pt}


@router.delete("/image")
def delete_image(path: str):
    """删除自己上传的题图（只允许删 data/library/questions 下的文件）。"""
    p = (LIBRARY_DIR / path).resolve()
    base = QUESTION_IMG_DIR.resolve()
    if not str(p).startswith(str(base)) or not p.is_file():
        raise HTTPException(404, "图片不存在")
    p.unlink()
    return {"ok": True}


# ================================================================ 手动拆解（小学题库）
# 与文献出题（/api/authoring）分开：这里写的是 books / units / questions 三张表，
# 拆出来的题目会进「题库」，能继续组卷、导出 PDF。
QTYPES = ["口算", "填空", "选择", "判断", "计算", "解决问题", "应用题", "阅读理解", "看图列式", "其他"]


def _book_brief(b: "Book") -> dict:
    return {
        "id": b.id, "title": b.title, "role": b.role, "subject": b.subject,
        "grade": b.grade, "semester": b.semester, "publisher": b.publisher,
        "edition_year": b.edition_year,
        "units": [{"id": u.id, "seq": u.seq, "name": u.name,
                   "knowledge_points": u.knowledge_points or []}
                  for u in sorted(b.units, key=lambda x: x.seq)],
    }


@router.get("/meta")
def manual_meta(db: Session = Depends(get_db)):
    """给手动拆解页用：可选书册（含单元与考点）+ 题型列表。"""
    from ..models import Book

    books = db.scalars(select(Book).order_by(Book.subject, Book.grade, Book.id)).all()
    return {"books": [_book_brief(b) for b in books], "qtypes": QTYPES}


class BookIn(BaseModel):
    title: str
    subject: str = "数学"
    grade: int = 1
    semester: str = "上册"
    publisher: str = "自编"
    edition_year: int = datetime.now().year
    role: str = "self_made"


@router.post("/books")
def manual_create_book(body: BookIn, db: Session = Depends(get_db)):
    """快速新建一册（默认 self_made），同参数已存在则直接返回。"""
    from ..models import Book

    exist = db.scalars(select(Book).where(
        Book.title == body.title, Book.publisher == body.publisher,
        Book.subject == body.subject, Book.grade == body.grade,
        Book.semester == body.semester, Book.edition_year == body.edition_year)).first()
    if exist:
        return _book_brief(exist)
    book = Book(role=body.role, subject=body.subject, grade=body.grade,
                semester=body.semester, publisher=body.publisher,
                edition_year=body.edition_year, title=body.title)
    db.add(book)
    db.commit()
    db.refresh(book)
    return _book_brief(book)


class ManualQuestionIn(BaseModel):
    book_id: int
    unit_id: int | None = None
    stem: str = ""
    options: list[str] = Field(default_factory=list)
    answer: str = ""
    analysis: str = ""
    q_type: str = "填空"
    difficulty: int = Field(default=2, ge=1, le=3)
    knowledge_points: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    images: list[dict] = Field(default_factory=list)
    source_page: str = ""
    status: str = "draft"


def _check_question_payload(db: Session, body: ManualQuestionIn):
    from ..models import Book, Unit

    book = db.get(Book, body.book_id)
    if not book:
        raise HTTPException(400, "书册不存在")
    if body.unit_id:
        unit = db.get(Unit, body.unit_id)
        if not unit or unit.book_id != book.id:
            raise HTTPException(400, "单元不属于该册书")
    if not (body.stem or "").strip() and not body.images:
        raise HTTPException(400, "题干文字与题图不能都为空")
    if body.status not in ("draft", "confirmed", "rejected"):
        raise HTTPException(400, "非法状态")
    return book


def _question_dict(q) -> dict:
    return {
        "id": q.id, "book_id": q.book_id, "unit_id": q.unit_id, "stem": q.stem,
        "options": q.options or [], "answer": q.answer, "analysis": q.analysis or "",
        "q_type": q.q_type, "difficulty": q.difficulty,
        "knowledge_points": q.knowledge_points or [], "tags": q.tags or [],
        "images": q.images or [], "source_page": q.source_page or "",
        "scope_status": q.scope_status, "scope_reason": q.scope_reason or "",
        "status": q.status,
    }


@router.post("/questions")
def manual_create_question(body: ManualQuestionIn, db: Session = Depends(get_db)):
    """手动拆解入库：同书同内容则更新，避免重复题。"""
    from ..models import Question
    from ..services.bundle_import import _question_hash

    book = _check_question_payload(db, body)
    payload = {
        "stem": body.stem or "（题图）", "q_type": body.q_type,
        "options": body.options or None, "answer": body.answer or "（见答案图）",
        "knowledge_points": body.knowledge_points, "source_page": body.source_page or None,
    }
    h = _question_hash(book.id, payload)
    q = db.scalars(select(Question).where(Question.book_id == book.id,
                                          Question.content_hash == h)).first()
    fields = dict(
        unit_id=body.unit_id, stem=payload["stem"], options=body.options or [],
        answer=payload["answer"], analysis=body.analysis, q_type=body.q_type,
        difficulty=body.difficulty, knowledge_points=body.knowledge_points,
        images=body.images, source_page=body.source_page or None, tags=body.tags,
        scope_status="ok" if body.unit_id else "pending",
        scope_reason="人工录入", status=body.status,
    )
    if q:
        for k, v in fields.items():
            setattr(q, k, v)
        db.commit()
        db.refresh(q)
        return _question_dict(q)
    q = Question(book_id=book.id, content_hash=h, **fields)
    db.add(q)
    db.commit()
    db.refresh(q)
    return _question_dict(q)


@router.put("/questions/{question_id}")
def manual_update_question(question_id: int, body: ManualQuestionIn,
                           db: Session = Depends(get_db)):
    from ..models import Question

    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(404, "题目不存在")
    _check_question_payload(db, body)
    q.book_id = body.book_id
    q.unit_id = body.unit_id
    q.stem = body.stem or "（题图）"
    q.options = body.options or []
    q.answer = body.answer or "（见答案图）"
    q.analysis = body.analysis
    q.q_type = body.q_type
    q.difficulty = body.difficulty
    q.knowledge_points = body.knowledge_points
    q.tags = body.tags
    q.images = body.images
    q.source_page = body.source_page or None
    q.status = body.status
    db.commit()
    db.refresh(q)
    return _question_dict(q)
