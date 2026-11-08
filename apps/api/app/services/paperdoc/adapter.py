"""paperdoc/v2 适配层：把库里三种老卷子翻译成 paperdoc/v2。

三种来源（对应 paper_doc.py 的老判断）：

1. 画布卷  snapshot.layout.kind == "canvas"：题干就是整页图，没有文字
2. 扁平快照  stem 字符串 + images[]：题库老题、打印视图里手改过的题
3. 文字层   snapshot.layout.kind == "text"，stem[] 是块数组（p / img / table）
4. exam 形状 title / intro / subs：人工整卷、整卷导入

本模块**只做形状翻译**，不猜版式：老快照里没有几何的图，按 §5 用
min(原始像素宽 @150dpi, 版心宽) 兜底，并把每一次兜底记进 warnings 返回给调用方
（schema 的 meta 是 additionalProperties=False，不能往 doc 里塞私货）。
"""
from __future__ import annotations

import re
from typing import Any

from .schema import (
    CONTENT_W,
    FALLBACK_FIGURE_W,
    SCHEMA_ID,
    _is_residual,
    validate_doc,
)

# 老数据里的位图是 150dpi 抽出来的；只有缺几何时才用它推宽度（§8 唯一允许的兜底）
LEGACY_DPI = 150.0
MIN_FIGURE_W = 24.0

# 支持两级（1-1）和三级（3-2-1）小问编号：真实卷子里三级很常见
SUB_RE = re.compile(r"^\s*(\d+(?:\s*[-－—–]\s*\d+)+)\s*[、.．:：]?\s*")
HEAD_RE = re.compile(r"^\s*第\s*(\d+)\s*题\s*[、.．:：]?\s*")
TERMINAL = "。！？!?；;：:”』）)"

# ---------------------------------------------------------------- 小工具


def _warn(warnings: list[str], msg: str) -> None:
    if msg not in warnings:
        warnings.append(msg)


def _natural_w(path: str) -> float:
    """老图没有几何：按 150dpi 换算成点宽。拿不到就当 0。"""
    try:
        from PIL import Image

        from ...config import LIBRARY_DIR

        f = LIBRARY_DIR / path
        if not f.is_file():
            return 0.0
        with Image.open(f) as im:
            px = float(im.width)
        return px * 72.0 / LEGACY_DPI
    except Exception:                                    # noqa: BLE001
        return 0.0


def _figure(path: str, caption: str = "", width: float | None = None,
            warnings: list[str] | None = None) -> dict | None:
    p = str(path or "").replace("\\", "/").strip()
    if not p:
        return None
    w = float(width or 0)
    if w <= 0:
        nat = _natural_w(p)
        w = min(nat, CONTENT_W) if nat > 0 else FALLBACK_FIGURE_W
        if warnings is not None:
            _warn(warnings, f"图 {p} 没有几何，按 150dpi 兜底成 {round(w, 1)}pt")
    w = max(MIN_FIGURE_W, min(w, CONTENT_W))
    blk: dict[str, Any] = {"t": "figure", "path": p, "w": round(w, 1), "align": "center"}
    if caption:
        blk["caption"] = str(caption).strip()
    return blk


def _cell(text: Any) -> str:
    s = str(text if text is not None else "")
    s = s.replace("\r", "").replace("\n", "")
    return re.sub(r"[ \t\u3000]+", " ", s).strip()


def _table(head: list, rows: list) -> dict | None:
    """结构化表；出现残格就退化成 None，调用方改印小字（不许印「1466.」）。"""
    cols = [_cell(c) for c in (head or [])]
    if not cols or not all(cols):
        return None
    body: list[list[str]] = []
    for raw in rows or []:
        cells = [_cell(c) for c in (raw or [])]
        cells = (cells + [""] * len(cols))[: len(cols)]
        body.append(cells)
    if not body:
        return None
    for line in [cols] + body:
        for c in line:
            if _is_residual(c):
                return None
    return {"t": "table", "head": cols, "rows": body}


def _lines(text: Any) -> list[str]:
    return [ln.strip() for ln in str(text or "").replace("\r", "").split("\n")]


