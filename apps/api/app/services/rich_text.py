"""题库富文本（stem / 小问 / 答案的 HTML）→ 统一结构 → 渲染成网页 HTML 或 Word 段落。

为什么需要它：题库里的题干是这样的：一个 <p> 里用 <br> 保留原 PDF 的换行，还有
<sub>/<sup>/<strong>。以前本项目直接把这些 HTML 去标签、按行拆开，于是「一句话被
断成三行」「下标丢了」「加粗没了」，打印出来很乱。这里保留行内样式，并把 <br> 当
软换行处理：前后都是中文就直接接上，否则补一个空格，读起来就是正常段落。
"""

from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser

# 允许出现在题干里的标签（其余标签只保留文字）
KEEP = {"p", "br", "sub", "sup", "b", "strong", "i", "em", "u", "span",
        "table", "thead", "tbody", "tr", "td", "th", "ul", "ol", "li", "img"}

DROP_TAGS = {"script", "style", "iframe", "object", "embed", "link", "meta"}

_CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")
_WS = re.compile(r"[ \t\u00a0]+")


def _squash(text: str) -> str:
    return _WS.sub(" ", text.replace("\r", " ").replace("\n", " "))


class _Blocks(HTMLParser):
    """把 HTML 变成 [{kind:'p', runs:[...]}, {kind:'table', rows:[[str]]}]。"""

    def __init__(self, img_prefix: str = ""):
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self.row: list[str] | None = None
        self.table: list[list[str]] | None = None
        self.cell: list[str] | None = None
        self.style = {"sub": 0, "sup": 0, "b": 0, "i": 0}
        self.pending_br = False
        self.skip = 0
        self.img_prefix = img_prefix.rstrip("/")
        self._new_p()

    # ---------------------------------------------------------------- 基础
    def _new_p(self):
        self.blocks.append({"kind": "p", "runs": []})
        self.pending_br = False

    def _cur(self) -> dict:
        return self.blocks[-1]

    def _runs(self) -> list:
        return self._cur()["runs"]

    def _last_text(self) -> str:
        for r in reversed(self._runs()):
            if r["t"]:
                return r["t"]
        return ""

    def _add(self, text: str):
        if self.skip:
            return
        if self.cell is not None:                     # 表格里：简单拼成一行文字
            self.cell.append(text)
            return
        if self.table is not None:
            return
        text = _squash(text)
        if not text.strip() and not text:
            return
        runs = self._runs()
        if self.pending_br:
            prev = self._last_text()
            nxt = text.lstrip()
            joiner = ""
            if prev and nxt:
                a, b = prev[-1], nxt[0]
                if not (_CJK.match(a) or _CJK.match(b)) and a not in "，。；：、）》”’" \
                        and b not in "，。；：、）（》”’":
                    joiner = " "
            if runs and prev:
                # 软换行：不折行，按上面规则接上
                if joiner:
                    self._push(joiner)
            text = text.lstrip() if (runs and joiner == "") else text
            self.pending_br = False
        if not runs and text.startswith(" "):
            text = text[1:]
        self._push(text)

    def _push(self, text: str):
        if not text:
            return
        st = {"t": text, "sub": bool(self.style["sub"]), "sup": bool(self.style["sup"]),
              "b": bool(self.style["b"]), "i": bool(self.style["i"])}
        runs = self._runs()
        if runs:
            last = runs[-1]
            if (last["sub"], last["sup"], last["b"], last["i"]) == (st["sub"], st["sup"], st["b"], st["i"]):
                last["t"] += text
                return
        runs.append(st)

    # ---------------------------------------------------------------- 解析
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_TAGS:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "br":
            self.pending_br = True
            return
        if tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"):
            if self._runs():
                self._new_p()
            return
        if tag == "li":
            if self._runs():
                self._new_p()
            self._push("· ")
            return
        if tag == "table":
            self.table = []
            return
        if tag == "tr":
            self.row = []
            return
        if tag in ("td", "th"):
            self.cell = []
            return
        if tag == "img":
            alt = dict(attrs).get("alt") or ""
            src = dict(attrs).get("src") or ""
            if not self.table:
                if src and self.img_prefix and src.startswith("/"):
                    self._push(f"[图]")       # 图单独走 figures 通道，这里只留位置提示
                elif alt:
                    self._push(f"[{alt}]")
            return
        if tag in ("sub",):
            self.style["sub"] += 1
        elif tag in ("sup",):
            self.style["sup"] += 1
        elif tag in ("b", "strong"):
            self.style["b"] += 1
        elif tag in ("i", "em"):
            self.style["i"] += 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in DROP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "table":
            if self.table:
                self.blocks.append({"kind": "table", "rows": self.table})
            self.table = None
            self._new_p()
            return
        if tag == "tr":
            if self.row is not None and self.table is not None:
                self.table.append(self.row)
            self.row = None
            return
        if tag in ("td", "th"):
            if self.cell is not None and self.row is not None:
                self.row.append(_squash("".join(self.cell)).strip())
            self.cell = None
            return
        if tag == "sub":
            self.style["sub"] = max(0, self.style["sub"] - 1)
        elif tag == "sup":
            self.style["sup"] = max(0, self.style["sup"] - 1)
        elif tag in ("b", "strong"):
            self.style["b"] = max(0, self.style["b"] - 1)
        elif tag in ("i", "em"):
            self.style["i"] = max(0, self.style["i"] - 1)
        elif tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"):
            if self._runs():
                self._new_p()

    def handle_data(self, data):
        if self.skip:
            return
        self._add(data)


