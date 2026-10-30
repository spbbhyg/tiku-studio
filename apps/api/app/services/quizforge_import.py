"""QuizForge 题库导入适配器。

QuizForge（https://github.com/cjxxx222/quizforge）用 SQLite 存「题库 → 题目」，
本模块把它读出来映射成本系统的 `Book + Question`：

    banks            → books（role=self_made，publisher=QuizForge）
    questions        → questions（题型映射、选项拼接、页码进 source_page）
    options_json     → ["A. xxx", "B. xxx", ...]（本系统 options 就是字符串数组）
    quarantine       → 忽略（那是 QuizForge 自己的隔离区，未抽成功的原始块）

去重口径与 bundle 导入一致：同一册书内 `_question_hash()` 相同即视为同题，
重复出现时只补全空着的答案/解析，不新增行。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Book, Question
from .bundle_import import _question_hash

# QuizForge 题型 → 本系统题型（Q_TYPES 里的取值）
TYPE_MAP = {
    "single_choice": "选择",
    "multi_choice": "多选",
    "true_false": "判断",
    "fill_blank": "填空",
}
TYPE_LABEL = {
    "single_choice": "单选",
    "multi_choice": "多选",
    "true_false": "判断",
    "fill_blank": "填空",
}
PUBLISHER = "QuizForge"


class QuizForgeError(RuntimeError):
    """QuizForge 数据库不可读或格式不符。"""


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise QuizForgeError(f"找不到数据库文件：{db_path}")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("select 1 from banks limit 1")
    except sqlite3.Error as e:
        raise QuizForgeError(f"这不是可读的 QuizForge 数据库（{e}）") from e
    return conn


def read_banks(db_path: Path) -> list[dict]:
    """列出库里的题库（供界面勾选）。"""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "select id, name, source_file, question_count, status, created_at "
            "from banks order by created_at desc").fetchall()
    finally:
        conn.close()
    return [{
        "id": r["id"],
        "name": r["name"],
        "source_file": r["source_file"],
        "question_count": r["question_count"],
        "status": r["status"],
        "created_at": r["created_at"],
    } for r in rows]


def _guess_subject(name: str) -> str:
    for key in ("化学", "数学", "物理", "语文", "英语", "生物", "政治", "历史", "地理"):
        if key in name:
            return key
    # 题库名常常是英文文件名，按常见词根再猜一轮
    lower = name.lower()
    for key, roots in (
        ("化学", ("chem", "organic", "inorganic", "electro")),
        ("数学", ("math", "linear", "algebra", "calculus")),
        ("物理", ("phys", "mechanics")),
        ("英语", ("english", "toefl", "cet")),
    ):
        if any(r in lower for r in roots):
            return key
    return "综合"


def _options(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    out = []
    for item in data:
        if isinstance(item, dict):
            key = str(item.get("key") or item.get("label") or "").strip()
            text = str(item.get("text") or "").strip()
            out.append(f"{key}. {text}" if key else text)
        else:
            out.append(str(item).strip())
    return [o for o in out if o]


def _get_or_create_book(session: Session, name: str, source_file: str | None) -> tuple[Book, bool]:
    subject = _guess_subject(name)
    book = session.scalar(select(Book).where(
        Book.title == name, Book.publisher == PUBLISHER, Book.subject == subject,
        Book.grade == 0, Book.semester == "", Book.edition_year == 0))
    if book:
        return book, False
    book = Book(role="self_made", subject=subject, grade=0, semester="",
                publisher=PUBLISHER, edition_year=0, title=name,
                source_file=source_file,
                description="由 QuizForge 题库导入（内置适配器）")
    session.add(book)
    session.flush()
    return book, True


def import_banks(session: Session, db_path: Path, bank_ids: list[str] | None = None) -> dict:
    """把指定（默认全部）题库导入本系统，返回统计。"""
    conn = _connect(db_path)
    try:
        banks = conn.execute("select * from banks order by created_at").fetchall()
        if bank_ids:
            wanted = set(bank_ids)
            banks = [b for b in banks if b["id"] in wanted]
        if not banks:
            raise QuizForgeError("没有可导入的题库")

        created_q = updated_q = 0
        report = []
        for bank in banks:
            book, book_new = _get_or_create_book(session, bank["name"], bank["source_file"])
            rows = conn.execute(
                "select * from questions where bank_id = ? order by seq, id", (bank["id"],)).fetchall()
            new_here = 0
            for row in rows:
                q_type = TYPE_MAP.get(row["type"], "其他")
                stem = (row["stem"] or "").strip()
                if not stem:
                    continue
                options = _options(row["options_json"])
                answer = (row["answer"] or "").strip()
                analysis = (row["analysis"] or "").strip() or None
                source_page = f"p.{row['page_ref']}" if row["page_ref"] else None
                tags = [PUBLISHER, TYPE_LABEL.get(row["type"], "其他")]
                if row["chapter"]:
                    tags.append(str(row["chapter"]).strip())

                content_hash = _question_hash(book.id, {
                    "stem": stem, "q_type": q_type, "options": options,
                    "answer": answer, "knowledge_points": [], "source_page": source_page,
                })
                exist = session.scalar(select(Question).where(
                    Question.book_id == book.id, Question.content_hash == content_hash))
                if exist:
                    changed = False
                    if not (exist.answer or "").strip() and answer:
                        exist.answer, changed = answer, True
                    if not (exist.analysis or "").strip() and analysis:
                        exist.analysis, changed = analysis, True
                    if changed:
                        updated_q += 1
                    continue

                session.add(Question(
                    book_id=book.id, content_hash=content_hash, stem=stem,
                    options=options or None, answer=answer, analysis=analysis,
                    q_type=q_type, difficulty=2, knowledge_points=[], images=[],
                    source_page=source_page, tags=tags,
                    scope_status="ok", scope_reason="QuizForge 导入", status="confirmed",
                ))
                created_q += 1
                new_here += 1
            session.flush()
            report.append({"bank": bank["name"], "book_id": book.id, "created": book_new,
                           "questions_in_bank": len(rows), "questions_imported": new_here})
        session.commit()
        return {"books_created": sum(1 for r in report if r["created"]),
                "questions_created": created_q, "questions_updated": updated_q,
                "banks": report}
    finally:
        conn.close()
