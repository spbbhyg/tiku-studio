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


def test_ai_config_roundtrip_and_no_key_error():
    """AI 配置可保存/读取/清除；没配 Key 时翻译给出可操作的中文提示。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.get("/api/ai/status")
        assert r.status_code == 200
        body = r.json()
        assert body["model"] and body["base_url"]
        assert body["has_key"] is False

        r = c.post("/api/ai/config", json={
            "api_key": "sk-test-1234567890", "base_url": "https://api.deepseek.com", "model": "deepseek-chat",
        })
        assert r.status_code == 200
        assert r.json()["configured"] is True
        assert "sk-test" not in r.json()["key_hint"]

        assert c.get("/api/ai/config").json()["has_key"] is True
        assert c.post("/api/ai/config", json={"clear_key": True}).status_code == 200
        assert c.get("/api/ai/status").json()["has_key"] is False

        r = c.post("/api/ai/translate", json={"text": "hello"})
        assert r.status_code == 400
        assert "API Key" in r.json()["detail"]


def test_manual_docs_list_is_safe_when_empty():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.get("/api/manual/docs")
        assert r.status_code == 200
        assert isinstance(r.json()["docs"], list)


def test_ai_summarize_and_answer_validation():
    """逐页精读与生成答案的接口存在，且对无效输入给出明确错误。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/ai/summarize-page", json={"doc_id": "000000000000", "page_no": 1})
        assert r.status_code == 404

        r = c.post("/api/ai/answer", json={"stem": "   "})
        assert r.status_code == 400
        assert "题干" in r.json()["detail"]


def test_ai_paper_generation_and_merge(monkeypatch):
    """AI 命题直接进试卷（question_id 为空），融合时第二套大题号顺延。"""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import ai as ai_svc

    def fake_questions(db, source, count=5, q_type="", difficulty=0):
        return {
            "questions": [{
                "q_type": "综合", "stem": "1-1 测试题干\n1-2 第二小问",
                "answer": "1-1 答案\n1-2 答案", "analysis": "解析",
                "knowledge_points": ["有机化学"], "score": 10,
            }],
            "usage": {}, "model": "fake",
        }

    monkeypatch.setattr(ai_svc, "generate_questions", fake_questions)
    with TestClient(app) as c:
        p1 = c.post("/api/papers/ai", json={"source": "素材1", "count": 1, "title": "AI卷1"})
        assert p1.status_code == 200, p1.text
        p2 = c.post("/api/papers/ai", json={"source": "素材2", "count": 1, "title": "AI卷2"})
        assert p2.status_code == 200, p2.text
        b1, b2 = p1.json(), p2.json()
        assert b1["items"][0]["question_id"] is None
        m = c.post("/api/papers/merge", json={"paper_ids": [b1["id"], b2["id"]], "title": "融合卷"})
        assert m.status_code == 200, m.text
        data = m.json()
        assert data["title"] == "融合卷"
        assert len(data["items"]) == 2
        assert data["items"][0]["snapshot"]["stem"].startswith("1-1")
        assert data["items"][1]["snapshot"]["stem"].startswith("2-1")


