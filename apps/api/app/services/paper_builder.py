"""组卷引擎：按『教材纲 + 单元范围 + 题型配额』从已确认题库抽题，并按总分自动配分。"""
from __future__ import annotations

import random
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Book, Paper, PaperQuestion, Question, Unit
from .scope import session_units

# 常见题型的默认每题分值（总分不齐时按比例再调整）
DEFAULT_SCORE_PER_TYPE: dict[str, float] = {
    "口算": 1, "计算": 1, "判断": 1, "填空": 2, "选择": 2, "拼音": 2, "字词": 2,
    "笔画": 2, "连线": 2, "默写": 2, "听写": 2, "古诗": 2, "字谜": 2, "操作": 2,
    "看图列式": 4, "应用": 4, "其他": 2,
}
TYPE_ORDER = ["口算", "计算", "填空", "选择", "判断", "拼音", "字词", "笔画",
              "连线", "默写", "听写", "古诗", "字谜", "看图列式", "应用", "操作", "其他"]


class NotEnoughQuestions(Exception):
    pass


def _snapshot(q: Question, book: Book, unit_name: str | None) -> dict:
    return {
        "stem": q.stem,
        "options": q.options,
        "answer": q.answer,
        "analysis": q.analysis,
        "q_type": q.q_type,
        "difficulty": q.difficulty,
        "knowledge_points": q.knowledge_points or [],
        "images": q.images or [],
        "layout": q.layout or None,
        "source_page": q.source_page,
        "source_book_title": book.title,
        "unit_name": unit_name,
    }


def _candidates(session: Session, textbook: Book, unit_ids: list[int],
                include_extension: bool) -> dict[str, list[Question]]:
    """可选池：已确认 + 范围/科目匹配 + 非疑似超纲。"""
    units = {u.id: u for u in session.scalars(
        select(Unit).where(Unit.id.in_(unit_ids))).all()}
    kp_scope: set[str] = set()
    for u in units.values():
        kp_scope.update(u.knowledge_points or [])

    stmt = select(Question).where(
        Question.status == "confirmed",
        Question.scope_status.in_(["ok", "extension"] if include_extension else ["ok"]),
        Book.subject == textbook.subject,
        Book.grade == textbook.grade,
        Book.semester == textbook.semester,
    ).join(Book, Question.book_id == Book.id)

    pool: dict[str, list[Question]] = defaultdict(list)
    for q in session.scalars(stmt).all():
        kps = set(q.knowledge_points or [])
        in_units = q.unit_id in units
        in_scope = kps and kps.issubset(kp_scope)
        if not (in_units or in_scope):
            continue
        if q.scope_status == "extension" and q.unit_id not in units:
            continue  # 提优题只在明确属于所选单元时可用
        pool[q.q_type].append(q)
    return pool


def _pick(group: list[Question], n: int, rng: random.Random,
          used_stems: set[str] | None = None) -> list[Question]:
    """知识点覆盖优先的随机抽取；尽量不让同一张卷出现重复题干。"""
    if used_stems is None:
        used_stems = set()
    if n >= len(group):
        chosen_all = [q for q in group if q.stem not in used_stems] or list(group)
        used_stems.update(q.stem for q in chosen_all)
        return chosen_all
    chosen: list[Question] = []
    seen_kp: set[str] = set()
    fresh = [q for q in group if q.stem not in used_stems]
    rest = fresh if len(fresh) >= n else list(group)
    while rest and len(chosen) < n:
        rng.shuffle(rest)
        rest.sort(key=lambda q: -len(set(q.knowledge_points or []) - seen_kp))
        pick = rest.pop(0)
        chosen.append(pick)
        used_stems.add(pick.stem)
        seen_kp.update(pick.knowledge_points or [])
    return chosen