def text_blocks(text: Any, style: str = "body", indent: bool = True,
                allow_sub: bool = True) -> list[dict]:
    """纯文本 → para / sub 序列。

    换行不一定是分段：上一行没写完（不以句末标点结尾）就接回去。带「1-2」这种
    小问编号的行转成 sub 块（编号是权威数据，正文里不留）。
    """
    out: list[dict] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        s = buf.strip()
        buf = ""
        if s:
            blk: dict[str, Any] = {"t": "para", "text": s}
            if style != "body":
                blk["style"] = style
            if not indent:
                blk["indent"] = False
            out.append(blk)

    for line in _lines(text):
        if not line:
            flush()
            continue
        if allow_sub:
            m = SUB_RE.match(line)
            if m:
                flush()
                out.append({"t": "sub", "no": _norm_no(m.group(1)),
                            "text": line[m.end():].strip()})
                continue
        if buf and (buf.endswith(tuple(TERMINAL)) or buf.endswith("</sub>")
                    or buf.endswith("</sup>")):
            flush()
        buf = f"{buf}{line}" if buf else line
    flush()
    return out


def _answer_blocks(text: Any, images: list, sub_nos: list[str],
                   warnings: list[str]) -> list[dict]:
    """答案 → ansblock 序列。

    老数据里答案常是一整块文本，可能带「1-1 …」小问号；能认出来就按小问拆开
    （渲染器据此把答案贴到对应小问下面），认不出来就整块印在题末。
    """
    figs = [f for f in (_figure(_path_of(im), _cap_of(im), _w_of(im), warnings)
                        for im in images) if f]
    groups: list[tuple[str | None, list]] = []
    cur_no: str | None = None
    cur: list = []
    for line in _lines(text):
        if not line:
            continue
        m = SUB_RE.match(line)
        if m and _norm_no(m.group(1)) in sub_nos:
            if cur:
                groups.append((cur_no, cur))
            cur_no = _norm_no(m.group(1))
            cur = []
            rest = line[m.end():].strip()
            if rest:
                cur.extend(text_blocks(rest, style="answer"))
            continue
        cur.extend(text_blocks(line, style="answer"))
    if cur:
        groups.append((cur_no, cur))

    if not groups and figs:
        groups = [(None, [])]
    if not groups:
        return []
    out: list[dict] = []
    for no, blocks in groups:
        blk: dict[str, Any] = {"t": "ansblock", "blocks": blocks}
        if no:
            blk["for"] = no
        out.append(blk)
    if figs:
        out[-1]["blocks"] = list(out[-1]["blocks"]) + figs
    for blk in out:
        if not blk["blocks"]:
            blk["blocks"] = [{"t": "para", "text": "（略）", "style": "answer"}]
    return out


def _path_of(im: Any) -> str:
    if isinstance(im, str):
        return im
    if isinstance(im, dict):
        return str(im.get("path") or im.get("src") or "")
    return ""


def _cap_of(im: Any) -> str:
    return str(im.get("caption") or "") if isinstance(im, dict) else ""


def _w_of(im: Any) -> float:
    if isinstance(im, dict):
        for k in ("w", "width"):
            try:
                if im.get(k):
                    return float(im[k])
            except (TypeError, ValueError):
                pass
    return 0.0


def _images(raw: Any) -> list:
    if not raw:
        return []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    return [x for x in raw if _path_of(x)]


def _meta_of(lay: dict) -> dict:
    m = lay.get("meta")
    return m if isinstance(m, dict) else {}


def _is_doc_layout(snap: dict) -> bool:
    lay = snap.get("layout")
    if isinstance(lay, dict) and str(lay.get("kind") or "") == "text":
        return True
    return isinstance(snap.get("stem"), list)


def _is_exam_shape(snap: dict) -> bool:
    return bool(snap.get("subs")) or "intro" in snap


def _is_canvas(snap: dict) -> bool:
    lay = snap.get("layout")
    return isinstance(lay, dict) and str(lay.get("kind") or "") == "canvas"


# ---------------------------------------------------------------- 三种来源