def test_ai_free_gateway_preserves_paid_config():
    """切换本机免费模型不会丢掉已保存的付费 Key，且可以切回。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/ai/config", json={
            "api_key": "sk-paid-test",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
        })
        assert r.status_code == 200 and r.json()["paid_configured"] is True

        r = c.post("/api/ai/config", json={"free_gateway": True})
        assert r.status_code == 200, r.text
        free_on = r.json()
        assert free_on["free_enabled"] is True
        assert free_on["paid_configured"] is True
        assert free_on["has_key"] is True
        if free_on["free_gateway"]:
            assert free_on["source"] == "dsh_gateway"
            assert "8326" in free_on["base_url"]
            assert free_on["model"].startswith("our-free-model/")
        else:
            # 本机没装免费模型插件时，自动回退到付费配置
            assert free_on["source"] == "db"
            assert free_on["base_url"] == "https://api.deepseek.com"

        r = c.post("/api/ai/config", json={
            "free_gateway": False,
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
        })
        restored = r.json()
        assert restored["free_enabled"] is False
        assert restored["paid_configured"] is True
        assert restored["has_key"] is True
        assert restored["base_url"] == "https://api.deepseek.com"
        assert restored["model"] == "deepseek-chat"


def test_ai_site_chat_endpoint(monkeypatch):
    """首页 AI 向导：校验入参并把站内资料交给模型。"""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import ai as ai_svc

    seen = {}

    def fake_chat(db, messages, **kwargs):
        seen["messages"] = messages
        return "先去「出题」上传 PDF，框选后保存到题库。", {"model": "fake"}

    monkeypatch.setattr(ai_svc, "_chat", fake_chat)
    with TestClient(app) as c:
        r = c.post("/api/ai/chat", json={
            "messages": [{"role": "user", "content": "怎么从 PDF 出一道题？"}],
            "page": "首页",
        })
        assert r.status_code == 200, r.text
        assert "出题" in r.json()["reply"]
        system_prompt = seen["messages"][0]["content"]
        assert system_prompt.startswith("你叫「小卷」")
        assert "站内 AI 助教" in system_prompt
        assert "新手上手顺序" in system_prompt
        assert seen["messages"][-1]["content"] == "怎么从 PDF 出一道题？"

        empty = c.post("/api/ai/chat", json={"messages": []})
        assert empty.status_code == 422

        assistant_only = c.post("/api/ai/chat", json={
            "messages": [{"role": "assistant", "content": "你好"}],
        })
        assert assistant_only.status_code == 400


def test_ai_chat_stream_and_search_sources(monkeypatch):
    """首页流式对话：SSE 逐块输出；开启搜索时先返回来源。"""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import ai as ai_svc
    from app.services import web_search as search_svc

    monkeypatch.setattr(search_svc, "search", lambda q, limit=5: {
        "ok": True,
        "query": q,
        "results": [{"title": "示例结果", "url": "https://example.com/a", "snippet": "摘要", "published": ""}],
        "error": "",
    })

    def fake_stream(db, messages, **kwargs):
        yield "先看"
        yield "「出题」页。"

    monkeypatch.setattr(ai_svc, "_chat_stream", fake_stream)
    monkeypatch.setattr(ai_svc, "_chat", lambda *a, **k: ("先看「出题」页。", {"model": "fake"}))
    with TestClient(app) as c:
        with c.stream("POST", "/api/ai/chat/stream", json={
            "messages": [{"role": "user", "content": "最新有什么进展？"}],
            "search": True,
        }) as r:
            assert r.status_code == 200
            body = "".join(r.iter_text())
        assert "event: meta" in body
        assert "https://example.com/a" in body
        assert "event: delta" in body
        assert "event: done" in body
        assert "先看" in body and "「出题」页。" in body

        direct = c.post("/api/ai/chat", json={
            "messages": [{"role": "user", "content": "测试"}],
            "search": True,
        })
        assert direct.status_code == 200, direct.text
        assert direct.json()["sources"][0]["url"] == "https://example.com/a"


def test_web_search_parse_rss():
    from app.services.web_search import parse_rss

    xml = """<?xml version="1.0" encoding="utf-8"?>
    <rss><channel>
      <item><title>标题一</title><link>https://example.com/1</link>
        <description>&lt;b&gt;摘要&lt;/b&gt; 内容</description><pubDate>today</pubDate></item>
      <item><title>标题二</title><link>https://example.com/2</link><description>第二条</description></item>
    </channel></rss>"""
    rows = parse_rss(xml)
    assert len(rows) == 2
    assert rows[0]["title"] == "标题一"
    assert rows[0]["snippet"] == "摘要 内容"
    assert rows[1]["url"] == "https://example.com/2"



def test_ai_paper_preview_build_and_print_parts(monkeypatch):
    """分步出题：预览不建卷 → 人工改题干 → build 成卷；打印视图支持题目卷/答案卷。"""
    from fastapi.testclient import TestClient
    from app.main import app

    def fake_generate(db, source, count=5, q_type="", difficulty=0):
        return {"questions": [{
            "stem": "1-1 原题干", "answer": "原答案", "analysis": "原解析",
            "q_type": "综合", "difficulty": 3, "knowledge_points": ["有机化学"], "score": 12,
        }], "usage": {"model": "fake"}, "model": "fake"}

    from app.services import ai
    monkeypatch.setattr(ai, "generate_questions", fake_generate)

    with TestClient(app) as c:
        r = c.post("/api/papers/ai/preview", json={"source": "素材", "count": 1})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["items"][0]["snapshot"]["stem"] == "1-1 原题干"

        items = data["items"]
        items[0]["snapshot"]["stem"] = "1-1 人工改过的题干"
        r2 = c.post("/api/papers/ai/build", json={"title": "测试分步卷", "items": items})
        assert r2.status_code == 200, r2.text
        paper = r2.json()
        pid = paper["id"]
        try:
            assert paper["items"][0]["snapshot"]["stem"] == "1-1 人工改过的题干"

            qv = c.get(f"/api/papers/{pid}/view?part=questions")
            assert qv.status_code == 200
            assert "测试分步卷" in qv.text and "参考答案（家长用）" not in qv.text
            av = c.get(f"/api/papers/{pid}/view?part=answers")
            assert av.status_code == 200 and "参考答案（家长用）" in av.text

            pqid = paper["items"][0]["id"]
            r3 = c.patch(f"/api/papers/{pid}/items", json={
                "title": "测试分步卷（改）",
                "items": [{"id": pqid, "stem": "打印页改的题干", "answer": "打印页改的答案"}],
            })
            assert r3.status_code == 200, r3.text
            assert r3.json()["title"] == "测试分步卷（改）"
            assert r3.json()["items"][0]["snapshot"]["answer"] == "打印页改的答案"
        finally:
            c.delete(f"/api/papers/{pid}")


def test_removed_features_are_gone():
    """整卷导入 / 本项目导入 / AI 拆题 / 错题集 已在二期里删掉，接口应当 404。"""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        # 注意：路由删掉后请求会落到末尾的 StaticFiles 挂载，POST 得到 405、GET 得到 404
        gone = (404, 405)
        assert c.post("/api/import/papers", files=[("files", ("a.txt", b"x", "text/plain"))]).status_code in gone
        assert c.post("/api/wb/parse-preview").status_code in gone
        assert c.post("/api/wb/parse-import", json={}).status_code in gone
        assert c.post("/api/wb/extract").status_code in gone
        assert c.get("/api/wb/wrongbooks").status_code in gone
        assert c.get("/api/wb/wrongbooks/1/export").status_code in gone
        # 留着的接口不能被误删
        assert c.get("/api/wb/settings/persona").status_code == 200


def test_lan_guard_needs_code_from_other_machines():
    """局域网访问码：别的机器没带码进不来，带了码能进；本机不受影响。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        c.post("/api/lan/code", json={"code": "abc123"})
    try:
        with TestClient(app, client=("192.168.1.50", 51234)) as remote:
            assert remote.get("/api/stats").status_code == 403
            assert remote.get("/api/stats?c=abc123").status_code == 200
            # 码记进 cookie 以后就不用再带
            assert remote.get("/api/stats").status_code == 200
            assert remote.get("/api/stats?c=wrong").status_code in (200, 403)
        with TestClient(app) as local:
            assert local.get("/api/stats").status_code == 200
    finally:
        with TestClient(app) as c:
            c.post("/api/lan/code", json={"code": ""})