def _assign_scores(items: list[tuple[str, float]], total: float,
                   per_type_score: dict[str, float]) -> list[float]:
    """按题型默认分值配分，再把总和精确调整到 total（0.5 分步进）。"""
    scores = [per_type_score.get(t, DEFAULT_SCORE_PER_TYPE.get(t, 2)) for t, _ in items]
    if not scores:
        return []
    factor = total / sum(scores)
    floors = [round(s * factor * 2) / 2 for s in scores]  # 按 0.5 分取整，符合小学卷习惯
    diff = round((total - sum(floors)) * 2)
    step = 0.5 if diff >= 0 else -0.5
    i = 0
    n = len(floors)
    while diff != 0 and n:
        floors[i % n] += step
        diff -= 1 if diff > 0 else -1
        i += 1
    return floors


def generate_paper(session: Session, cfg) -> Paper:
    if getattr(cfg, "question_ids", None):
        return generate_from_questions(session, cfg)

    if not getattr(cfg, "textbook_id", None):
        raise ValueError("自动组卷需要指定 textbook_id")
    textbook = session.get(Book, cfg.textbook_id)
    if textbook is None or textbook.role != "textbook":
        raise ValueError("textbook_id 必须指向一册课本（role=textbook）")

    all_units = session_units(textbook)
    if not all_units:
        raise ValueError("该课本还没有单元目录，请先做目录级拆解")
    unit_to = cfg.unit_to or cfg.unit_from
    unit_ids = [u.id for u in all_units if cfg.unit_from <= u.seq <= unit_to]
    if not unit_ids:
        raise ValueError(f"单元范围 {cfg.unit_from}~{unit_to} 不存在（该课本共 {len(all_units)} 个单元）")

    pool = _candidates(session, textbook, unit_ids, cfg.include_extension)
    rng = random.Random(cfg.seed)
    picked: list[Question] = []
    shortage: list[str] = []
    used_stems: set[str] = set()
    for q_type in TYPE_ORDER:
        want = cfg.counts.get(q_type, 0)
        if want <= 0:
            continue
        got = _pick(pool.get(q_type, []), want, rng, used_stems)
        if len(got) < want:
            shortage.append(f"{q_type} 需 {want} 题，仅有 {len(got)} 题")
        picked.extend(got)
    if cfg.counts and not picked:
        raise NotEnoughQuestions("按当前配额没有抽到任何题：" + "；".join(shortage))

    unit_names = {u.id: u.name for u in all_units}
    books = {b.id: b for b in session.scalars(select(Book)).all()}
    type_seq = {t: i for i, t in enumerate(TYPE_ORDER)}
    picked.sort(key=lambda q: (type_seq.get(q.q_type, 99), q.id))

    items = [(q.q_type, 0) for q in picked]
    scores = _assign_scores(items, cfg.total_score, cfg.per_type_score or {})

    paper = Paper(
        title=cfg.title or _default_title(textbook, cfg),
        textbook_id=textbook.id,
        subject=textbook.subject, grade=textbook.grade, semester=textbook.semester,
        unit_from=cfg.unit_from, unit_to=unit_to, unit_ids=unit_ids,
        total_score=cfg.total_score, duration_minutes=cfg.duration_minutes,
        include_extension=cfg.include_extension,
        config={"counts": cfg.counts, "seed": cfg.seed,
                "shortage": shortage, "per_type_score": cfg.per_type_score or {}},
    )
    session.add(paper)
    session.flush()
    for i, (q, score) in enumerate(zip(picked, scores), start=1):
        session.add(PaperQuestion(
            paper_id=paper.id, question_id=q.id, seq=i, score=score,
            snapshot=_snapshot(q, books[q.book_id], unit_names.get(q.unit_id)),
        ))
    session.flush()
    paper.config = {**(paper.config or {}), "shortage": shortage}
    return paper


