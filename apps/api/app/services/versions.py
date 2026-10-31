"""E：题目版本历史服务——存快照、列版本、回滚。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Question, QuestionVersion

# 参与快照的字段：改这些才值得留版本
FIELDS = (
    "stem", "options", "answer", "analysis", "q_type", "difficulty",
    "knowledge_points", "images", "tags", "layout", "source_page",
    "source_title", "source_citation", "unit_id", "set_id", "status",
    "scope_status", "scope_reason",
)


def take_snapshot(session: Session, q: Question, note: str = "") -> QuestionVersion | None:
    """把当前状态存成新版本；内容与上一版完全相同则跳过。"""
    snap = {f: getattr(q, f, None) for f in FIELDS}
    last = session.scalar(
        select(QuestionVersion).where(QuestionVersion.question_id == q.id)
        .order_by(QuestionVersion.version_no.desc()).limit(1))
    if last is not None and (last.snapshot or {}) == snap:
        return None
    no = (last.version_no + 1) if last is not None else 1
    ver = QuestionVersion(question_id=q.id, version_no=no, note=note or None, snapshot=snap)
    session.add(ver)
    return ver


def list_versions(session: Session, question_id: int) -> list[QuestionVersion]:
    return list(session.scalars(
        select(QuestionVersion).where(QuestionVersion.question_id == question_id)
        .order_by(QuestionVersion.version_no.desc())).all())


def version_brief(v: QuestionVersion) -> dict:
    snap = v.snapshot or {}
    return {
        "id": v.id,
        "version_no": v.version_no,
        "note": v.note,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "stem": (snap.get("stem") or "")[:120],
        "answer": (snap.get("answer") or "")[:120],
        "q_type": snap.get("q_type"),
        "images": len(snap.get("images") or []),
        "tags": snap.get("tags") or [],
        "has_layout": bool(snap.get("layout")),
    }


def restore_version(session: Session, q: Question, version_id: int) -> QuestionVersion:
    """回滚到指定版本；回滚前先给「当前状态」也存一版，便于后悔。"""
    ver = session.get(QuestionVersion, version_id)
    if ver is None or ver.question_id != q.id:
        raise ValueError("版本不存在")
    take_snapshot(session, q, note=f"回滚到 v{ver.version_no} 之前")
    session.flush()  # 让待插入的版本号先落库，避免和下面的 _next_no 撞号
    for f in FIELDS:
        if f in (ver.snapshot or {}):
            setattr(q, f, ver.snapshot[f])
    q.updated_at = datetime.now(timezone.utc)
    session.add(QuestionVersion(question_id=q.id, version_no=_next_no(session, q.id),
                                note=f"回滚到 v{ver.version_no}", snapshot=dict(ver.snapshot or {})))
    return ver


def _next_no(session: Session, question_id: int) -> int:
    session.flush()
    n = session.scalar(select(func.max(QuestionVersion.version_no))
                       .where(QuestionVersion.question_id == question_id)) or 0
    return n + 1
