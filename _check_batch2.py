import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

with TestClient(app) as c:
    r = c.get("/api/questions", params={"ids": "1,2,3", "page_size": 50})
    print("ids filter", r.status_code, len(r.json().get("items", [])))
    ids = [q["id"] for q in r.json()["items"]]
    print("ids", ids)

    chem = c.get("/api/questions", params={"q": "化学竞赛", "page_size": 200})
    print("chem search", chem.status_code, chem.json().get("total"))
    seed_ids = [q["id"] for q in chem.json().get("items", [])][:4]
    print("seed ids", seed_ids)

    if seed_ids:
        paper = c.post("/api/papers", json={
            "question_ids": seed_ids,
            "total_score": sum(3 for _ in seed_ids),
            "duration_minutes": 60,
            "per_type_score": {},
        })
        print("paper", paper.status_code, paper.json().get("id"), paper.json().get("title"))
        if paper.status_code == 200:
            print("paper qcount", len(paper.json().get("questions", [])))

    layout = {"blocks": [
        {"type": "text", "text": "下列哪一种试剂可用于鉴别乙醛和丙酮？请说明实验现象。", "x": 48, "y": 48, "w": 480, "size": 14},
    ], "meta": {}}
    compose = c.post("/api/authoring/compose", json={
        "layout": layout,
        "q_type": "单选",
        "difficulty": 1,
        "tags": ["有机化学"],
        "book_id": 2,
        "auto_classify": True,
        "status": "draft",
    })
    print("compose", compose.status_code)
    data = compose.json()
    print("classified", json.dumps(data.get("classified"), ensure_ascii=False)[:300])
    print("q_type", data.get("q_type"), "difficulty", data.get("difficulty"), "tags", data.get("tags"))
