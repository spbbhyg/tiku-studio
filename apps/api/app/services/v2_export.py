"""v2 导出：把组卷篮渲染成可打印 HTML / PDF（服务端可控排版）。"""
from __future__ import annotations

import base64
import re
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models_v2 import BasketItem, QuestionGroup, SubQuestion
from .authoring_export import math_png


def _b64_png(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _b64_svg(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode((svg or "").encode("utf-8")).decode("ascii")


def doc_html(doc, size: float = 12.0) -> str:
    """Tiptap JSON → 打印用 HTML；公式走 PNG 内联，结构式走 SVG 内联。"""
    if not doc:
        return ""

    def marks(text: str, m: list[dict]) -> str:
        out = escape(text)
        for mk in m or []:
            t = mk.get("type")
            if t == "bold":
                out = f"<strong>{out}</strong>"
            elif t == "italic":
                out = f"<em>{out}</em>"
            elif t == "code":
                out = f"<code>{out}</code>"
            elif t == "strike":
                out = f"<s>{out}</s>"
        return out

    def inline(node) -> str:
        t = node.get("type")
        if t == "text":
            return marks(node.get("text", ""), node.get("marks") or [])
        if t == "mathInline":
            latex = ((node.get("attrs") or {}).get("latex") or "").strip()
            got = math_png(latex, size) if latex else None
            if not got:
                return f"<span class='m'>{escape(latex)}</span>"
            data, w, h = got
            return f"<img class='m' style='height:{h * 1.05:.1f}pt;width:{w * 1.05:.1f}pt' src='{_b64_png(data)}' alt='{escape(latex)}'>"
        if t == "chemInline":
            svg = (node.get("attrs") or {}).get("svg") or ""
            return f"<img class='c' style='height:2.1em' src='{_b64_svg(svg)}' alt='结构式'>" if svg else "<span class='m'>[结构式]</span>"
        if t == "hardBreak":
            return "<br>"
        return ""

    def block(node) -> str:
        t = node.get("type")
        kids = node.get("content") or []
        if t == "paragraph":
            return f"<p>{''.join(inline(k) if k.get('type') in ('text', 'mathInline', 'chemInline', 'hardBreak') else block(k) for k in kids)}</p>"
        if t == "heading":
            lvl = (node.get("attrs") or {}).get("level", 3)
            return f"<h{lvl}>{''.join(block(k) for k in kids)}</h{lvl}>"
        if t == "bulletList":
            return f"<ul>{''.join(block(k) for k in kids)}</ul>"
        if t == "orderedList":
            return f"<ol>{''.join(block(k) for k in kids)}</ol>"
        if t == "listItem":
            return f"<li>{''.join(block(k) for k in kids)}</li>"
        if t == "blockquote":
            return f"<blockquote>{''.join(block(k) for k in kids)}</blockquote>"
        if t == "codeBlock":
            return f"<pre>{escape(''.join((k.get('text') or '') for k in kids))}</pre>"
        if t == "horizontalRule":
            return "<hr>"
        if t == "image":
            src = (node.get("attrs") or {}).get("src") or ""
            return f"<img class='img' src='{escape(src)}'>" if src else ""
        return "".join(block(k) for k in kids)

    return "".join(block(n) for n in (doc.get("content") or []))


CSS = """
@page { size: A4; margin: 22mm 32mm 20mm; }
* { box-sizing: border-box; }
/* 参照第41届 Chemy 考试卷：正文 10.5pt 宋体 + Times 数字，版心 146mm，行距 1.45 */
body { font-family: "SimSun", "宋体", "Times New Roman", serif; font-size: 10.5pt; color: #000; line-height: 1.45; }
h1 { font-family: "SimHei", "黑体", "SimSun", serif; font-size: 12.5pt; text-align: center; margin: 0 0 2.4mm; letter-spacing: .3px; }
.subtitle { text-align: center; color: #333; font-size: 9.5pt; margin: -0.6mm 0 2mm; }
.meta { text-align: center; color: #444; font-size: 9pt; margin-bottom: 2mm; }
.fields { display: flex; gap: 8mm; font-size: 9.5pt; margin: 0 0 2.4mm; }
.fields span { flex: 1; border-bottom: 1px solid #333; padding-bottom: 0.8mm; }
hr.sep { border: 0; border-top: 1px solid #999; margin: 2mm 0 3mm; }
/* 大题标题：Arial Bold，与样卷「第 1 题 磷化学（29 分，占 11%）」一致 */
.gtitle { font-family: "Arial", "Microsoft YaHei", sans-serif; font-weight: 700; font-size: 10.5pt; margin: 3.6mm 0 1.2mm; }
.q { margin: 0 0 1.6mm; page-break-inside: avoid; }
.qhead { display: flex; gap: 1.6mm; align-items: baseline; }
.qno { font-family: "Arial", "Microsoft YaHei", sans-serif; font-weight: 700; min-width: 7.5mm; }
.qscore { color: #000; font-size: 9.5pt; }
p { margin: 0 0 0.9mm; }
img.m { vertical-align: -0.35em; }
img.c { vertical-align: middle; }
.answers { margin-top: 5mm; page-break-before: always; }
.answers h2 { font-family: "SimHei", "黑体", sans-serif; font-size: 11.5pt; text-align: center; margin-bottom: 2.4mm; }
.ans { margin: 0 0 1.8mm; font-size: 9.8pt; }
.ans b { color: #000; font-weight: 700; }
ul, ol { margin: 0 0 1.2mm 5mm; padding: 0; }
"""



def _item_rows(session: Session, paper_id: int):
    rows = session.scalars(select(BasketItem).where(BasketItem.paper_id == paper_id)
                           .order_by(BasketItem.seq)).all()
    out = []
    for b in rows:
        sub = session.get(SubQuestion, b.subquestion_id)
        grp = session.get(QuestionGroup, sub.group_id) if sub else None
        out.append((b, sub, grp))
    return out


def _content(b, sub) -> dict:
    """组卷篮快照优先：导出的是「进篮那一刻」的内容，避免题库改动影响已出的卷。"""
    snap = b.snapshot if isinstance(b.snapshot, dict) else {}
    if snap and (snap.get("stem") or snap.get("answer")):
        return {"stem": snap.get("stem"), "answer": snap.get("answer"),
                "rubric": snap.get("rubric"), "score": b.score, "type": snap.get("q_type"),
                "from_snapshot": True}
    if sub is None:
        return {"stem": None, "answer": None, "rubric": None, "score": b.score,
                "type": None, "from_snapshot": False}
    return {"stem": sub.stem, "answer": sub.answer, "rubric": sub.rubric,
            "score": b.score, "type": sub.q_type, "from_snapshot": False}


def _walk_images(doc):
    """递归找出 doc 里所有图片节点（src / material 引用）。"""
    out = []

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "image":
                out.append(n.get("attrs") or {})
            for v in (n.get("content") or []):
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(doc)
    return out


DEFAULT_SPEC = {
    "title": "",
    "subtitle": "",
    "show_scores": True,
    "show_answers": True,
    "section_style": "cn",     # cn=一、二、三  arabic=1. 2. 3.
    "margin_mm": 16,
    "font_pt": 12,
}


def _spec(tpl_spec, opts) -> dict:
    out = dict(DEFAULT_SPEC)
    if isinstance(tpl_spec, dict):
        out.update({k: v for k, v in tpl_spec.items() if v is not None})
    if opts and not isinstance(tpl_spec, dict):
        if "answers" in opts:
            out["show_answers"] = bool(opts["answers"])
        if "scores" in opts:
            out["show_scores"] = bool(opts["scores"])
        if opts.get("title"):
            out["title"] = opts["title"]
    return out


def basket_html(session: Session, paper_id: int, title: str = "试卷", opts: dict | None = None,
                spec: dict | None = None) -> str:
    """把组卷篮渲染成打印 HTML（题目卷 + 答案页）。"""
    opts = opts or {}
    sp = _spec(spec, opts)
    with_answers = sp["show_answers"]
    with_scores = sp["show_scores"]
    title = sp.get("title") or title
    subtitle = sp.get("subtitle") or ""
    body_size = float(sp.get("font_pt") or 12)
    rows = _item_rows(session, paper_id)
    total = sum(float(b.score or 0) for b, _, _ in rows)

    body: list[str] = []
    body.append(f"<h1>{escape(title)}</h1>")
    if subtitle:
        body.append(f"<div class='subtitle'>{escape(subtitle)}</div>")
    if not subtitle:   # 模板给了副标题就不再重复统计行
        body.append(f"<div class='meta'>共 {len(rows)} 小题 · 满分 {total:g} 分</div>")
    body.append("<div class='fields'><span>姓名：</span><span>班级：</span><span>得分：</span></div>")
    body.append("<hr class='sep'>")

    cur_group = None
    cn = "一二三四五六七八九十"
    gno = 0
    for b, sub, grp in rows:
        gname = (grp.title if grp else "") or "未命名题组"
        if gname != cur_group:
            cur_group = gname
            gno += 1
            style = sp.get("section_style") or "cn"
            if style == "arabic":
                head = f"{gno}. "
            else:
                num = cn[gno - 1] if gno <= len(cn) else str(gno)
                head = f"{num}、"
            body.append(f"<div class='gtitle'>{head}{escape(gname)}</div>")
        score = f"<span class='qscore'>（{float(b.score or 0):g} 分）</span>" if with_scores else ""
        c = _content(b, sub)
        stem = doc_html(c["stem"])
        body.append(
            "<div class='q'><div class='qhead'>"
            f"<span class='qno'>{escape(str(b.number or b.seq))}.</span>"
            f"<div>{score}{stem}</div></div></div>"
        )

    if with_answers:
        body.append("<div class='answers'><h2>参考答案与评分细则</h2>")
        for b, sub, _ in rows:
            c = _content(b, sub)
            ans = doc_html(c["answer"]) or "<span class='muted'>（未填写）</span>"
            rub = doc_html(c["rubric"])
            extra = f"<div class='rubric'><i>评分细则：</i>{rub}</div>" if rub else ""
            body.append(
                f"<div class='ans'><b>{escape(str(b.number or b.seq))}.</b> {ans}{extra}</div>"
            )
        body.append("</div>")

    css = CSS
    css = re.sub(r"@page\s*\{[^}]*\}",
                 "@page { size: A4; margin: 18mm %dmm; }" % int(sp.get("margin_mm") or 32), css, count=1)
    css = re.sub(r"(body\s*\{[^}]*?font-size:\s*)[\d.]+pt",
                 lambda m: m.group(1) + ("%g" % body_size) + "pt", css, count=1)
    return f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>{escape(title)}</title><style>{css}</style></head><body>{''.join(body)}</body></html>"


def check_basket(session: Session, paper_id: int) -> dict:
    """导出前检查清单：空题干/无答案/分值为 0/未归组。"""
    rows = _item_rows(session, paper_id)
    issues: list[dict] = []
    if not rows:
        issues.append({"level": "error", "msg": "组卷篮是空的"})
    for b, sub, grp in rows:
        label = str(b.number or b.seq)
        if sub is None:
            issues.append({"level": "error", "msg": f"第 {label} 题的小问已删除"})
            continue
        if not doc_html(sub.stem).strip():
            issues.append({"level": "warn", "msg": f"第 {label} 题没有题干"})
        if not doc_html(sub.answer).strip():
            issues.append({"level": "warn", "msg": f"第 {label} 题没有答案"})
        if float(b.score or 0) <= 0:
            issues.append({"level": "warn", "msg": f"第 {label} 题分值为 0"})

        c = _content(b, sub)
        if c["from_snapshot"] and sub is not None and sub.version != (b.snapshot or {}).get("version"):
            issues.append({"level": "warn",
                           "msg": f"第 {label} 题题库里已更新（v{sub.version}），本次导出仍用进篮时的快照；"
                                  f"如需最新内容，请重新保存组卷篮"})

        doc = c["stem"] if c["stem"] else (sub.stem if sub else None)
        for attrs in _walk_images(doc):
            src = str(attrs.get("src") or "")
            if src.startswith("material:") or not src:
                issues.append({"level": "warn", "msg": f"第 {label} 题有图片缺少地址"})
            elif "/api/images/" in src:
                rel = src.split("/api/images/", 1)[1]
                from urllib.parse import unquote
                target = (LIBRARY_DIR / unquote(rel)).resolve()
                if not str(target).startswith(str(LIBRARY_DIR.resolve())) or not target.is_file():
                    issues.append({"level": "error", "msg": f"第 {label} 题的图片文件不见了：{rel}"})

        for node in (doc or {}).get("content", []) if isinstance(doc, dict) else []:
            pass
    return {"ok": not any(i["level"] == "error" for i in issues),
            "count": len(rows),
            "total_score": sum(float(b.score or 0) for b, _, _ in rows),
            "issues": issues}


def html_to_pdf(html: str) -> bytes:
    """用本机 Chromium（playwright）把打印 HTML 转成 PDF。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover
        raise RuntimeError("未安装 playwright，无法导出 PDF（可先用 HTML 打印）")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="load")
        page.wait_for_timeout(300)
        data = page.pdf(format="A4", print_background=True,
                        margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        browser.close()
    return data
