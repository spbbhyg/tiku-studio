"""核心链路测试：bundle 校验 → 导入 → 对纲校验 → 自动组卷 / 自选组卷 → 换题。

跑法：`python -m pytest apps/api/tests -q`
测试使用独立的临时数据库，绝不触碰 data/tiku.db；夹具在 tests/fixtures/ 下，
是「1 册课本纲（2 单元）+ 8 道题」的最小可用集合。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="tiku-test-")
os.environ["TIKU_DB_PATH"] = str(Path(_TMP) / "test.db")
os.environ["TIKU_DATA_DIR"] = _TMP

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

import pytest  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Book, Paper, Question  # noqa: E402
from app.schemas import PaperGenerate  # noqa: E402
from app.services import paper_builder  # noqa: E402
from app.services.bundle_import import import_bundle_file, validate_bundle  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FILES = sorted(FIXTURES.glob("*.json"))


@pytest.fixture(scope="module", autouse=True)
def _db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(scope="module")
def seeded():
    with SessionLocal() as db:
        for p in FILES:
            import_bundle_file(db, p)
        db.commit()
        yield db


def test_fixtures_validate():
    kinds = {p.name: validate_bundle(json.loads(p.read_text(encoding="utf-8"))) for p in FILES}
    assert set(kinds.values()) == {"catalog", "question"}


def test_import_counts(seeded):
    books = seeded.scalars(select(Book)).all()
    assert len(books) == 2  # 1 册课本纲 + 1 本自编卷
    textbook = seeded.scalar(select(Book).where(Book.role == "textbook"))
    assert textbook is not None and len(textbook.units) == 2
    assert seeded.scalar(select(func.count()).select_from(Question)) == 8


def test_scope_classification(seeded):
    """对纲校验：题都落在单元范围内，不应出现 pending。"""
    rows = seeded.execute(select(Question.scope_status, Question.unit_id)).all()
    assert all(unit_id is not None for _, unit_id in rows)
    statuses = {s for s, _ in rows}
    assert "pending" not in statuses
    assert statuses <= {"ok", "extension"}


def test_generate_paper_auto(seeded):
    textbook = seeded.scalar(select(Book).where(Book.role == "textbook"))
    paper = paper_builder.generate_paper(seeded, PaperGenerate(
        textbook_id=textbook.id, unit_from=1, unit_to=2,
        counts={"填空": 4, "连线": 1, "应用": 1},
        total_score=100, duration_minutes=60, seed=7,
    ))
    seeded.commit()
    types = [it.snapshot["q_type"] for it in paper.items]
    assert types.count("填空") == 4
    assert sum(it.score for it in paper.items) == 100
    stems = [it.snapshot["stem"] for it in paper.items]
    assert len(stems) == len(set(stems)), "同一张卷不应出现重复题干"
    assert paper.config.get("source") != "manual"


def test_generate_paper_from_picks(seeded):
    """自选组卷：按传入顺序出题，分值与总分对得上。"""
    ids = [q.id for q in seeded.scalars(select(Question).order_by(Question.id)).all()][:3]
    picked = list(reversed(ids))
    paper = paper_builder.generate_paper(seeded, PaperGenerate(
        question_ids=picked, total_score=12, duration_minutes=30,
        per_type_score={"填空": 3, "连线": 2, "应用": 5}, title="自选测试卷",
    ))
    seeded.commit()
    assert [it.question_id for it in paper.items] == picked
    assert sum(it.score for it in paper.items) == 12
    assert paper.config.get("source") == "manual"
    assert paper.config.get("question_ids") == picked


def test_replace_question(seeded):
    paper = seeded.scalars(select(Paper).order_by(Paper.id)).first()
    item = paper.items[0]
    old_qid = item.question_id
    new_item = paper_builder.replace_question(seeded, paper, item.id)
    seeded.commit()
    assert new_item is not None
    assert new_item.question_id != old_qid
    assert new_item.snapshot["q_type"] == item.snapshot["q_type"]


def test_reimport_is_idempotent(seeded):
    """重复导入同一 bundle 不应产生重复题。"""
    before = seeded.scalar(select(func.count()).select_from(Question))
    for p in FILES:
        import_bundle_file(seeded, p)
    seeded.commit()
    assert seeded.scalar(select(func.count()).select_from(Question)) == before
