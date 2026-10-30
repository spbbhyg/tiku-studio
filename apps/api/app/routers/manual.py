"""文献页图服务：上传 PDF → 渲染页图 → 按矩形裁剪成题图。

最初是「手动拆解（小学教材）」的后端，现在只保留下与业务无关的通用能力：
上传 / 页图 / 裁剪 / 上传图片 / 删除。文献出题页（Authoring.vue）复用它。
完全本地运行，不依赖 AI。
"""
from __future__ import annotations

import base64
import binascii
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


def _doc_file(doc_id: str) -> Path:
    if not DOC_ID_RE.match(doc_id or ""):
        raise HTTPException(400, "非法 doc_id")
    path = UPLOAD_DIR / f"{doc_id}.pdf"
    if not path.exists():
        raise HTTPException(404, "文档不存在或已被清理，请重新上传")
    return path


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
    return {"doc_id": doc_id, "filename": file.filename or "未命名.pdf",
            "page_count": len(pages),
            "url": f"/api/manual/doc/{doc_id}/page/1.png?zoom=2",
            "pages": pages}


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


# ---------------------------------------------------------------- 裁剪
@router.delete("/doc/{doc_id}")
def delete_doc(doc_id: str):
    """删除已上传的原件（连带页面渲染缓存）。已裁剪出的题图不受影响。"""
    path = _doc_file(doc_id)
    path.unlink()
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