def _blocks_from_doc(stem: list, warnings: list[str]) -> list[dict]:
    """文字层的 stem[]（p / img / figure / table 块数组）→ v2 blocks。"""
    out: list[dict] = []
    for b in stem or []:
        if not isinstance(b, dict):
            continue
        t = b.get("t")
        if t == "p":
            out.extend(text_blocks(b.get("text")))
        elif t == "figure" and b.get("path"):
            # 导入时已经写过几何（§4）：原样搬，不再猜
            f = _figure(_path_of(b), _cap_of(b), _w_of(b), warnings)
            if f:
                if str(b.get("align") or "") in ("left", "center", "right"):
                    f["align"] = str(b["align"])
                try:
                    if float(b.get("h") or 0) > 0:
                        f["h"] = round(float(b["h"]), 1)
                except (TypeError, ValueError):
                    pass
                out.append(f)
        elif t == "img":
            f = _figure(_path_of(b), _cap_of(b), _w_of(b), warnings)
            if f:
                out.append(f)
        elif t == "table":
            tb = _table(b.get("head") or [], b.get("rows") or [])
            if tb:
                out.append(tb)
            else:
                out.extend(text_blocks("　".join(_cell(c) for c in (b.get("head") or [])),
                                       style="note"))
                for raw in b.get("rows") or []:
                    out.extend(text_blocks("　".join(_cell(c) for c in (raw or [])),
                                           style="note"))
    return out


def _item_from_doc(snap: dict, lay: dict, score: float, number: int,
                   warnings: list[str]) -> dict:
    meta = _meta_of(lay)
    blocks = _blocks_from_doc(lay.get("stem") or [], warnings)
    title = str(meta.get("title") or snap.get("title") or "").strip()
    answers: list[dict] = []
    sub_nos: list[str] = []

    for k, sub in enumerate(lay.get("subs") or [], 1):
        if not isinstance(sub, dict):
            continue
        no = _sub_no(sub, number, k)
        sub_nos.append(no)
        stem = sub.get("text")
        if isinstance(stem, list):
            inner = _blocks_from_doc(stem, warnings)
            if inner and inner[0].get("t") == "para":
                blocks.append({"t": "sub", "no": no,
                               "text": _strip_lead(inner[0].get("text") or "")})
                inner = inner[1:]
            elif inner and inner[0].get("t") == "sub":
                inner = [dict(inner[0], no=no)] + inner[1:]
            else:
                blocks.append({"t": "sub", "no": no, "text": "（见下图）"})
        else:
            raw = str(stem if stem is not None else sub.get("stem") or "")
            blocks.append({"t": "sub", "no": no, "text": _strip_lead(raw)})
            inner = []
        blocks.extend(inner)
        for im in _images(sub.get("images") or sub.get("imgs")):
            f = _figure(_path_of(im), _cap_of(im), _w_of(im), warnings)
            if f:
                blocks.append(f)
        ans = _answer_blocks(sub.get("answer") or "", sub.get("answer_images") or [],
                             [no], warnings)
        for a in ans:
            a["for"] = no
            answers.append(a)

    if not blocks:
        blocks = [{"t": "para", "text": title or "（本题无题干）"}]

    ans_text = str(snap.get("answer") or "").strip()
    if ans_text:
        answers.extend(_answer_blocks(ans_text, [], sub_nos, warnings))
    ans_imgs = [x for x in _images(snap.get("answer_images"))
                if _cap_of(x) != "答案"]
    if ans_imgs:
        answers.extend(_answer_blocks("", ans_imgs, sub_nos, warnings))

    return _item(blocks, answers, title, score, number, snap.get("percent") or meta.get("percent"))


def _item_from_flat(snap: dict, score: float, number: int, warnings: list[str]) -> dict:
    """扁平快照：stem 字符串 + images[]；画布题没有文字。"""
    raw_imgs = _images(snap.get("images"))
    stem_imgs = [x for x in raw_imgs if _cap_of(x) != "答案"]
    ans_imgs = [x for x in raw_imgs if _cap_of(x) == "答案"] + _images(snap.get("answer_images"))
    stem = str(snap.get("stem") or "").strip()

    if _is_canvas(snap) and stem_imgs:
        # 画布卷：题干就是整页图，按版心宽铺满（§5）
        blocks = []
        for im in stem_imgs:
            f = _figure(_path_of(im), "", None, None)
            if f:
                f["w"] = round(CONTENT_W, 1)
                blocks.append(f)
        if not blocks:
            blocks = [{"t": "para", "text": "（本题无内容）"}]
        answers = _answer_blocks(str(snap.get("answer") or ""), ans_imgs, [], warnings)
        return _item(blocks, answers, str(snap.get("title") or ""), score, number,
                     snap.get("percent"))

    # 首行若是短标题（「van Leusen 试剂」这种）就提到大题标题上
    title = str(snap.get("title") or "").strip()
    lines = [ln for ln in _lines(stem) if ln]
    if not title and lines and len(lines[0]) <= 30 and not lines[0].startswith(("（", "(")):
        title = lines[0]
        stem = "\n".join(lines[1:])

    options = snap.get("options") or []
    if options:
        stem = stem + "\n" + "\n".join(f"{chr(65 + i)}．{o}" for i, o in enumerate(options))

    blocks = text_blocks(stem)
    for im in stem_imgs:
        f = _figure(_path_of(im), _cap_of(im), _w_of(im), warnings)
        if f:
            blocks.append(f)
    if not blocks:
        blocks = [{"t": "para", "text": title or "（本题无题干）"}]

    sub_nos = [b["no"] for b in blocks if b.get("t") == "sub"]
    answers = _answer_blocks(str(snap.get("answer") or ""), ans_imgs, sub_nos, warnings)
    return _item(blocks, answers, title, score, number, snap.get("percent"))


