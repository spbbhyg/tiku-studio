"""本机联网搜索：用 Bing 的 RSS 结果，不需要 API Key。

只取标题、链接和摘要，不抓网页正文。结果供 AI 参考，最终答案仍需用户自己核对。
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET

import httpx

BING_RSS = "https://cn.bing.com/search"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _plain(text: str | None, limit: int = 400) -> str:
    raw = html.unescape(text or "")
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:limit]


def parse_rss(xml_text: str, limit: int = 6) -> list[dict]:
    root = ET.fromstring(xml_text)
    out: list[dict] = []
    for item in root.findall(".//item"):
        title = _plain(item.findtext("title"), 160)
        link = (item.findtext("link") or "").strip()
        snippet = _plain(item.findtext("description"), 500)
        if not title or not link:
            continue
        out.append({
            "title": title,
            "url": link,
            "snippet": snippet,
            "published": _plain(item.findtext("pubDate"), 60),
        })
        if len(out) >= limit:
            break
    return out


def search(query: str, limit: int = 5) -> dict:
    q = (query or "").strip()
    if not q:
        return {"ok": False, "query": "", "results": [], "error": "搜索词为空"}
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(
                BING_RSS,
                params={"format": "rss", "q": q.replace("\n", " ")[:300]},
                headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
            )
    except httpx.HTTPError as exc:
        return {"ok": False, "query": q, "results": [], "error": f"搜索请求失败：{exc}"}
    if resp.status_code >= 400:
        return {"ok": False, "query": q, "results": [], "error": f"搜索返回 {resp.status_code}"}
    try:
        results = parse_rss(resp.text, limit=limit)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "query": q, "results": [], "error": f"搜索结果解析失败：{exc}"}
    if not results:
        return {"ok": False, "query": q, "results": [], "error": "没有搜到结果"}
    return {"ok": True, "query": q, "results": results, "error": ""}
