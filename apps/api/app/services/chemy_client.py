"""本地题库（chemy，默认 http://127.0.0.1:8080）的 HTTP 客户端。

铁律：**只走 HTTP**。绝不读写题库目录下的任何文件（`data/chemy.db`、`config.json`、
`chemy/*.py` 都不碰），题库改版也不会影响这里——它只是对方的公开接口。

题库的 `require_admin()` 对"本机请求"直接放行，所以本机调用管理接口天然合法；
只有把服务放到别的机器上时才需要 `TIKU_CHEMY_TOKEN`。
"""
from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_BASE = "http://127.0.0.1:8080"
TIMEOUT = 20.0


class ChemyError(RuntimeError):
    """题库不可达或返回错误。消息可直接展示给老师。"""


def base_url() -> str:
    return (os.environ.get("TIKU_CHEMY_BASE") or DEFAULT_BASE).rstrip("/")


def _headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    token = os.environ.get("TIKU_CHEMY_TOKEN", "").strip()
    if token:
        h["x-admin-token"] = token
    return h


def _detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return (resp.text or "").strip()[:300] or resp.reason_phrase
    if isinstance(data, dict) and data.get("detail"):
        d = data["detail"]
        return d if isinstance(d, str) else str(d)
    return str(data)[:300]


def call(method: str, path: str, *, json_body: Any = None,
         params: dict | None = None, files: dict | None = None,
         data: dict | None = None, raw: bool = False, timeout: float | None = None):
    """调用题库接口。连接失败翻译成人话，4xx/5xx 抛出题库自己的说明。"""
    url = base_url() + path
    try:
        with httpx.Client(base_url=base_url(), timeout=timeout or TIMEOUT,
                          headers=_headers(), follow_redirects=True) as client:
            resp = client.request(method, path, json=json_body, params=params,
                                  files=files, data=data)
    except httpx.HTTPError as exc:
        raise ChemyError(f"连不上本地题库（{url}）：请先启动题库再试") from exc
    if resp.status_code >= 400:
        raise ChemyError(f"题库拒绝了请求（{resp.status_code}）：{_detail(resp)}")
    if raw:
        return resp
    try:
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        raise ChemyError(f"题库返回了非 JSON 内容（{resp.status_code}）") from exc


# ---------- 只读 ----------

def health() -> dict:
    return call("GET", "/api/health", timeout=4.0)


def stats() -> dict:
    return call("GET", "/api/stats")


def topics() -> list[dict]:
    return call("GET", "/api/topics")


def taxonomy() -> dict[str, list[dict]]:
    return call("GET", "/api/taxonomy")


def search(q: str = "", topic: str = "", page: int = 1, page_size: int = 20,
           scope: str = "") -> dict:
    params = {"q": q, "page": page, "pageSize": page_size}
    if topic:
        params["topic"] = topic
    if scope:
        params["scope"] = scope
    return call("GET", "/api/search", params=params)


def tree(topic: str) -> list:
    """某个专题下的全部题目（按题号）。题库的搜索必须带关键词，浏览专题用这个。"""
    return call("GET", "/api/tree", params={"topic": topic})


def all_questions() -> list:
    """全部已发布题目：题库没有「列全部」的接口，按专题逐个取再合并。"""
    out: list = []
    names = {t["id"]: t.get("name", t["id"]) for t in topics()}
    for tid, name in names.items():
        try:
            rows = tree(tid)
        except ChemyError:
            continue
        for r in rows:
            r = dict(r)
            r["topic_id"] = tid
            r["topic_name"] = name
            out.append(r)
    return out


def question(qid: str) -> dict:
    """题目详情。公开接口只给已发布的，草稿回落到管理接口。"""
    try:
        return call("GET", f"/api/questions/{qid}")
    except ChemyError:
        return call("GET", f"/api/admin/questions/{qid}")


def batch_questions(ids: list[str]) -> list[dict]:
    return call("POST", "/api/questions/batch", json_body={"ids": ids})


def drafts() -> list[dict]:
    return call("GET", "/api/admin/drafts")


# ---------- 写入（都走题库自己的管理接口） ----------

def import_paste(text: str, topic: str, paper_title: str) -> dict:
    """粘贴导入 → 生成一个 draft 批次，返回 {batch, count}。"""
    return call("POST", "/api/admin/import/paste",
                json_body={"text": text, "topic": topic, "paperTitle": paper_title})


def publish(batch: str) -> dict:
    return call("POST", f"/api/admin/batches/{batch}/publish")


def rollback(batch: str) -> dict:
    return call("POST", f"/api/admin/batches/{batch}/rollback")


def patch_question(qid: str, **fields) -> dict:
    return call("PATCH", f"/api/admin/questions/{qid}", json_body=fields)


def upload_figure(qid: str, filename: str, content: bytes, content_type: str = "image/png") -> dict:
    return call("POST", f"/api/admin/questions/{qid}/figures",
                files={"file": (filename, content, content_type)}, timeout=60.0)


def delete_question(qid: str) -> dict:
    """删除一道题（题库侧是神奋加的补丁接口）。"""
    return call("DELETE", f"/api/admin/questions/{qid}", timeout=30.0)
