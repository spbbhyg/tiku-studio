"""文献重点句识别（纯本地，不调用 AI）。

移植自文献精读原型（bolt「重点句 + 章节导航」）：
1. 按「摘要 / 引言 / 方法 / 结果 / 讨论 / 结论」等小标题切章节；
2. 用「本研究提出…」「结果表明…」这类句式与术语打分，挑出高价值句；
3. 句子带上它在 PDF 上的行矩形（归一化 0~1），前端据此在页面上画出下划线与编号。

不联网、不依赖 Key，扫描件没有文字层时返回空结构，由前端提示改用裁剪或 AI 精读。
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

import fitz

from ..config import LIBRARY_DIR

DOC_ID_RE = re.compile(r"^[0-9a-f]{12}$")

SECTION_LABELS: dict[str, str] = {
    "abstract": "摘要",
    "intro": "引言",
    "method": "方法",
    "result": "结果",
    "discussion": "讨论",
    "conclusion": "结论",
    "other": "其他",
}
SECTION_ORDER = ["abstract", "intro", "method", "result", "discussion", "conclusion", "other"]

_NUMBERING = r"(?:[IVX]+\.?\s*|\d+(?:\.\d+)*\.?\s*)?"
_END = r"(?=[\s:：.、]|$)"

HEADING_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("abstract", re.compile(rf"^{_NUMBERING}(abstract|摘\s*要){_END}", re.I)),
    ("intro", re.compile(rf"^{_NUMBERING}(introduction|引\s*言|研究背景|背\s*景){_END}", re.I)),
    ("method", re.compile(
        rf"^{_NUMBERING}(materials?\s+and\s+methods?|methods?|methodology|"
        rf"experimental(\s+section)?|实验方法|研究方法|方\s*法|材料与方法|实验部分){_END}", re.I)),
    ("result", re.compile(rf"^{_NUMBERING}(results?|findings?|结\s*果){_END}", re.I)),
    ("discussion", re.compile(rf"^{_NUMBERING}(discussion|讨\s*论){_END}", re.I)),
    ("conclusion", re.compile(
        rf"^{_NUMBERING}(conclusions?|concluding\s+remarks|结\s*论|总\s*结|小结){_END}", re.I)),
    ("other", re.compile(
        rf"^{_NUMBERING}(references?|bibliography|acknowledg\w*|"
        rf"参考文献|致\s*谢|附录|supplementary){_END}", re.I)),
]

SCORE_RULES: list[tuple[re.Pattern, float]] = [
    (re.compile(
        r"\b(we|this\s+(study|paper|work|article|review))\s+"
        r"(propose|present|show|find|found|demonstrate|develop|introduce|report|"
        r"observe|identify|aim|investigate|evaluate|examine)", re.I), 3.0),
    (re.compile(
        r"\b(results?|findings?|data)\s+(show|shows|indicate|indicates|suggest|suggests|"
        r"reveal|reveals|demonstrate|demonstrates)", re.I), 3.0),
    (re.compile(r"\b(conclude|concluded|in\s+summary|in\s+conclusion|overall|therefore|thus|hence)\b", re.I), 2.0),
    (re.compile(
        r"\b(significant(ly)?|outperform\w*|improv(e|es|ed|ement)|increas(e|es|ed)|"
        r"decreas(e|es|ed)|reduc(e|es|ed)|enhanc(e|es|ed))\b", re.I), 2.0),
    (re.compile(r"\b(purpose|objective|aim|goal|hypothesis|contribution)s?\b", re.I), 2.0),
    (re.compile(r"\b(novel|key|main|primary|critical|crucial|important|essential|central)\b", re.I), 1.5),
    (re.compile(r"\b(p\s*[<=]\s*0?\.\d+|\d+(\.\d+)?\s?%)", re.I), 1.5),
    (re.compile(r"\b(limitation|future\s+work|further\s+(research|studies))\b", re.I), 1.0),
    (re.compile(r"(研究表明|结果显示|结果表明|本文提出|我们提出|提出了|发现|显著|关键|主要|结论|"
                r"因此|从而|据此|由此可见)"), 2.0),
]

SECTION_BOOST = {"abstract": 1.0, "result": 1.0, "discussion": 1.0, "conclusion": 1.5}

SENTENCE_PATTERN = re.compile(r"[^.!?。！？]+(?:[.!?。！？]+[\"'”’)\]]*|$)")

MAX_PAGES = 60
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()


def _doc_file(doc_id: str) -> Path:
    if not DOC_ID_RE.match(doc_id or ""):
        raise ValueError("非法 doc_id")
    path = LIBRARY_DIR / "uploads" / f"{doc_id}.pdf"
    if not path.is_file():
        raise FileNotFoundError("文档不存在或已被清理，请重新上传")
    return path


def _page_lines(page: fitz.Page) -> tuple[list[dict], list[dict]]:
    """把一页文字按行/按字拆出来，坐标归一化到 0~1。

    返回 (lines, chars)：
    - lines: [{text, x0, y0, x1, y1}]（与 /api/manual/page/text 同构，用于标题识别）
    - chars: [{c, line, x0, y0, x1, y1}]（逐字矩形，用于给「一句话」精确画下划线与圈号；
      同一行里有几句时不会整行互相覆盖）
    """
    rect = page.rect
    w = float(rect.width) or 1.0
    h = float(rect.height) or 1.0
    try:
        raw = page.get_text("rawdict")
    except Exception:                                        # noqa: BLE001
        return [], []
    lines: list[dict] = []
    chars: list[dict] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            text = ""
            line_chars: list[dict] = []
            for span in spans:
                for ch in span.get("chars") or []:
                    c = str(ch.get("c") or "")
                    if not c:
                        continue
                    try:
                        cx0, cy0, cx1, cy1 = [float(v) for v in ch.get("bbox") or []]
                    except Exception:                    # noqa: BLE001
                        continue
                    if cx1 <= cx0 or cy1 <= cy0:
                        continue
                    line_chars.append({
                        "c": c,
                        "x0": round(cx0 / w, 5), "y0": round(cy0 / h, 5),
                        "x1": round(min(cx1, w) / w, 5), "y1": round(min(cy1, h) / h, 5),
                    })
                    text += c
            if not line_chars or not text.strip():
                continue
            try:
                x0, y0, x1, y1 = [float(v) for v in line.get("bbox") or []]
            except Exception:                            # noqa: BLE001
                continue
            if x1 <= x0 or y1 <= y0:
                continue
            index = len(lines)
            lines.append({
                "text": text,
                "x0": round(x0 / w, 5), "y0": round(y0 / h, 5),
                "x1": round(min(x1, w) / w, 5), "y1": round(min(y1, h) / h, 5),
            })
            for ch in line_chars:
                ch["line"] = index
                chars.append(ch)
    order = sorted(range(len(lines)), key=lambda i: (round(lines[i]["y0"], 3), lines[i]["x0"]))
    if order != list(range(len(lines))):
        remap = {old: new for new, old in enumerate(order)}
        lines = [lines[i] for i in order]
        for ch in chars:
            ch["line"] = remap[ch["line"]]
    return lines, chars


def _detect_headings(lines: list[dict], offsets: list[tuple[int, int]]) -> list[dict]:
    """在短行里找章节标题，返回 [{section,start,end}]（字符偏移）。"""
    headings: list[dict] = []
    for line, (start, end) in zip(lines, offsets):
        trimmed = line["text"].strip()
        if not trimmed or len(trimmed) > 60 or len(trimmed.split()) > 8:
            continue
        if trimmed.endswith((".", "。", "；", ";")):
            continue
        for key, pattern in HEADING_PATTERNS:
            if pattern.search(trimmed):
                headings.append({"section": key, "start": start, "end": end})
                break
    return headings


def _section_at(headings: list[dict], offset: int, carry: str) -> str:
    current = carry
    for heading in headings:
        if heading["start"] <= offset:
            current = heading["section"]
    return current


def _score(text: str, section: str) -> float:
    score = sum(weight for pattern, weight in SCORE_RULES if pattern.search(text))
    score += SECTION_BOOST.get(section, 0.0)
    if re.search(r"\d", text):
        score += 0.5
    if len(text) < 80:
        score -= 1.0
    if len(text) > 320:
        score -= 1.0
    return round(score, 2)


def _has_enough_letters(text: str) -> bool:
    return len(re.findall(r"[A-Za-z\u4e00-\u9fa5]", text)) >= 12


def _page_text(lines: list[dict], chars: list[dict] | None = None) -> tuple[str, list[tuple[int, int]]]:
    """拼出整页文本并记住每行偏移；同时给每个字打上绝对偏移（用于精确矩形）。"""
    parts: list[str] = []
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for index, line in enumerate(lines):
        if index:
            parts.append("\n")
            cursor += 1
        body = line["text"]
        offsets.append((cursor, cursor + len(body)))
        parts.append(body)
        cursor += len(body)
    if chars is not None:
        by_line: dict[int, list[dict]] = {}
        for ch in chars:
            by_line.setdefault(ch["line"], []).append(ch)
        for index, (start, _end) in enumerate(offsets):
            for j, ch in enumerate(by_line.get(index, [])):
                ch["start"] = start + j
                ch["end"] = start + j + 1
    return "".join(parts), offsets


def _rects_from_chars(chars: list[dict], start: int, end: int) -> list[list[float]]:
    """取落在 [start,end) 的字符，按行合并成矩形；同一行多句时只覆盖各自那一段。"""
    rows: dict[int, list[dict]] = {}
    for ch in chars:
        if ch["end"] <= start or ch["start"] >= end:
            continue
        rows.setdefault(ch["line"], []).append(ch)
    rects: list[list[float]] = []
    for line_chars in rows.values():
        x0 = min(c["x0"] for c in line_chars)
        y0 = min(c["y0"] for c in line_chars)
        x1 = max(c["x1"] for c in line_chars)
        y1 = max(c["y1"] for c in line_chars)
        rects.append([round(x0, 5), round(y0, 5), round(x1, 5), round(y1, 5)])
    rects.sort(key=lambda r: (r[1], r[0]))
    return rects


def _collect(pages: list[dict]) -> list[dict]:
    has_headings = any(page["headings"] for page in pages)
    candidates: list[dict] = []
    carry = "other"
    for page in pages:
        text = page["text"]
        lines = page["lines"]
        offsets = page["offsets"]
        index = 0
        for match in SENTENCE_PATTERN.finditer(text):
            start = match.start()
            end = match.end()
            for heading in page["headings"]:
                if heading["start"] <= start < heading["end"]:
                    start = heading["end"]
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            if end <= start:
                continue
            body = text[start:end].replace("\n", " ")
            body = re.sub(r"\s{2,}", " ", body).strip()
            if not 30 <= len(body) <= 420:
                continue
            if not _has_enough_letters(body):
                continue
            section = _section_at(page["headings"], start, carry)
            if has_headings and section == "other":
                continue
            rects = (_rects_from_chars(page["chars"], start, end) if page.get("chars") else [])
            if not rects:
                rects = [
                    [pages_line["x0"], pages_line["y0"], pages_line["x1"], pages_line["y1"]]
                    for pages_line, (ls, le) in zip(lines, offsets)
                    if ls < end and le > start
                ]
            if not rects:
                continue
            candidates.append({
                "page": page["page_no"],
                "index": index,
                "text": body,
                "section": section,
                "score": _score(body, section),
                "rects": rects,
            })
            index += 1
        carry = _section_at(page["headings"], len(text), carry) if page["headings"] else carry
    return candidates


def _pick(candidates: list[dict]) -> list[dict]:
    target = min(40, max(6, round(len(candidates) * 0.12)))
    strong = [c for c in candidates if c["score"] >= 3]
    if len(strong) < 6:
        # 中文文献/短篇常常打不到 3 分：放宽到 2 分，再不够就按分数取前若干
        strong = [c for c in candidates if c["score"] >= 2] or candidates
    picked = sorted(strong, key=lambda c: -c["score"])[:target]
    return sorted(picked, key=lambda c: (c["page"], c["index"]))


def outline(doc_id: str) -> dict:
    """返回 {doc_id, page_count, sections[], sentences[]}；带 mtime 缓存。"""
    path = _doc_file(doc_id)
    mtime = path.stat().st_mtime
    with _CACHE_LOCK:
        cached = _CACHE.get(doc_id)
        if cached and abs(cached[0] - mtime) < 1e-6:
            return cached[1]

    pages: list[dict] = []
    with fitz.open(str(path)) as doc:
        page_count = len(doc)
        for i in range(min(page_count, MAX_PAGES)):
            page = doc.load_page(i)
            lines, chars = _page_lines(page)
            if not lines:
                continue
            text, offsets = _page_text(lines, chars)
            pages.append({
                "page_no": i + 1,
                "text": text,
                "lines": lines,
                "chars": chars,
                "offsets": offsets,
                "headings": _detect_headings(lines, offsets),
            })

    picked = _pick(_collect(pages))
    sentences = [{
        "id": f"p{s['page']}-s{s['index']}",
        "page": s["page"],
        "index": s["index"],
        "section": s["section"],
        "score": s["score"],
        "text": s["text"],
        "rects": s["rects"],
    } for s in picked]

    sections = []
    for key in SECTION_ORDER:
        group = [s for s in sentences if s["section"] == key]
        if not group:
            continue
        page_numbers = [s["page"] for s in group]
        sections.append({
            "key": key,
            "label": SECTION_LABELS[key],
            "page_from": min(page_numbers),
            "page_to": max(page_numbers),
            "count": len(group),
        })

    result = {
        "doc_id": doc_id,
        "page_count": page_count,
        "has_text": bool(pages),
        "sections": sections,
        "sentences": sentences,
    }
    with _CACHE_LOCK:
        _CACHE[doc_id] = (mtime, result)
    return result
