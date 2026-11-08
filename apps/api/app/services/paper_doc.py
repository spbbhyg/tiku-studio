"""统一导出：把「任何一张试卷」转成 exam_render 的 doc。

项目里先后出现过来三种卷子，导出路径原本也各写一套，结果同一份卷子在
不同入口导出的版式不一样，还有画布卷导出空白页的问题。这个模块只做一件事：
把三种来源的 snapshot 统一成 exam_render 认识的形状（一题一个 item）。

1. 人工整卷 / 整卷导入：snapshot 已是 exam 形状（title / intro / subs）
2. 单题模式的结构化题：snapshot 是 doc 形状（kind=text，stem[] + subs[]）
3. 题库老题：snapshot 是扁平形状（stem 字符串 + images[]，画布题没有文字）
"""
from __future__ import annotations

import re

from typing import Any

# 正文宽度 = A4(595) - 左右边距(90+80) = 425pt；画布整页图要按页高再压一次
FULL_W = 424.0
FULL_H = 690.0


def _img(im: Any, caption: str = "", width: float | None = None) -> dict:
    if isinstance(im, str):
        return {"path": im, "caption": caption, "width": width}
    if isinstance(im, dict):
        out = {"path": str(im.get("path") or "")}
        cap = im.get("caption")
        out["caption"] = str(cap) if cap else caption
        if width is not None:
            out["width"] = width
        elif im.get("w"):
            out["width"] = im.get("w")
        elif im.get("width"):
            out["width"] = im.get("width")
        return out
    return {"path": "", "caption": caption}


def _imgs(raw) -> list[dict]:
    if not raw:
        return []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    out = [_img(x) for x in raw]
    return [x for x in out if x.get("path")]


def _blocks(blocks) -> tuple[str, list[dict], list[dict]]:
    """doc 的 stem[] → (文字, 图, 表)。"""
    paras: list[str] = []
    imgs: list[dict] = []
    tables: list[dict] = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        t = b.get("t")
        if t == "p":
            txt = str(b.get("text") or "").strip()
            if not txt:
                continue
            numbered = bool(re.match(r"^(第\s*\d+\s*题|\d+\s*[-–]\s*\d+|\d+\s*[.．、]\s|\(?（\d+）)",
                                     re.sub(r"<[^>]+>", "", txt)))
            last_is_head = bool(re.match(r"^(第\s*\d+\s*题|\d+\s*[-–]\s*\d+\s)",
                                         re.sub(r"<[^>]+>", "", paras[-1]))) if paras else False
            if (paras and not numbered and not last_is_head
                    and not re.search(r"[。！？!?；;：:]$", paras[-1])):
                paras[-1] += txt          # 被公式/表格行截断的句子接回去
            else:
                paras.append(txt)
        elif t in ("img", "figure") and b.get("path"):
            imgs.append(_img(b, width=b.get("w")))
        elif t == "table":
            tables.append({"head": b.get("head") or [], "rows": b.get("rows") or [],
                           "caption": b.get("caption") or ""})
    return "\n".join(paras), imgs, tables


def _layout_subs(subs) -> list[dict]:
    out: list[dict] = []
    for s in subs or []:
        if not isinstance(s, dict):
            continue
        raw = s.get("text")
        if isinstance(raw, list):
            stem, imgs, _ = _blocks(raw)
        else:
            stem, imgs = str(raw or s.get("stem") or ""), []
        imgs = imgs or [_img(x) for x in (s.get("imgs") or s.get("images") or [])]
        out.append({
            "stem": stem.strip(),
            "images": [x for x in imgs if x.get("path")],
            "score": s.get("score") or 0,
            "answer": s.get("answer") or "",
            "points": s.get("points") or [],
            "answer_images": _imgs(s.get("answer_images")),
            "subs": _layout_subs(s.get("subs") or []),
        })
    return out


def is_exam_snapshot(snap: dict) -> bool:
    return bool(snap.get("subs")) or "intro" in snap or "title" in snap


def is_doc_layout(snap: dict) -> bool:
    return str(snap.get("kind") or "") == "text" or isinstance(snap.get("stem"), list)


def item_from_doc(lay: dict, score: float, number: int) -> dict:
    """结构化题（单题模式 / 整卷导入）→ exam item。"""
    meta = dict(lay.get("meta") or {})
    intro, imgs, tables = _blocks(lay.get("stem"))
    total = score or meta.get("score") or 0
    return {
        "number": number,
        "score": total,
        "percent": meta.get("percent"),
        "title": str(meta.get("title") or "").strip(),
        "intro": intro,
        "images": imgs,
        "tables": tables,
        "subs": _layout_subs(lay.get("subs") or []),
        "answer": str(lay.get("answer") or "").strip(),
        "answer_images": _imgs(lay.get("answer_images")),
    }