def _item_from_exam(snap: dict, score: float, number: int, warnings: list[str]) -> dict:
    """exam 形状：title / intro / subs[]。"""
    blocks: list[dict] = []
    intro = snap.get("intro")
    if intro:
        blocks.extend(text_blocks(intro))
    for im in _images(snap.get("images")):
        f = _figure(_path_of(im), _cap_of(im), _w_of(im), warnings)
        if f:
            blocks.append(f)
    for tb in snap.get("tables") or []:
        if not isinstance(tb, dict):
            continue
        t = _table(tb.get("head") or [], tb.get("rows") or [])
        if t:
            blocks.append(t)
        else:
            warnings.append("exam 快照里的表格是残格，已降级成小字")

    answers: list[dict] = []
    sub_nos: list[str] = []
    for k, sub in enumerate(snap.get("subs") or [], 1):
        if not isinstance(sub, dict):
            continue
        no = _sub_no(sub, number, k)
        sub_nos.append(no)
        blocks.append({"t": "sub", "no": no, "text": _strip_lead(str(sub.get("stem") or ""))})
        for im in _images(sub.get("images")):
            f = _figure(_path_of(im), _cap_of(im), _w_of(im), warnings)
            if f:
                blocks.append(f)
        for a in _answer_blocks(sub.get("answer") or "", sub.get("answer_images") or [],
                                [no], warnings):
            a["for"] = no
            answers.append(a)

    ans_text = str(snap.get("answer") or "").strip()
    if ans_text:
        answers.extend(_answer_blocks(ans_text, _images(snap.get("answer_images")),
                                      sub_nos, warnings))
    if not blocks:
        blocks = [{"t": "para", "text": str(snap.get("title") or "") or "（本题无题干）"}]
    return _item(blocks, answers, str(snap.get("title") or ""), score, number,
                 snap.get("percent"))


def _norm_no(no: str) -> str:
    return re.sub(r"\s*[-－—–]\s*", "-", str(no or "").strip()).strip("-")


def _sub_no(sub: dict, number: int, k: int) -> str:
    no = _norm_no(sub.get("no") or "")
    if re.fullmatch(r"\d+-\d+(?:-\d+)*", no):
        return no
    return f"{number}-{k}"


def _strip_lead(text: str) -> str:
    m = SUB_RE.match(text) or HEAD_RE.match(text)
    return text[m.end():].strip() if m else text.strip()


def _dedupe_subs(blocks: list[dict]) -> list[dict]:
    """同一题里重复出现的小问编号（PDF 抽出的重复行）并回正文，不让校验炸掉。"""
    seen: set[str] = set()
    out: list[dict] = []
    for b in blocks:
        if b.get("t") == "sub":
            no = str(b.get("no") or "")
            if no in seen:
                txt = str(b.get("text") or "").strip()
                if txt:
                    out.append({"t": "para", "text": txt})
                continue
            seen.add(no)
        out.append(b)
    return out


def _item(blocks: list[dict], answers: list[dict], title: str, score: float,
          number: int, percent: Any) -> dict:
    blocks = _dedupe_subs(blocks)
    # 老数据常把「第 9 题 van Leusen 试剂」整句塞进 title；题号由渲染器生成，这里去掉，
    # 否则会印成「第 1 题 第 9 题 van Leusen 试剂」。
    name = HEAD_RE.sub("", str(title or "")).strip()
    item: dict[str, Any] = {
        "number": int(number),
        "title": name,
        "score": float(score or 0),
        "blocks": blocks,
        "answer": {"blocks": answers},
    }
    try:
        p = float(percent) if percent not in (None, "") else 0.0
    except (TypeError, ValueError):
        p = 0.0
    if 0 < p <= 100:
        item["percent"] = round(p, 1)
    return item


