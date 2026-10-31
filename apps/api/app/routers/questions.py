from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Book, Question, QuestionSet, QuestionVersion, Unit
from ..schemas import QuestionOut, QuestionPatch
from ..services import compose_render, qimage, versions as version_svc

router = APIRouter(prefix="/api/questions", tags=["questions"])


def _serialize(q: Question) -> QuestionOut:
    out = QuestionOut.model_validate(q)
    out.book_title = q.book.title if q.book else ""
    if q.unit:
        out.unit_name = q.unit.name
        out.unit_seq = q.unit.seq
    if q.qset:
        out.set_id = q.qset.id
        out.set_name = q.qset.name
        out.set_year = q.qset.year
    return out


@router.get("")
def list_questions(
    db: Session = Depends(get_db),
    book_id: int | None = None,
    unit_id: int | None = None,
    textbook_id: int | None = None,
    subject: str | None = None,
    grade: int | None = None,
    semester: str | None = None,
    q_type: str | None = None,
    difficulty: int | None = None,
    kp: str | None = None,
    scope_status: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    tags: str | None = None,
    set_id: int | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    stmt = select(Question).join(Book, Question.book_id == Book.id)
    count_stmt = select(func.count()).select_from(Question).join(Book, Question.book_id == Book.id)
    conds = []
    if book_id:
        conds.append(Question.book_id == book_id)
    if unit_id:
        conds.append(Question.unit_id == unit_id)
    if textbook_id:
        conds.append(Question.source_textbook_id == textbook_id)
    if subject:
        conds.append(Book.subject == subject)
    if grade is not None:
        conds.append(Book.grade == grade)
    if semester:
        conds.append(Book.semester == semester)
    if q_type:
        conds.append(Question.q_type == q_type)
    if difficulty:
        conds.append(Question.difficulty == difficulty)
    if scope_status:
        conds.append(Question.scope_status == scope_status)
    if status:
        conds.append(Question.status == status)
    if set_id is not None:
        # set_id = -1 表示「未归卷」
        conds.append(Question.set_id.is_(None) if set_id == -1 else Question.set_id == set_id)
    if tag:  # 标签在 JSON 数组里，用 json_each 精确匹配
        conds.append(text(
            "EXISTS (SELECT 1 FROM json_each(questions.tags) WHERE json_each.value = :tag)"
        ).bindparams(tag=tag))
    if tags:  # 多个标签：逗号分隔，要求同时命中（AND）
        for i, t in enumerate([x.strip() for x in tags.split(",") if x.strip()]):
            conds.append(text(
                f"EXISTS (SELECT 1 FROM json_each(questions.tags) WHERE json_each.value = :tg{i})"
            ).bindparams(**{f"tg{i}": t}))
    if q:
        # 全文：空格分隔多个词，全部命中才算（题干/答案/解析/出处/标签/整卷名）
        for i, term in enumerate([x for x in q.split() if x.strip()]):
            like = f"%{term}%"
            conds.append(or_(
                Question.stem.like(like),
                Question.answer.like(like),
                Question.analysis.like(like),
                Question.source_title.like(like),
                Question.source_citation.like(like),
                cast(Question.tags, String).like(like),
                Question.qset.has(QuestionSet.name.like(like)),
            ))
    if conds:
        stmt = stmt.where(*conds)
        count_stmt = count_stmt.where(*conds)

    if kp:  # JSON 数组包含：先粗筛再精确过滤
        ids = {r[0] for r in db.execute(text(
            "SELECT q.id FROM questions q, json_each(q.knowledge_points) je WHERE je.value = :kp"),
            {"kp": kp}).all()}
        if not ids:
            return {"items": [], "total": 0}
        stmt = stmt.where(Question.id.in_(ids))
        count_stmt = select(func.count()).select_from(Question).where(Question.id.in_(ids))

    total = db.scalar(count_stmt)
    rows = db.scalars(
        stmt.order_by(Question.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"items": [_serialize(r) for r in rows], "total": total}


@router.get("/knowledge-points")
def knowledge_points(textbook_id: int, db: Session = Depends(get_db)):
    """某本教材纲的全部知识点（附单元信息），供筛选与组卷用。"""
    textbook = db.get(Book, textbook_id)
    if textbook is None:
        raise HTTPException(404, "textbook 不存在")
    out = []
    for u in sorted(textbook.units, key=lambda x: x.seq):
        for kp in u.knowledge_points or []:
            out.append({"name": kp, "unit_id": u.id, "unit_seq": u.seq, "unit_name": u.name})
    return out


@router.get("/type-stats")
def type_stats(textbook_id: int, db: Session = Depends(get_db)):
    """各题型已确认题量（与该纲同学科/年级/学期、非疑似超纲）：组卷页据此提示可用量。"""
    textbook = db.get(Book, textbook_id)
    if textbook is None:
        raise HTTPException(404, "textbook 不存在")
    rows = db.execute(
        select(Question.q_type, func.count())
        .join(Book, Question.book_id == Book.id)
        .where(Question.status == "confirmed",
               Question.scope_status != "suspect",
               Book.subject == textbook.subject,
               Book.grade == textbook.grade,
               Book.semester == textbook.semester)
        .group_by(Question.q_type)).all()
    return {t: c for t, c in rows}


@router.get("/{question_id}", response_model=QuestionOut)
def get_question(question_id: int, db: Session = Depends(get_db)):
    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(404, "题目不存在")
    return _serialize(q)


@router.patch("/{question_id}", response_model=QuestionOut)
def patch_question(question_id: int, patch: QuestionPatch, db: Session = Depends(get_db)):
    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(404, "题目不存在")
    data = patch.model_dump(exclude_unset=True)
    if data.get("unit_id") is not None:
        unit = db.get(Unit, data["unit_id"])
        if unit is None:
            raise HTTPException(400, "目标单元不存在")
    if "set_id" in data and data["set_id"] is not None:
        if db.get(QuestionSet, data["set_id"]) is None:
            raise HTTPException(400, "目标整卷不存在")
    version_svc.take_snapshot(db, q, note="编辑前")
    for k, v in data.items():
        setattr(q, k, v)
    db.commit()
    db.refresh(q)
    return _serialize(q)


@router.delete("/{question_id}")
def delete_question(question_id: int, db: Session = Depends(get_db)):
    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(404, "题目不存在")
    db.delete(q)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ 版本历史（E）
@router.get("/{question_id}/versions")
def list_versions(question_id: int, db: Session = Depends(get_db)):
    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(404, "题目不存在")
    rows = version_svc.list_versions(db, question_id)
    return {"items": [version_svc.version_brief(v) for v in rows], "total": len(rows)}


@router.get("/{question_id}/versions/{version_id}")
def get_version(question_id: int, version_id: int, db: Session = Depends(get_db)):
    v = db.get(QuestionVersion, version_id)
    if v is None or v.question_id != question_id:
        raise HTTPException(404, "版本不存在")
    return {"id": v.id, "version_no": v.version_no, "note": v.note,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "snapshot": v.snapshot or {}}


@router.post("/{question_id}/versions/{version_id}/restore", response_model=QuestionOut)
def restore_version(question_id: int, version_id: int, db: Session = Depends(get_db)):
    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(404, "题目不存在")
    try:
        version_svc.restore_version(db, q, version_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    db.commit()
    db.refresh(q)
    return _serialize(q)


@router.delete("/{question_id}/versions")
def clear_versions(question_id: int, db: Session = Depends(get_db)):
    for v in version_svc.list_versions(db, question_id):
        db.delete(v)
    db.commit()
    return {"ok": True}


@router.post("/{question_id}/render")
def render_one(question_id: int, force: bool = False, db: Session = Depends(get_db)):
    """把纯文本题目渲染成题图（已有题干图则跳过）。"""
    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(404, "题目不存在")
    r = qimage.render_question(db, q, force=force)
    db.commit()
    return r


@router.post("/render-all")
def render_all(force: bool = False, db: Session = Depends(get_db)):
    """一次性给所有缺题图的题目补图。"""
    rows = db.scalars(select(Question).order_by(Question.id)).all()
    done, skipped, failed = [], 0, []
    for q in rows:
        try:
            r = qimage.render_question(db, q, force=force)
            if r.get("skipped"):
                skipped += 1
            else:
                done.append(q.id)
        except Exception as e:  # noqa: BLE001
            failed.append({"id": q.id, "error": str(e)})
    db.commit()
    return {"rendered": done, "skipped": skipped, "failed": failed}


@router.post("/{question_id}/trim")
def trim_blank(question_id: int, db: Session = Depends(get_db)):
    """按已存的版面重新渲染，裁掉页面下方空白（保留页眉/页脚）。"""
    q = db.get(Question, question_id)
    if q is None:
        raise HTTPException(404, "题目不存在")
    layout = q.layout or {}
    if not layout.get("blocks"):
        raise HTTPException(400, "这道题没有版面数据，无法裁剪")
    layout = dict(layout)
    layout["trim"] = True
    layout.setdefault("trim_pad", 24)
    out = compose_render.render_layout(layout, dpi=200)
    version_svc.take_snapshot(db, q, note="裁剪空白前")
    imgs = [i for i in (q.images or []) if (i.get("caption") or "") == "答案"]
    imgs.insert(0, {"path": out["path"], "caption": "题干"})
    q.images = imgs
    q.layout = layout
    db.commit()
    return {"id": q.id, "image": out["path"], "width": out["width"], "height": out["height"]}