def generate_from_questions(session: Session, cfg) -> Paper:
    """手动选题：完全按用户勾选的顺序与题目组卷（跳过单元范围与随机抽取）。"""
    ids: list[int] = []
    for raw in cfg.question_ids:
        try:
            qid = int(raw)
        except (TypeError, ValueError):
            continue
        if qid not in ids:
            ids.append(qid)          # 去重但保留选择顺序
    if not ids:
        raise ValueError("question_ids 为空")

    found = {q.id: q for q in session.scalars(select(Question).where(Question.id.in_(ids))).all()}
    missing = [i for i in ids if i not in found]
    if missing:
        raise ValueError(f"以下题目不存在或已被删除：{missing}")
    picked = [found[i] for i in ids]

    books = {b.id: b for b in session.scalars(select(Book)).all()}
    first = picked[0]
    textbook = session.get(Book, cfg.textbook_id) if getattr(cfg, "textbook_id", None) else None
    if textbook is None:
        textbook = books.get(first.book_id)
    if textbook is None:
        raise ValueError("题目所属书册缺失，无法组卷")

    unit_names: dict[int, str | None] = {}
    for q in picked:
        if q.unit_id and q.unit_id not in unit_names:
            unit = session.get(Unit, q.unit_id)
            unit_names[q.unit_id] = unit.name if unit else None

    items = [(q.q_type, 0) for q in picked]
    scores = _assign_scores(items, cfg.total_score, cfg.per_type_score or {})

    all_units = {u.id: u for u in session.scalars(select(Unit)).all()}
    unit_ids = sorted({q.unit_id for q in picked if q.unit_id})
    seqs = [all_units[u].seq for u in unit_ids if u in all_units]
    unit_from = min(seqs) if seqs else 1
    unit_to = max(seqs) if seqs else unit_from

    paper = Paper(
        title=cfg.title or f"{textbook.grade}年级{textbook.subject}{textbook.semester} 自选卷",
        textbook_id=textbook.id,
        subject=textbook.subject, grade=textbook.grade, semester=textbook.semester,
        unit_from=unit_from, unit_to=unit_to, unit_ids=unit_ids,
        total_score=cfg.total_score, duration_minutes=cfg.duration_minutes,
        include_extension=True,
        config={"source": "manual", "question_ids": ids,
                "per_type_score": cfg.per_type_score or {}, "shortage": []},
    )
    session.add(paper)
    session.flush()
    for i, (q, score) in enumerate(zip(picked, scores), start=1):
        book = books.get(q.book_id) or textbook
        session.add(PaperQuestion(
            paper_id=paper.id, question_id=q.id, seq=i, score=score,
            snapshot=_snapshot(q, book, unit_names.get(q.unit_id)),
        ))
    session.flush()
    return paper


def _default_title(textbook: Book, cfg) -> str:
    span = f"第{cfg.unit_from}~{unit_to_label(cfg.unit_to)}单元" if cfg.unit_to and cfg.unit_to != cfg.unit_from \
        else f"第{cfg.unit_from}单元"
    return f"{textbook.grade}年级{textbook.subject}{textbook.semester} {span}阶段自测卷"


def unit_to_label(unit_to: int | None) -> int:
    return unit_to or 0


def replace_question(session: Session, paper: Paper, paper_question_id: int) -> PaperQuestion | None:
    """把卷中一题换成同题型、同范围、未用过的新题。返回替换后的新条目。"""
    item = session.get(PaperQuestion, paper_question_id)
    if item is None or item.paper_id != paper.id:
        raise ValueError("paper_question 不属于这张卷")
    old = session.get(Question, item.question_id) if item.question_id else None
    q_type = (old.q_type if old else item.snapshot.get("q_type")) or "其他"

    pool = _candidates(session, session.get(Book, paper.textbook_id),
                       paper.unit_ids, paper.include_extension)
    used = {i.question_id for i in paper.items}
    cands = [q for q in pool.get(q_type, []) if q.id not in used]
    if not cands:
        return None
    if old:
        cands.sort(key=lambda q: (abs(q.difficulty - old.difficulty), q.id))
    best_diff = cands[0].difficulty
    cands = [q for q in cands if q.difficulty == best_diff]
    unit_names = {u.id: u.name for b in [session.get(Book, paper.textbook_id)]
                  for u in session_units(b)}
    books = {b.id: b for b in session.scalars(select(Book)).all()}
    q = random.choice(cands)
    new_item = PaperQuestion(
        paper_id=paper.id, question_id=q.id, seq=item.seq, score=item.score,
        snapshot=_snapshot(q, books[q.book_id], unit_names.get(q.unit_id)),
    )
    session.delete(item)
    session.flush()
    session.add(new_item)
    session.flush()
    return new_item