# ---------------------------------------------------------------- 卷级


def _norm_footer(text: str) -> str:
    """页脚：把字面量 X / Y 换成真页码占位符，换不出来就用默认。"""
    raw = str(text or "").strip()
    if not raw:
        return "{page}/{pages}"
    s = raw.replace("{page}", "\x00p").replace("{pages}", "\x00s")
    for ch in "XxＸｘ":
        s = s.replace(ch, "\x00p")
    for ch in "YyＹｙ":
        s = s.replace(ch, "\x00s")
    s = s.replace("\x00p", "{page}").replace("\x00s", "{pages}")
    if "{page}" not in s:
        return "{page}/{pages}"
    if "{pages}" not in s:
        s = s + " / {pages}"
    if re.search(r"\{(?!page\}|pages\}|title\}|subject\})", s):
        return "{page}/{pages}"
    return s


def _norm_header(text: str) -> str:
    raw = str(text or "").strip()
    return raw or "{title}"


def doc_from_paper(paper, title: str = "", header: str = "", footer: str = "",
                   subject: str = "", grade: str = "") -> tuple[dict, list[str]]:
    """Paper → (paperdoc/v2 doc, warnings)。"""
    cfg = dict(getattr(paper, "config", None) or {})
    exam_cfg = dict(cfg.get("exam") or {})
    warnings: list[str] = []

    rows = sorted(list(getattr(paper, "items", None) or []), key=lambda x: (x.seq or 0, x.id))
    items: list[dict] = []
    for i, pq in enumerate(rows, 1):
        snap = dict(getattr(pq, "snapshot", None) or {})
        score = float(getattr(pq, "score", 0) or 0)
        number = int(getattr(pq, "seq", 0) or i)
        try:
            if _is_doc_layout(snap):
                lay = snap.get("layout") if isinstance(snap.get("layout"), dict) else snap
                items.append(_item_from_doc(snap, dict(lay), score, number, warnings))
            elif _is_exam_shape(snap) and not _is_canvas(snap):
                items.append(_item_from_exam(snap, score, number, warnings))
            else:
                items.append(_item_from_flat(snap, score, number, warnings))
        except Exception as exc:                          # noqa: BLE001
            _warn(warnings, f"第 {number} 题适配失败：{exc}")
            items.append({
                "number": number, "title": str(snap.get("title") or ""),
                "score": score,
                "blocks": [{"t": "para", "text": "（本题数据无法解析）"}],
                "answer": {"blocks": []},
            })

    if not items:
        items = [{
            "number": 1, "title": "", "score": 0.0,
            "blocks": [{"t": "para", "text": "（本卷还没有题目）"}],
            "answer": {"blocks": []},
        }]
    total = sum(float(it.get("score") or 0) for it in items) or float(
        getattr(paper, "total_score", 0) or 0)
    meta: dict[str, Any] = {
        "title": str(title or "").strip() or str(getattr(paper, "title", "") or "") or "试卷",
        "header": _norm_header(header or exam_cfg.get("header") or ""),
        "footer": _norm_footer(footer or exam_cfg.get("footer") or ""),
        "total_score": round(total, 1),
    }
    if subject:
        meta["subject"] = str(subject)
    if grade:
        meta["grade"] = str(grade)
    dur = getattr(paper, "duration_minutes", None)
    if dur:
        meta["duration"] = float(dur)

    doc = {"schema": SCHEMA_ID, "meta": meta, "items": items}
    front = _front_blocks(exam_cfg)
    if front:
        doc["front"] = front
    return doc, warnings


def _front_blocks(exam_cfg: dict) -> list[dict]:
    """卷首「提示：」之类的整卷前言（config.exam.front，字符串或块数组）。"""
    raw = exam_cfg.get("front")
    if not raw:
        return []
    if isinstance(raw, list):
        return _blocks_from_doc(raw, [])
    return text_blocks(raw, indent=False)


def build_and_check(paper, title: str = "", header: str = "", footer: str = "",
                    subject: str = "", grade: str = "") -> tuple[dict, list[str], list[str]]:
    """适配 + 校验；返回 (doc, 全部 warning)。有 error 时不抛，交给调用方兜底。"""
    doc, warnings = doc_from_paper(paper, title=title, header=header, footer=footer,
                                  subject=subject, grade=grade)
    errors, warns = validate_doc(doc)
    for w in warns:
        _warn(warnings, w)
    return doc, warnings, errors
