"""对纲校验：以绑定教材（role=textbook）的『单元→知识点』映射为标尺，判定每道题的范围属性。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Book, Unit

AUTO_MATCH_PUBLISHERS = ("人教版", "统编版", "部编版", "北师大版", "苏教版")


def find_textbook(session: Session, subject: str, grade: int, semester: str,
                  title: str | None = None, publisher: str | None = None) -> Book | None:
    """优先按标题精确匹配，其次按 年级+科目+学期（publisher 仅作排序偏好）。"""
    if title:
        book = session.scalar(select(Book).where(Book.role == "textbook", Book.title == title))
        if book:
            return book
    stmt = select(Book).where(
        Book.role == "textbook",
        Book.subject == subject,
        Book.grade == grade,
        Book.semester == semester,
    )
    books = session.scalars(stmt).all()
    if not books:
        return None
    if publisher:
        books.sort(key=lambda b: 0 if b.publisher == publisher else 1)
    return books[0]


def build_kp_index(textbook: Book) -> dict[str, dict]:
    """{知识点名: {unit_id, unit_seq, unit_name}}。同名知识点以先出现的单元为准。"""
    index: dict[str, dict] = {}
    for unit in session_units(textbook):
        for kp in unit.knowledge_points or []:
            index.setdefault(kp, {
                "unit_id": unit.id,
                "unit_seq": unit.seq,
                "unit_name": unit.name,
            })
    return index


def session_units(textbook: Book) -> list[Unit]:
    return sorted(textbook.units, key=lambda u: u.seq)


def classify(kps: list[str], kp_index: dict[str, dict],
             unit_hint: dict | None = None) -> tuple[str, int | None, str | None]:
    """返回 (scope_status, unit_id, reason)。

    规则：
    - 全部知识点都在纲内 → ok，归属知识点覆盖最多的单元
    - 有知识点不在纲内 → suspect（疑似超纲或版本不符），附原因
    - 纲缺失 → pending
    - unit_hint 可指定归属单元（教辅按自身单元目录对齐课本时使用）
    - 『提优拓展』不由自动判定产生：拆解方若明确知道是提优题，在 bundle 里显式给 scope_status=extension
    """
    if not kp_index:
        return "pending", None, "未找到对应教材纲（课本），无法对纲校验"

    unit_seq_hint = None
    unit_name_hint = None
    if unit_hint:
        unit_seq_hint = unit_hint.get("seq")
        unit_name_hint = unit_hint.get("name")

    # 归属单元：hint 优先，否则取知识点重合最多的单元
    unit_id: int | None = None
    if unit_seq_hint or unit_name_hint:
        for u in kp_index.values():
            if (unit_seq_hint and u["unit_seq"] == unit_seq_hint) or \
               (unit_name_hint and u["unit_name"] == unit_name_hint):
                unit_id = u["unit_id"]
                break

    known: list[str] = []
    unknown: list[str] = []
    overlap: dict[int, int] = {}
    for kp in kps:
        hit = kp_index.get(kp)
        if hit:
            known.append(kp)
            overlap[hit["unit_id"]] = overlap.get(hit["unit_id"], 0) + 1
        else:
            unknown.append(kp)

    if unit_id is None and overlap:
        unit_id = max(overlap, key=overlap.get)

    if unknown:
        reason = "知识点不在教材纲内：%s" % "、".join(unknown)
        if known:
            reason += "（已匹配：%s）" % "、".join(known)
        return "suspect", unit_id, reason
    return "ok", unit_id, None