def item_from_flat(snap: dict, score: float, number: int) -> dict:
    """题库老题（扁平快照）→ exam item；画布题只剩一张整页图。"""
    stem = str(snap.get("stem") or "").strip()
    raw_imgs = _imgs(snap.get("images"))
    stem_imgs = [x for x in raw_imgs if (x.get("caption") or "") != "答案"]
    ans_imgs = [x for x in raw_imgs if (x.get("caption") or "") == "答案"]
    ans_imgs += [x for x in _imgs(snap.get("answer_images")) if x.get("path")]

    # 画布题：没有文字，题干就是整页渲染图
    if not stem and stem_imgs:
        for x in stem_imgs:
            x["width"] = FULL_W
            x["caption"] = ""
        return {"number": number, "score": score, "title": "", "intro": "",
                "images": stem_imgs, "tables": [], "subs": [],
                "answer": str(snap.get("answer") or "").strip(),
                "answer_images": [dict(x, width=FULL_W, caption="") for x in ans_imgs]}

    # 首行/唯一一行若是短标题（如「van Leusen 试剂」），提到大题标题上
    title = ""
    nonempty = [ln.strip() for ln in stem.split(chr(10)) if ln.strip()]
    if nonempty and len(nonempty[0]) <= 30 and not nonempty[0].startswith(("（", "(")):
        title = nonempty[0]
        stem = chr(10).join(nonempty[1:])
    if title and not stem and stem_imgs:
        for x in stem_imgs:
            x["caption"] = x.get("caption") or ""
        return {"number": number, "score": score, "title": title, "intro": "",
                "images": stem_imgs, "tables": [], "subs": [],
                "answer": str(snap.get("answer") or "").strip(),
                "answer_images": ans_imgs}
    options = snap.get("options") or []
    if options:
        stem += "\n" + "\n".join(f"{chr(65 + i)}．{o}" for i, o in enumerate(options))
    return {
        "number": number, "score": score, "title": title, "intro": "",
        "percent": snap.get("percent"),
        "images": [], "tables": [],
        "subs": [{"stem": stem, "images": stem_imgs, "score": score,
                  "answer": str(snap.get("answer") or "").strip(), "points": [],
                  "answer_images": ans_imgs, "subs": []}],
        "answer": "", "answer_images": [],
    }


def item_from_snapshot(snap: dict, score: float, number: int) -> dict:
    lay = snap.get("layout")
    if isinstance(lay, dict) and is_doc_layout(lay):
        item = item_from_doc(lay, score, number)
        if snap.get("percent") not in (None, ""):
            item["percent"] = snap["percent"]
        return item
    if is_exam_snapshot(snap) and not is_doc_layout(snap):
        item = dict(snap)
        item["number"] = number
        item["score"] = score or item.get("score") or 0
        return item
    if is_doc_layout(snap):
        return item_from_doc(snap, score, number)
    return item_from_flat(snap, score, number)


def _has_full_page(items: list[dict]) -> bool:
    for it in items:
        for im in (it.get("images") or []):
            if float(im.get("width") or 0) >= FULL_W - 1:
                return True
    return False


def build_doc(paper, title: str = "", header: str = "", footer: str = "",
              subtitle: str = "") -> dict:
    """Paper → exam doc；title/header/footer 是导出时的覆盖值（不写库）。"""
    cfg = dict(getattr(paper, "config", None) or {})
    exam_cfg = dict(cfg.get("exam") or {})
    tpl = dict(cfg.get("template") or {})

    items: list[dict] = []
    rows = sorted(list(paper.items or []), key=lambda x: (x.seq or 0, x.id))
    for i, pq in enumerate(rows, 1):
        items.append(item_from_snapshot(dict(pq.snapshot or {}), float(pq.score or 0), pq.seq or i))

    # 画布整页图要允许比正文更宽：单独把图的尺寸上限抬高
    if _has_full_page(items):
        fig = dict(tpl.get("figure") or {})
        fig["maxWidth"] = FULL_W
        fig["maxHeight"] = FULL_H
        tpl["figure"] = fig

    header = (header or "").strip()
    footer = (footer or "").strip()
    if header:
        tpl["header"] = {"text": header}
    if footer:
        tpl["footer"] = {"format": footer}

    total = sum(float(it.get("score") or 0) for it in items)
    return {
        "id": getattr(paper, "id", None),
        "title": (title or "").strip() or paper.title or "试卷",
        "subtitle": (subtitle or exam_cfg.get("subtitle") or "").strip(),
        "duration": paper.duration_minutes,
        "total_score": total or paper.total_score,
        "template": tpl,
        "items": items,
        "status": exam_cfg.get("status") or "done",
        "created_at": str(getattr(paper, "created_at", "") or ""),
    }


def build_doc_from_question(q, score: float = 10.0) -> dict:
    """单题导出：三种题都能出（画布题出整页图）。"""
    lay = dict(getattr(q, "layout", None) or {})
    if str(lay.get("kind") or "") == "text":
        item = item_from_doc(lay, score, 1)
        title = item.get("title") or getattr(q, "source_title", "") or "试题"
    else:
        snap = {"stem": getattr(q, "stem", "") or "", "images": getattr(q, "images", None) or [],
                "answer": getattr(q, "answer", "") or "", "options": getattr(q, "options", None) or []}
        item = item_from_flat(snap, score, 1)
        title = getattr(q, "source_title", "") or "试题"
    tpl: dict = {}
    if _has_full_page([item]):
        tpl["figure"] = {"maxWidth": FULL_W, "maxHeight": FULL_H}
    return {"title": title, "subtitle": "", "template": tpl,
            "items": [item], "status": "done"}
