"""试卷/答案结构化：按「第N题」切题，并用答案卷同题号回填答案。

不依赖 AI，纯文本处理；遇到没有「第N题」标记的稿子退回整段。
"""
from __future__ import annotations

import re

HEAD = re.compile(r"第\s*(\d{1,2})\s*题\s*([^\n（(]{0,40})[（(]([^）)]{0,20})[）)]")
SUB = re.compile(r"(?m)^\s*(\d{1,2}\s*[-－.]\s*\d{1,2})\s*")
# pdf_to_text 会在每页开头插入「=== 第 N 页 ===」标记，有时还跟正文同行
PAGEMARK = re.compile(r"=+\s*第\s*\d+\s*页\s*=+\s*")


def _clean(s: str) -> str:
    s = s.replace("\u3000", " ")
    s = PAGEMARK.sub("", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def split_by_question(text: str) -> list[dict]:
    """按「第 N 题…」切分成 [{no, title, score, body}]。"""
    text = _clean(text)
    marks = list(HEAD.finditer(text))
    if not marks:
        return [{"no": 1, "title": "", "score": "", "body": text}] if text else []

    out: list[dict] = []
    for i, m in enumerate(marks):
        start = m.start()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[start:end].strip()
        # 去掉题头本身，body 只留正文
        body = body[m.end() - start:].strip()
        out.append({
            "no": int(m.group(1)),
            "title": (m.group(2) or "").strip(),
            "score": (m.group(3) or "").strip(),
            "body": body,
        })
    return out


def _strip_prefix(stem: str, ans: str) -> str:
    """答案卷常把题干原样重复一遍：把共同前缀（忽略空白）切掉。"""
    ns = re.sub(r"\s+", "", stem or "")
    na = re.sub(r"\s+", "", ans or "")
    if not ns or not na:
        return ans
    lo, hi = 0, min(len(ns), len(na))
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if na.startswith(ns[:mid]):
            lo = mid
        else:
            hi = mid - 1
    if lo < 40:
        return ans
    cnt = pos = 0
    while pos < len(ans) and cnt < lo:
        if not ans[pos].isspace():
            cnt += 1
        pos += 1
    return ans[pos:].lstrip()


def pair(qtext: str, atext: str = "") -> list[dict]:
    """题目卷 + 答案卷合并：同题号把答案文本挂上。"""
    qs = split_by_question(qtext)
    by_no = {q["no"]: q for q in qs}
    if atext.strip():
        for a in split_by_question(atext):
            t = by_no.get(a["no"])
            if not t:
                continue
            t["answer"] = _clean(_strip_prefix(t["body"], a["body"]))
    for q in qs:
        q.setdefault("answer", "")
        q["stem"] = q.pop("body")
    return qs