def to_blocks(raw: str, img_prefix: str = "") -> list[dict]:
    p = _Blocks(img_prefix=img_prefix)
    try:
        p.feed(raw or "")
        p.close()
    except Exception:
        return [{"kind": "p", "runs": [{"t": re.sub(r"<[^>]+>", "", raw or ""),
                                        "sub": False, "sup": False, "b": False, "i": False}]}]
    out = []
    for b in p.blocks:
        if b["kind"] == "table":
            if b["rows"]:
                out.append(b)
            continue
        runs = [r for r in b["runs"] if r["t"].strip() or r["t"] == " "]
        if runs:
            out.append({"kind": "p", "runs": runs})
    return out


def blocks_text(blocks: list[dict], sep: str = "\n") -> str:
    """纯文本版本（喂给老接口、或做摘要用）。"""
    out = []
    for b in blocks:
        if b["kind"] == "table":
            out.extend("　".join(r) for r in b["rows"])
        else:
            out.append("".join(r["t"] for r in b["runs"]).strip())
    return sep.join(x for x in out if x)


# ---------------------------------------------------------------- HTML 渲染
def esc(t: str) -> str:
    return _html.escape(t, quote=False)


def blocks_html(blocks: list[dict], cls: str = "body") -> str:
    """渲染成试卷用的 HTML；$...$ 原样留着交给 KaTeX。"""
    out = []
    for b in blocks:
        if b["kind"] == "table":
            rows = b["rows"]
            if not rows:
                continue
            head, body = rows[0], rows[1:]
            t = ['<table class="rt"><thead><tr>']
            t += [f"<th>{esc(c)}</th>" for c in head]
            t.append("</tr></thead><tbody>")
            for r in body:
                t.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>")
            t.append("</tbody></table>")
            out.append("".join(t))
            continue
        bits = []
        for r in b["runs"]:
            s = esc(r["t"])
            if r["b"]:
                s = f"<strong>{s}</strong>"
            if r["i"]:
                s = f"<em>{s}</em>"
            if r["sub"]:
                s = f"<sub>{s}</sub>"
            if r["sup"]:
                s = f"<sup>{s}</sup>"
            bits.append(s)
        txt = "".join(bits)
        if txt.strip():
            out.append(f'<p class="{cls}">{txt}</p>')
    return "".join(out)


def blocks_tables_html(blocks: list[dict]) -> str:
    """只渲染表格块（小问里夹表格时用）。"""
    return "".join(blocks_html([b], cls="body") for b in blocks if b["kind"] == "table")


def blocks_inline_html(blocks: list[dict]) -> str:
    """只要行内片段（不带 <p> 外壳），用于把题干塞进已有段落里。"""
    out = []
    for b in blocks:
        if b["kind"] != "p":
            continue
        bits = []
        for r in b["runs"]:
            t = esc(r["t"])
            if r["b"]:
                t = f"<strong>{t}</strong>"
            if r["i"]:
                t = f"<em>{t}</em>"
            if r["sub"]:
                t = f"<sub>{t}</sub>"
            if r["sup"]:
                t = f"<sup>{t}</sup>"
            bits.append(t)
        out.append("".join(bits))
    return "".join(out)


# ---------------------------------------------------------------- Word 渲染
def _ea(run, name: str = "宋体"):
    from docx.oxml.ns import qn
    run.font.name = name
    try:
        rpr = run._element.get_or_add_rPr()
        rpr.get_or_add_rFonts().set(qn("w:eastAsia"), name)
    except Exception:  # noqa: BLE001 — 字体名设不上不影响内容
        pass


def docx_add(container, blocks: list[dict], size_pt: float, *, indent_cm: float | None = None,
             line_spacing: float | None = None, bold: bool = False, font: str = "宋体"):
    """把 blocks 写进 python-docx 文档（container 是 Document 或 Cell）。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    for b in blocks:
        if b["kind"] == "table":
            rows = b["rows"]
            if not rows:
                continue
            ncol = max(len(r) for r in rows)
            tab = container.add_table(rows=len(rows), cols=ncol)
            tab.style = "Table Grid"
            for i, r in enumerate(rows):
                for j in range(ncol):
                    cell = tab.cell(i, j)
                    cell.text = ""
                    par = cell.paragraphs[0]
                    run = par.add_run(r[j] if j < len(r) else "")
                    run.font.size = Pt(max(8.0, size_pt - 1.0))
                    _ea(run, font)
            continue
        par = container.add_paragraph()
        par.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        pf = par.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(2)
        if line_spacing:
            pf.line_spacing = line_spacing
        if indent_cm:
            pf.left_indent = Cm(indent_cm)
        for r in b["runs"]:
            run = par.add_run(r["t"])
            run.font.size = Pt(size_pt)
            _ea(run, font)
            run.bold = bool(r["b"]) or bold
            run.italic = bool(r["i"])
            if r["sub"]:
                run.font.subscript = True
            if r["sup"]:
                run.font.superscript = True
