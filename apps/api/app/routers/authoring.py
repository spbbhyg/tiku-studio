"""文献出题：PDF 裁剪 + 手写题干/答案（支持 $LaTeX$）+ Chemy 风格专题标签。

2026-10 改版（用户要求「文献出题与手动拆解合并、直接进题库」）：
- 不再有独立的 authoring_questions 表：题目直接写进 `questions`，和题库/组卷/导出共用一套；
- 标签库换成本系统仿 Chemy 的九大专题，仍可增删改、批量导入；
- 导出按 `questions` 的记录生成 Word / PDF / Markdown。

接口：
- `GET  /api/authoring/tags`            标签（分组返回）
- `POST /api/authoring/tags`            新建标签
- `PUT  /api/authoring/tags/{id}`       改名/换组/改色（改名会同步题目上的旧名）
- `DELETE /api/authoring/tags/{id}`
- `POST /api/authoring/tags/import`     批量导入（按空格/逗号/顿号切分）
- `GET  /api/authoring/meta`            题型、书册与单元（给下拉用）
- `POST /api/authoring/questions`       出题入库（可带题图/答案图）
- `PUT  /api/authoring/questions/{id}`  改题
- `GET  /api/authoring/export`          导出 Word / PDF / Markdown
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import versions as version_svc
from ..models import AuthoringTag, Book, Question, Setting, Unit
from ..services import authoring_export as exporter
from ..services.bundle_import import _question_hash

router = APIRouter(prefix="/api/authoring", tags=["authoring"])

Q_TYPES = ["单选", "多选", "判断", "填空", "名词解释", "简答", "论述",
           "计算", "证明", "案例分析", "翻译", "文献阅读", "其他"]

TAG_SEED_MARKER = "authoring_tags_seeded_v2"

# 仿 Chemy 的专题划分（https://brucejllpku.github.io/Chemy-/）
CHEMY_TOPICS = [
    "有机化学", "高分子化学", "晶体化学", "无机综合与结构推断", "方程式与元素化学",
    "热力学与化学平衡", "电化学", "动力学", "分析化学",
]
SEED_TAGS: list[tuple[str, list[str]]] = [("专题", CHEMY_TOPICS)]

DEFAULT_BOOK = "文献命题集"


# ---------------------------------------------------------------- 标签库
def _tag_out(t: AuthoringTag) -> dict:
    return {"id": t.id, "name": t.name, "group": t.group,
            "color": t.color or "", "seq": t.seq or 0}


def _ensure_seed(db: Session) -> None:
    """第一次使用时灌入 Chemy 专题；用户删光了也不复活。"""
    if db.get(Setting, TAG_SEED_MARKER) is not None:
        return
    n = db.scalar(select(func.count()).select_from(AuthoringTag)) or 0
    if n == 0:
        seq = 0
        for group, names in SEED_TAGS:
            for name in names:
                seq += 1
                db.add(AuthoringTag(name=name, group=group, seq=seq))
    db.add(Setting(key=TAG_SEED_MARKER, value={"at": datetime.now(timezone.utc).isoformat()}))
    db.commit()


@router.get("/tags")
def list_tags(db: Session = Depends(get_db)):
    _ensure_seed(db)
    rows = db.scalars(select(AuthoringTag).order_by(
        AuthoringTag.group, AuthoringTag.seq, AuthoringTag.id)).all()
    groups: dict[str, list[dict]] = {}
    for t in rows:
        groups.setdefault(t.group or "专题", []).append(_tag_out(t))
    return {"tags": [_tag_out(t) for t in rows], "groups": groups}


class TagIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    group: str = Field(default="专题", max_length=40)
    color: str = Field(default="", max_length=16)


@router.post("/tags")
def create_tag(body: TagIn, db: Session = Depends(get_db)):
    _ensure_seed(db)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "标签不能为空")
    exist = db.scalar(select(AuthoringTag).where(AuthoringTag.name == name))
    if exist:
        return {"tag": _tag_out(exist), "created": False}
    seq = (db.scalar(select(func.max(AuthoringTag.seq))) or 0) + 1
    tag = AuthoringTag(name=name, group=(body.group or "专题").strip() or "专题",
                       color=body.color or "", seq=seq)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return {"tag": _tag_out(tag), "created": True}


class TagPatch(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    group: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=16)


@router.put("/tags/{tag_id}")
def update_tag(tag_id: int, body: TagPatch, db: Session = Depends(get_db)):
    tag = db.get(AuthoringTag, tag_id)
    if not tag:
        raise HTTPException(404, "标签不存在")
    old = tag.name
    if body.name:
        name = body.name.strip()
        if name and name != old:
            dup = db.scalar(select(AuthoringTag).where(AuthoringTag.name == name))
            if dup:
                raise HTTPException(400, f"已经有同名标签：{name}")
            tag.name = name
            # 题目里存的是标签名，改名要一起改
            for q in db.scalars(select(Question).where(Question.tags.isnot(None))).all():
                tags = list(q.tags or [])
                if old in tags:
                    q.tags = [name if t == old else t for t in tags]
    if body.group is not None:
        tag.group = body.group.strip() or "专题"
    if body.color is not None:
        tag.color = body.color
    db.commit()
    db.refresh(tag)
    return {"tag": _tag_out(tag)}


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int, db: Session = Depends(get_db)):
    tag = db.get(AuthoringTag, tag_id)
    if not tag:
        raise HTTPException(404, "标签不存在")
    db.delete(tag)
    db.commit()
    return {"ok": True}


class TagImport(BaseModel):
    text: str = Field(default="", description="用空格/逗号/顿号/换行分隔")
    names: list[str] = Field(default_factory=list)
    group: str = Field(default="专题")


@router.post("/tags/import")
def import_tags(body: TagImport, db: Session = Depends(get_db)):
    _ensure_seed(db)
    raw: list[str] = list(body.names)
    if body.text:
        cleaned = body.text
        for ch in ",，、;；\n\r\t":
            cleaned = cleaned.replace(ch, " ")
        raw += [x for x in cleaned.split(" ") if x.strip()]
    seen, added, existed = set(), 0, 0
    for name in raw:
        name = name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        if db.scalar(select(AuthoringTag).where(AuthoringTag.name == name)):
            existed += 1
            continue
        seq = (db.scalar(select(func.max(AuthoringTag.seq))) or 0) + 1
        db.add(AuthoringTag(name=name, group=(body.group or "专题").strip() or "专题", seq=seq))
        added += 1
    db.commit()
    return {"added": added, "existed": existed, "total": added + existed}


# ---------------------------------------------------------------- 元数据
def _book_brief(book: Book, units: list[Unit]) -> dict:
    return {
        "id": book.id, "title": book.title, "role": book.role, "subject": book.subject,
        "units": [{"id": u.id, "seq": u.seq, "name": u.name,
                   "knowledge_points": u.knowledge_points or []} for u in units],
    }


def _default_book(db: Session) -> Book:
    book = db.scalar(select(Book).where(Book.title == DEFAULT_BOOK))
    if book:
        return book
    book = Book(role="self_made", subject="综合", grade=0, semester="",
                publisher="自建", edition_year=0, title=DEFAULT_BOOK,
                description="文献出题默认落库位置")
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


@router.get("/meta")
def meta(db: Session = Depends(get_db)):
    _ensure_seed(db)
    books = db.scalars(select(Book).order_by(Book.role.desc(), Book.id)).all()
    units = db.scalars(select(Unit).order_by(Unit.book_id, Unit.seq)).all()
    by_book: dict[int, list[Unit]] = {}
    for u in units:
        by_book.setdefault(u.book_id, []).append(u)
    default_book = _default_book(db)
    return {
        "qtypes": Q_TYPES,
        "books": [_book_brief(b, by_book.get(b.id, [])) for b in books],
        "default_book_id": default_book.id,
        "default_book_title": default_book.title,
    }


# ---------------------------------------------------------------- 出题入库
class AuthoringQuestionIn(BaseModel):
    book_id: int | None = None
    unit_id: int | None = None
    stem: str = Field(default="", description="支持 $LaTeX$")
    stem_image: str | None = None
    options: list[str] = Field(default_factory=list)
    answer: str = ""
    answer_image: str | None = None
    analysis: str = ""
    q_type: str = "简答"
    difficulty: int = 2
    tags: list[str] = Field(default_factory=list)
    knowledge_points: list[str] = Field(default_factory=list)
    source_title: str = ""
    source_citation: str = ""
    locator: str = ""
    status: str = "confirmed"


def _images_of(stem_image: str | None, answer_image: str | None) -> list[dict]:
    out = []
    if stem_image:
        out.append({"path": stem_image, "caption": "题干"})
    if answer_image:
        out.append({"path": answer_image, "caption": "答案"})
    return out


def _apply(q: Question, body: AuthoringQuestionIn, db: Session) -> None:
    q.stem = (body.stem or "").strip()
    q.options = [o.strip() for o in body.options if o and o.strip()] or None
    q.answer = (body.answer or "").strip()
    q.analysis = (body.analysis or "").strip() or None
    q.q_type = body.q_type if body.q_type in Q_TYPES else "简答"
    q.difficulty = body.difficulty if body.difficulty in (1, 2, 3) else 2
    q.tags = [t.strip() for t in body.tags if t and t.strip()]
    q.knowledge_points = [k.strip() for k in body.knowledge_points if k and k.strip()]
    q.images = _images_of(body.stem_image, body.answer_image)
    q.source_title = (body.source_title or "").strip() or None
    q.source_citation = (body.source_citation or "").strip() or None
    q.source_page = (body.locator or "").strip() or None
    q.status = "confirmed" if body.status == "confirmed" else "draft"
    q.scope_status = "ok" if body.unit_id else "pending"
    q.scope_reason = "文献出题"


@router.post("/questions")
def create_question(body: AuthoringQuestionIn, db: Session = Depends(get_db)):
    if not (body.stem or "").strip() and not body.stem_image:
        raise HTTPException(400, "题干文本与题干图不能同时为空")
    if not (body.answer or "").strip() and not body.answer_image:
        raise HTTPException(400, "答案文本与答案图不能同时为空")
    book = db.get(Book, body.book_id) if body.book_id else _default_book(db)
    if not book:
        raise HTTPException(400, "书册不存在")
    if body.unit_id and not db.get(Unit, body.unit_id):
        raise HTTPException(400, "单元不存在")
    _ensure_seed(db)
    content_hash = _question_hash(book.id, {
        "stem": (body.stem or "").strip(), "q_type": body.q_type,
        "options": body.options or None, "answer": (body.answer or "").strip(),
        "knowledge_points": body.knowledge_points or [],
        "source_page": (body.locator or "").strip() or None,
    })
    exist = db.scalar(select(Question).where(
        Question.book_id == book.id, Question.content_hash == content_hash))
    if exist:
        _apply(exist, body, db)
        exist.unit_id = body.unit_id
        db.commit()
        db.refresh(exist)
        return {"id": exist.id, "updated": True}
    q = Question(book_id=book.id, unit_id=body.unit_id, content_hash=content_hash,
                 stem="", answer="", q_type="简答")
    _apply(q, body, db)
    db.add(q)
    db.commit()
    db.refresh(q)
    return {"id": q.id, "updated": False}


@router.put("/questions/{question_id}")
def update_question(question_id: int, body: AuthoringQuestionIn, db: Session = Depends(get_db)):
    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(404, "题目不存在")
    if not (body.stem or "").strip() and not body.stem_image:
        raise HTTPException(400, "题干文本与题干图不能同时为空")
    version_svc.take_snapshot(db, q, note="编辑前")
    _apply(q, body, db)
    if body.book_id:
        if not db.get(Book, body.book_id):
            raise HTTPException(400, "书册不存在")
        q.book_id = body.book_id
    q.unit_id = body.unit_id
    q.content_hash = _question_hash(q.book_id, {
        "stem": q.stem, "q_type": q.q_type, "options": q.options, "answer": q.answer,
        "knowledge_points": q.knowledge_points or [], "source_page": q.source_page,
    })
    db.commit()
    return {"ok": True, "id": q.id}


@router.get("/questions/{question_id}")
def get_question(question_id: int, db: Session = Depends(get_db)):
    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(404, "题目不存在")
    return _q_brief(q)


# ---------------------------------------------------------------- 大题画布
class ComposeIn(BaseModel):
    """出题画布：版面 + 归属，保存时渲染成一张 A4 题图写入题库。"""

    layout: dict
    question_id: int | None = None
    book_id: int | None = None
    unit_id: int | None = None
    q_type: str = "简答"
    difficulty: int = 2
    tags: list[str] = Field(default_factory=list)
    source_title: str = ""
    source_citation: str = ""
    locator: str = ""
    status: str = "confirmed"
    answer: str = ""
    answer_images: list[str] = Field(default_factory=list)
    answer_layout: dict | None = None


@router.post("/compose")
def compose_question(body: ComposeIn, db: Session = Depends(get_db)):
    from ..services import compose_render

    blocks = (body.layout or {}).get("blocks") or []
    if not blocks:
        raise HTTPException(400, "画布是空的：先裁剪或输入文字再保存")

    book = db.get(Book, body.book_id) if body.book_id else _default_book(db)
    if not book:
        raise HTTPException(400, "书册不存在")
    if body.unit_id and not db.get(Unit, body.unit_id):
        raise HTTPException(400, "单元不存在")

    rendered = compose_render.render_layout(body.layout)
    ans_rendered = None
    if body.answer_layout and (body.answer_layout.get("blocks") or []):
        ans_rendered = compose_render.render_layout(body.answer_layout)
    content_hash = compose_render.layout_hash(body.layout)
    tags = [t.strip() for t in body.tags if t and t.strip()]

    q = db.get(Question, body.question_id) if body.question_id else None
    created = q is None
    if created:
        q = db.scalar(select(Question).where(
            Question.book_id == book.id, Question.content_hash == content_hash))
        if q:
            created = False
    if not q:
        q = Question(book_id=book.id, content_hash=content_hash, stem="", answer="", q_type="简答")
        db.add(q)

    q.book_id = book.id
    q.unit_id = body.unit_id
    q.content_hash = content_hash
    q.stem = ""
    q.answer = (body.answer or "").strip()
    q.q_type = body.q_type if body.q_type in Q_TYPES else "简答"
    q.difficulty = body.difficulty if body.difficulty in (1, 2, 3) else 2
    q.tags = tags
    imgs = [{"path": rendered["path"], "caption": "题干"}]
    if ans_rendered:
        imgs.append({"path": ans_rendered["path"], "caption": "答案"})
    for ap in (body.answer_images or []):
        ap = (ap or "").strip()
        if ap and ap != (ans_rendered or {}).get("path"):
            imgs.append({"path": ap, "caption": "答案"})
    q.images = imgs
    q.layout = body.layout
    q.answer_layout = body.answer_layout or None
    q.source_title = (body.source_title or "").strip() or None
    q.source_citation = (body.source_citation or "").strip() or None
    q.source_page = (body.locator or "").strip() or None
    q.status = "confirmed" if body.status == "confirmed" else "draft"
    q.scope_status = "ok" if body.unit_id else "pending"
    q.scope_reason = "出题画布"
    db.commit()
    db.refresh(q)
    return {"id": q.id, "created": created, "image": rendered["path"],
            "width": rendered["width"], "height": rendered["height"],
            "answer_image": (ans_rendered or {}).get("path")}


@router.get("/compose/{question_id}")
def get_compose(question_id: int, db: Session = Depends(get_db)):
    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(404, "题目不存在")
    return {
        "id": q.id, "layout": q.layout or {"blocks": []},
        "answer_layout": q.answer_layout or {"blocks": []},
        "book_id": q.book_id, "unit_id": q.unit_id, "q_type": q.q_type,
        "difficulty": q.difficulty, "tags": q.tags or [], "answer": q.answer or "",
        "source_title": q.source_title or "", "source_citation": q.source_citation or "",
        "locator": q.source_page or "", "status": q.status,
        "stem_image": next((i.get("path") for i in q.images or []), None),
        "stem_image2": None,
        "answer_images": [i.get("path") for i in (q.images or [])
                          if (i.get("caption") or "") == "答案"],
    }


def _q_brief(q: Question) -> dict:
    imgs = q.images or []
    stem_image = next((i.get("path") for i in imgs if (i.get("caption") or "") != "答案"), None)
    answer_image = next((i.get("path") for i in imgs if (i.get("caption") or "") == "答案"), None)
    return {
        "id": q.id, "book_id": q.book_id, "unit_id": q.unit_id,
        "stem": q.stem or "", "stem_image": stem_image,
        "options": q.options or [], "answer": q.answer or "",
        "answer_image": answer_image, "analysis": q.analysis or "",
        "tags": q.tags or [], "q_type": q.q_type, "difficulty": q.difficulty,
        "knowledge_points": q.knowledge_points or [],
        "source_title": q.source_title or "", "source_citation": q.source_citation or "",
        "locator": q.source_page or "", "status": q.status,
    }


# ---------------------------------------------------------------- 导出
def _export_row(q: Question) -> dict:
    brief = _q_brief(q)
    return {
        "stem": brief["stem"], "stem_image": brief["stem_image"],
        "options": brief["options"], "answer": brief["answer"],
        "answer_image": brief["answer_image"], "analysis": brief["analysis"],
        "q_type": brief["q_type"], "difficulty": brief["difficulty"],
        "tags": brief["tags"], "source_title": brief["source_title"],
        "source_citation": brief["source_citation"], "locator": brief["locator"],
    }


@router.get("/export")
def export(
    fmt: str = Query("docx", pattern="^(docx|pdf|md)$"),
    ids: str = "",
    q: str = "",
    tags: str = "",
    q_type: str = "",
    difficulty: int | None = None,
    answers: str = Query("appendix", pattern="^(inline|appendix|none)$"),
    analysis: int = 1,
    show_tags: int = Query(1, alias="show_tags"),
    source: int = 1,
    title: str = "文献命题",
    subtitle: str = "",
    db: Session = Depends(get_db),
):
    stmt = select(Question).where(or_(Question.status == "confirmed", Question.status == "draft"))
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    if id_list:
        stmt = stmt.where(Question.id.in_(id_list))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Question.stem.like(like), Question.source_title.like(like)))
    if q_type:
        stmt = stmt.where(Question.q_type == q_type)
    if difficulty:
        stmt = stmt.where(Question.difficulty == difficulty)
    rows = db.scalars(stmt.order_by(Question.id)).all()
    if tags.strip():
        wanted = [t.strip() for t in tags.split(",") if t.strip()]
        rows = [r for r in rows if set(wanted) & set(r.tags or [])]
    if not rows:
        raise HTTPException(400, "没有符合条件的题目可导出")

    items = [_export_row(r) for r in rows]
    opts = {"answers": answers, "analysis": bool(analysis), "tags": bool(show_tags or tags),
            "source": bool(source), "title": title, "subtitle": subtitle}
    if fmt == "docx":
        data, mime = exporter.build_docx(items, opts), \
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif fmt == "pdf":
        data, mime = exporter.build_pdf(items, opts), "application/pdf"
    else:
        data, mime = exporter.build_markdown(items, opts).encode("utf-8"), "text/markdown; charset=utf-8"

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    ext = {"docx": "docx", "pdf": "pdf", "md": "md"}[fmt]
    name = f"{title or '文献命题'}-{stamp}.{ext}"
    headers = {
        "Content-Disposition": f"attachment; filename=export.{ext}; filename*=UTF-8''{quote(name)}",
        "X-Question-Count": str(len(items)),
    }
    return Response(content=data, media_type=mime, headers=headers)
