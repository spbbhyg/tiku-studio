"""P0 校验：确认题库的 导入→取草稿 qid→PATCH 出题人标签→读回→回滚 这条链路可用。

只调 HTTP 接口，不碰题库文件；跑完必须回滚，保证题库仍是 204 题 / 0 草稿 / 3 卷。
"""
import json
import sys

import httpx

BASE = "http://127.0.0.1:8080"
TEXT = """===题目===
标题: __P0自检__ 标签回读
专题: 有机化学
年份: 2025
===题干===
测试题干：苯的分子式是（  ）
===小问===
1.1 写出苯的结构简式
===答案===
1.1 一个六元环
"""

c = httpx.Client(base_url=BASE, timeout=20.0)
before = c.get("/api/health").json()["stats"]
print("before:", before)

res = c.post("/api/admin/import/paste",
             json={"text": TEXT, "topic": "organic", "paperTitle": "__P0自检批次"}).json()
bid = res["batch"]
print("batch:", res)

drafts = c.get("/api/admin/drafts").json()
qids = [d["id"] for d in drafts if d.get("paper_id") == f"import-{bid}"]
print("qids:", qids)
assert qids, "没找到刚导入的草稿"

qid = qids[0]
c.patch(f"/api/admin/questions/{qid}", json={"tags": ["出题人：__P0_TEST__"]}).raise_for_status()
detail = c.get(f"/api/admin/questions/{qid}").json()
print("tags:", detail.get("tags"), "| topic:", detail.get("topicId"), "| title:", detail.get("title"))
print("parts:", [p.get("originLabel") for p in (detail.get("parts") or [])])

rb = c.post(f"/api/admin/batches/{bid}/rollback").json()
print("rollback:", rb)
after = c.get("/api/health").json()["stats"]
print("after:", after)
ok = detail.get("tags") and "出题人：__P0_TEST__" in detail["tags"] and after == before
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
