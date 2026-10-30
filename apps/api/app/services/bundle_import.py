"""拆解产物（bundle）校验与入库。CLI 脚本与 API 拆解共用这一条通道。

bundle 分两种，由内容自动识别：
- 目录级 catalog-bundle：book + units（课本/教辅的目录索引，课本即『纲』）
- 题目级 question-bundle：book + questions（试卷/教辅的题目）
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import LIBRARY_DIR, SCHEMA_DIR
from ..models import Book, Lesson, PageAsset, Question, Unit
from . import scope as scope_svc

_VALIDATORS: dict[str, Draft202012Validator] = {}


def _inline_catalog_refs(node, catalog_defs: dict) -> None:
    """把 question-bundle 里对 catalog-bundle 的 $ref 替换为内联定义。"""
    if isinstance(node, dict):
        if node.get("$ref") == "catalog-bundle.schema.json#/$defs/book":
            node.clear()
            node.update(catalog_defs["book"])
            return
        for v in node.values():
            _inline_catalog_refs(v, catalog_defs)
    elif isinstance(node, list):
        for v in node:
            _inline_catalog_refs(v, catalog_defs)


def _validator(name: str) -> Draft202012Validator:
    if name not in _VALIDATORS:
        catalog = json.loads((SCHEMA_DIR / "catalog-bundle.schema.json").read_text(encoding="utf-8"))
        schema = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
        _inline_catalog_refs(schema, catalog["$defs"])
        Draft202012Validator.check_schema(schema)
        _VALIDATORS[name] = Draft202012Validator(schema)
    return _VALIDATORS[name]


def detect_kind(data: dict) -> str:
    if "questions" in data:
        return "question"
    if "units" in data:
        return "catalog"
    raise ValueError("无法识别 bundle 类型：缺少 questions 或 units 字段")


def validate_bundle(data: dict) -> str:
    kind = detect_kind(data)
    validator = _validator(f"{kind}-bundle.schema.json")
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        msgs = ["%s: %s" % ("/".join(map(str, e.path)) or "<root>", e.message) for e in errors[:10]]
        raise ValueError("bundle 校验失败：\n" + "\n".join(msgs))
    return kind


def _book_key(meta: dict) -> tuple:
    return (meta["title"], meta["publisher"], meta["subject"],
            meta["grade"], meta["semester"], meta["edition_year"])


def _get_or_create_book(session: Session, meta: dict) -> Book:
    stmt = select(Book).where(
        Book.title == meta["title"], Book.publisher == meta["publisher"],
        Book.subject == meta["subject"], Book.grade == meta["grade"],
        Book.semester == meta["semester"], Book.edition_year == meta["edition_year"])
    book = session.scalar(stmt)
    if book is None:
        book = Book(**meta)
        session.add(book)
        session.flush()
    else:
        for f in ("role", "source_file", "description"):
            if meta.get(f):
                setattr(book, f, meta[f])
    return book


def _save_image(book_key: str, idx: int, item: dict, source_dir: Path | None) -> dict:
    """把 bundle 里的图片落库到 data/library，返回 {path, caption?}。"""
    caption = item.get("caption")
    out: dict = {}
    if caption:
        out["caption"] = caption
    if item.get("data_url"):
        m = re.match(r"data:image/(\w+);base64,(.+)", item["data_url"], re.S)
        if not m:
            raise ValueError("images[].data_url 格式非法")
        ext = "jpg" if m.group(1) in ("jpeg", "jpg") else m.group(1)
        try:
            raw = base64.b64decode(m.group(2))
        except binascii.Error as e:
            raise ValueError(f"images[{idx}] base64 解码失败: {e}") from e
        rel_dir = Path("images") / re.sub(r'[\\/:*?"<>| ]', "_", book_key)
        abs_dir = LIBRARY_DIR / rel_dir
        abs_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{idx:04d}.{ext}"
        (abs_dir / fname).write_bytes(raw)
        out["path"] = str(rel_dir / fname)
        return out
    if item.get("path"):
        p = Path(item["path"])
        if not p.is_absolute() and source_dir:
            p = source_dir / p
        if p.exists() and source_dir is not None:
            rel_dir = Path("images") / re.sub(r'[\\/:*?"<>| ]', "_", book_key)
            abs_dir = LIBRARY_DIR / rel_dir
            abs_dir.mkdir(parents=True, exist_ok=True)
            fname = f"{idx:04d}{p.suffix or '.png'}"
            (abs_dir / fname).write_bytes(p.read_bytes())
            out["path"] = str(rel_dir / fname)
        else:
            out["path"] = item["path"]  # 路径不存在时原样保留，待人工补
    return out


def _import_catalog(session: Session, data: dict, source_dir: Path | None) -> dict:
    book = _get_or_create_book(session, data["book"])
    existing_units = {u.seq: u for u in session.scalars(
        select(Unit).where(Unit.book_id == book.id)).all()}
    seen_seqs = set()
    for u in data["units"]:
        seen_seqs.add(u["seq"])
        unit = existing_units.get(u["seq"])
        if unit is None:
            unit = Unit(book_id=book.id, seq=u["seq"], name=u["name"])
            session.add(unit)
        unit.name = u["name"]
        unit.description = u.get("description")
        unit.knowledge_points = u.get("knowledge_points", [])
        session.flush()
        # 课时
        want_lessons = {l["seq"]: l for l in u.get("lessons", [])}
        have_lessons = {l.seq: l for l in session.scalars(
            select(Lesson).where(Lesson.unit_id == unit.id)).all()}
        for seq, l in want_lessons.items():
            if seq in have_lessons:
                have_lessons[seq].name = l["name"]
                have_lessons[seq].pages = l.get("pages")
            else:
                session.add(Lesson(unit_id=unit.id, seq=seq, name=l["name"], pages=l.get("pages")))
        for seq in set(have_lessons) - set(want_lessons):
            session.delete(have_lessons[seq])
        # 页面索引
        for p in u.get("pages", []):
            session.add(PageAsset(book_id=book.id, unit_id=unit.id,
                                  page_no=p.get("page_no"), kind=p.get("kind", "scan"),
                                  path=p["path"], note=p.get("note")))
    # 删除不再出现的单元（题目 unit_id 会因 ondelete=SET NULL 置空）
    for seq in set(existing_units) - seen_seqs:
        session.delete(existing_units[seq])
    session.flush()
    return {"book_id": book.id, "units": len(data["units"]), "role": book.role}


def _question_hash(book_id: int, q: dict) -> str:
    """同一册书内【题干+题型+选项+答案+考点+来源页】完全一致才视为重复题。"""
    raw = json.dumps([book_id, q["stem"], q["q_type"], q.get("options"), q["answer"],
                      q.get("knowledge_points"), q.get("source_page")],
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _import_questions(session: Session, data: dict, source_dir: Path | None) -> dict:
    meta = data["book"]
    book = _get_or_create_book(session, meta)
    textbook = None
    if data.get("source_textbook_title"):
        textbook = session.scalar(select(Book).where(
            Book.role == "textbook", Book.title == data["source_textbook_title"]))
    if textbook is None:
        textbook = scope_svc.find_textbook(
            session, meta["subject"], meta["grade"], meta["semester"],
            publisher=meta["publisher"] if meta["publisher"] in scope_svc.AUTO_MATCH_PUBLISHERS else None)
    kp_index = scope_svc.build_kp_index(textbook) if textbook else {}

    n_new = n_upd = 0
    for idx, q in enumerate(data["questions"]):
        status, unit_id, reason = scope_svc.classify(
            q["knowledge_points"], kp_index, unit_hint=q.get("unit_hint"))
        if q.get("scope_status"):
            status, reason = q["scope_status"], q.get("scope_reason", reason)
            if unit_id is None and q.get("unit_hint") and kp_index:
                for u in kp_index.values():
                    if (q["unit_hint"].get("seq") and u["unit_seq"] == q["unit_hint"]["seq"]) or \
                       (q["unit_hint"].get("name") and u["unit_name"] == q["unit_hint"]["name"]):
                        unit_id = u["unit_id"]
                        break
        images = [_save_image(meta["title"], i, item, source_dir)
                  for i, item in enumerate(q.get("images", []))]
        values = dict(
            book_id=book.id,
            source_textbook_id=textbook.id if textbook else None,
            unit_id=unit_id,
            stem=q["stem"], options=q.get("options"), answer=q["answer"],
            analysis=q.get("analysis"), q_type=q["q_type"], difficulty=q["difficulty"],
            knowledge_points=q["knowledge_points"], images=images,
            source_page=q.get("source_page"), tags=q.get("tags", []),
            scope_status=status, scope_reason=reason,
            status=q.get("status", "draft"),
        )
        ch = _question_hash(book.id, q)
        existing = session.scalar(select(Question).where(
            Question.book_id == book.id, Question.content_hash == ch))
        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
            n_upd += 1
        else:
            session.add(Question(content_hash=ch, **values))
            n_new += 1
    session.flush()
    return {"book_id": book.id, "questions_new": n_new, "questions_updated": n_upd,
            "textbook_id": textbook.id if textbook else None}


def import_bundle(session: Session, data: dict,
                  source_dir: Path | None = None) -> dict:
    """校验并导入一个 bundle。返回统计信息；调用方负责 commit/rollback。"""
    kind = validate_bundle(data)
    if kind == "catalog":
        result = _import_catalog(session, data, source_dir)
    else:
        result = _import_questions(session, data, source_dir)
    result["kind"] = kind
    return result


def import_bundle_file(session: Session, path: str | Path) -> dict:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    return import_bundle(session, data, source_dir=p.parent)
