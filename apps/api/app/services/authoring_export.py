"""文献题导出：Word(.docx) / PDF / Markdown。

设计要点
- 题干、答案、解析里都可以混写 ``$LaTeX$``：用 matplotlib 的 mathtext 把公式
  渲染成透明 PNG（无需安装 LaTeX），Word 里按行内小图插入，PDF 里按基线贴图，
  Markdown 保留 ``$...$`` 原文。
- 题干图 / 答案图是裁剪出的 PNG，按正文宽度等比缩放，超出则自动翻页。
- 答案位置三档：inline（紧跟题目）、appendix（卷末「参考答案与解析」）、none。
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime
from pathlib import Path

from ..config import DATA_DIR, LIBRARY_DIR

MATH_RE = re.compile(r"\$([^$\n]+)\$")
MATH_CACHE = DATA_DIR / "cache" / "math"

DIFF_STAR = {1: "★", 2: "★★", 3: "★★★", 4: "★★★★"}

GREY = (0.42, 0.45, 0.5)
DARK = (0.09, 0.11, 0.13)


# ------------------------------------------------------------------ 公式
_ENV_RE = re.compile(r"\\(begin|end)\{[^}]*\}")
_FRAC_RE = re.compile(r"\\frac\{([^{}]*)\}\{([^{}]*)\}")
_FONT_CMD_RE = re.compile(r"\\(mathbf|mathrm|mathit|mathcal|mathsf|text|textrm|operatorname)\{([^{}]*)\}")

_SYM = {
    "\\alpha": "α", "\\beta": "β", "\\gamma": "γ", "\\delta": "δ", "\\epsilon": "ε",
    "\\zeta": "ζ", "\\eta": "η", "\\theta": "θ", "\\lambda": "λ", "\\mu": "μ",
    "\\nu": "ν", "\\xi": "ξ", "\\pi": "π", "\\rho": "ρ", "\\sigma": "σ", "\\tau": "τ",
    "\\phi": "φ", "\\chi": "χ", "\\psi": "ψ", "\\omega": "ω",
    "\\Delta": "Δ", "\\Gamma": "Γ", "\\Theta": "Θ", "\\Lambda": "Λ", "\\Xi": "Ξ",
    "\\Pi": "Π", "\\Sigma": "Σ", "\\Phi": "Φ", "\\Psi": "Ψ", "\\Omega": "Ω",
    "\\pm": "±", "\\mp": "∓", "\\times": "×", "\\div": "÷", "\\cdot": "·",
    "\\leq": "≤", "\\geq": "≥", "\\neq": "≠", "\\approx": "≈", "\\infty": "∞",
    "\\partial": "∂", "\\nabla": "∇", "\\sum": "∑", "\\prod": "∏", "\\int": "∫",
    "\\rightarrow": "→", "\\leftarrow": "←", "\\Rightarrow": "⇒", "\\in": "∈",
    "\\sqrt": "√", "\\det": "det", "\\max": "max", "\\min": "min", "\\log": "log",
    "\\ln": "ln", "\\exp": "exp", "\\sin": "sin", "\\cos": "cos", "\\tan": "tan",
}


def _sanitize(latex: str) -> str:
    """第一层降级：去掉 mathtext 不认的环境/命令，保留结构。"""
    s = _ENV_RE.sub(" ", latex)
    s = s.replace("\\dfrac", "\\frac").replace("\\tfrac", "\\frac")
    s = re.sub(r"\\left|\\right", "", s)
    s = re.sub(r"\\[!,;:]", " ", s)
    s = s.replace("&", ",\\ ").replace("\\\\", ";\\ ")
    for bad in ("\\displaystyle", "\\mbox", "\\tag", "\\quad", "\\qquad", "\\!"):
        s = s.replace(bad, " ")
    return _FONT_CMD_RE.sub(r"\2", s).strip()


def _sanitize_deep(latex: str) -> str:
    """第二层降级：分式改写成 (a)/(b)、符号换成 Unicode，任何字体都能显示。"""
    s = _sanitize(latex)
    for _ in range(3):  # 处理嵌套分式
        s2 = _FRAC_RE.sub(r"(\1)/(\2)", s)
        if s2 == s:
            break
        s = s2
    s = s.replace("\\{", "{").replace("\\}", "}").replace("\\%", "%")
    for k in sorted(_SYM, key=len, reverse=True):
        s = s.replace(k, _SYM[k])
    return re.sub(r"\s{2,}", " ", s).strip()


def math_png(latex: str, fontsize: float = 13.0) -> tuple[bytes, float, float] | None:
    """渲染一段 LaTeX → (png, 宽高比)。失败返回 None（调用方退回纯文本）。"""
    key = hashlib.sha1(f"{fontsize}|{latex}".encode("utf-8")).hexdigest()[:20]
    cached = MATH_CACHE / f"{key}.png"
    try:
        from PIL import Image

        if cached.exists():
            with Image.open(cached) as im:
                return (cached.read_bytes(), im.width / max(1, im.height),
                        im.height * 72.0 / 300.0)

        import matplotlib

        matplotlib.use("Agg")
        from matplotlib.figure import Figure

        candidates = [f"${latex}$", f"${_sanitize(latex)}$",
                      f"${_sanitize_deep(latex)}$", _sanitize_deep(latex)]
        png = b""
        for expr in candidates:
            try:
                fig = Figure(figsize=(0.01, 0.01), dpi=300)
                fig.patch.set_alpha(0)
                fig.text(0, 0, expr, fontsize=fontsize, color="#111111")
                buf = io.BytesIO()
                fig.savefig(buf, format="png", dpi=300, transparent=True,
                            bbox_inches="tight", pad_inches=0.02)
                png = buf.getvalue()
                break
            except Exception:
                continue
        if not png:
            return None
        with Image.open(io.BytesIO(png)) as im:
            ratio = im.width / max(1, im.height)
            # 300 dpi 下的自然高度（pt），保证公式字形与实际字号一致
            h_pt = im.height * 72.0 / 300.0
        MATH_CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(png)
        return png, ratio, h_pt
    except Exception:
        return None


def rich_segments(text: str) -> list[tuple[str, str]]:
    """把文本切成 [('text', '...'), ('math', 'latex'), ...]。"""
    text = text or ""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in MATH_RE.finditer(text):
        if m.start() > pos:
            out.append(("text", text[pos:m.start()]))
        out.append(("math", m.group(1).strip()))
        pos = m.end()
    if pos < len(text):
        out.append(("text", text[pos:]))
    return out or [("text", "")]


def plain(text: str) -> str:
    return (text or "").replace("$", "")


def image_path(path: str | None) -> Path | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = LIBRARY_DIR / path
    return p if p.exists() else None


def image_ratio(path: Path) -> float:
    try:
        from PIL import Image as PILImage

        with PILImage.open(path) as im:
            return im.width / max(1, im.height)
    except Exception:
        return 1.6


def question_head(idx: int, q: dict) -> str:
    head = f"{idx}. {q.get('q_type') or '简答'}"
    diff = q.get("difficulty") or 2
    return f"{head} {DIFF_STAR.get(diff, '★★')}"


def options_text(q: dict) -> str:
    opts = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
    if not opts:
        return ""
    letters = "ABCDEFGH"
    return "    ".join(f"{letters[i]}. {o}" for i, o in enumerate(opts[:len(letters)]))


def source_line(q: dict) -> str:
    bits = []
    if q.get("source_title"):
        bits.append(str(q["source_title"]))
    if q.get("source_citation"):
        bits.append(str(q["source_citation"]))
    if q.get("locator"):
        bits.append(f"位置：{q['locator']}")
    return "出处：" + " · ".join(bits) if bits else ""


def _opts(opts: dict) -> dict:
    return {
        "title": opts.get("title") or "文献命题",
        "subtitle": opts.get("subtitle") or "",
        "answers": opts.get("answers") or "appendix",   # inline | appendix | none
        "analysis": bool(opts.get("analysis", True)),
        "tags": bool(opts.get("tags", True)),
        "source": bool(opts.get("source", True)),
    }


# ------------------------------------------------------------------ Word
def build_docx(questions: list[dict], opts: dict | None = None) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    def _ea(el, name: str):
        """确保 rPr/rFonts 存在后设置东亚字体（python-docx 里 rPr 可能为 None）。"""
        rpr = el.get_or_add_rPr()
        rf = rpr.get_or_add_rFonts()
        rf.set(qn("w:eastAsia"), name)
        rf.set(qn("w:ascii"), name)
        rf.set(qn("w:hAnsi"), name)
    o = _opts(opts or {})
    doc = Document()

    normal = doc.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(10.5)
    _ea(normal.element, "宋体")

    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin = sec.bottom_margin = Cm(2.0)
    body_w = Cm(21.0 - 4.4)


    def para(before: float = 0, after: float = 4, align=None, line: float = 1.45):
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.space_before = Pt(before)
        pf.space_after = Pt(after)
        pf.line_spacing = line
        if align is not None:
            p.alignment = align
        return p

    def rich(p, text: str, size: float = 10.5, bold: bool = False,
             color: tuple[float, float, float] | None = None):
        for kind, val in rich_segments(text):
            if kind == "text":
                if not val:
                    continue
                r = p.add_run(val)
                r.font.size = Pt(size)
                r.bold = bold
                if color:
                    r.font.color.rgb = RGBColor.from_string(
                        "%02X%02X%02X" % tuple(int(c * 255) for c in color))
                _ea(r._element, "宋体")
            else:
                rendered = math_png(val, fontsize=size)
                if rendered:
                    png, _ratio, h_pt = rendered
                    r = p.add_run()
                    r.add_picture(io.BytesIO(png), height=Pt(h_pt))
                else:
                    r = p.add_run(f"${val}$")
                    r.font.size = Pt(size)

    def picture(p, path: Path, max_w):
        ratio = image_ratio(path)
        w = max_w
        h = int(w / ratio)
        p.add_run().add_picture(str(path), width=w)

    # 标题
    tp = para(before=0, after=2, align=WD_ALIGN_PARAGRAPH.CENTER)
    tr = tp.add_run(o["title"])
    tr.bold = True
    tr.font.size = Pt(18)
    tr.font.name = "微软雅黑"
    _ea(tr._element, "微软雅黑")

    meta_bits = [datetime.now().strftime("%Y-%m-%d"), f"共 {len(questions)} 题"]
    if o["subtitle"]:
        meta_bits.insert(0, o["subtitle"])
    mp = para(before=0, after=10, align=WD_ALIGN_PARAGRAPH.CENTER)
    mr = mp.add_run(" · ".join(meta_bits))
    mr.font.size = Pt(9)
    mr.font.color.rgb = RGBColor(0x77, 0x77, 0x77)

    def answer_block(q: dict, idx: int, compact: bool):
        ans = (q.get("answer") or "").strip()
        ana = (q.get("analysis") or "").strip() if o["analysis"] else ""
        ax = image_path(q.get("answer_image"))
        if not (ans or ana or ax):
            return
        if compact:
            p = para(before=2, after=2)
            r = p.add_run(f"答案（{idx}）：")
            r.bold = True
            r.font.size = Pt(10)
            rich(p, ans or "（见下图）", size=10)
            if ax:
                p2 = para(before=0, after=4)
                picture(p2, ax, body_w * 0.62)
            if ana:
                p3 = para(before=0, after=8)
                r3 = p3.add_run("解析：")
                r3.bold = True
                r3.font.size = Pt(10)
                rich(p3, ana, size=10)
        else:
            p = para(before=2, after=3)
            r = p.add_run(f"{idx}. 答案：")
            r.bold = True
            r.font.size = Pt(10.5)
            rich(p, ans or "（见下图）")
            if ax:
                p2 = para(before=0, after=4)
                picture(p2, ax, body_w * 0.55)
            if ana:
                p3 = para(before=0, after=10)
                r3 = p3.add_run("解析：")
                r3.bold = True
                p3.add_run("").font.size = Pt(10)
                rich(p3, ana, size=10, color=GREY)

    # 题目
    for i, q in enumerate(questions, 1):
        hp = para(before=8 if i > 1 else 2, after=4)
        hr = hp.add_run(question_head(i, q))
        hr.bold = True
        hr.font.size = Pt(11.5)
        hr.font.name = "微软雅黑"
        _ea(hr._element, "微软雅黑")

        if (q.get("stem") or "").strip():
            sp = para(before=0, after=4)
            rich(sp, q["stem"])

        sx = image_path(q.get("stem_image"))
        if sx:
            ip = para(before=2, after=6, align=WD_ALIGN_PARAGRAPH.CENTER)
            picture(ip, sx, body_w * 0.92)

        opt = options_text(q)
        if opt:
            op = para(before=2, after=4)
            rich(op, opt)

        if o["answers"] == "inline":
            answer_block(q, i, compact=True)

        tail = []
        if o["tags"] and q.get("tags"):
            tail.append("标签：" + "、".join(q["tags"]))
        if o["source"] and source_line(q):
            tail.append(source_line(q))
        if tail:
            tp2 = para(before=2, after=6)
            r2 = tp2.add_run("    ".join(tail))
            r2.font.size = Pt(8.5)
            r2.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    if o["answers"] == "appendix" and questions:
        doc.add_page_break()
        ap = para(before=0, after=10, align=WD_ALIGN_PARAGRAPH.CENTER)
        ar = ap.add_run("参考答案与解析")
        ar.bold = True
        ar.font.size = Pt(15)
        ar.font.name = "微软雅黑"
        _ea(ar._element, "微软雅黑")
        for i, q in enumerate(questions, 1):
            answer_block(q, i, compact=False)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ PDF
class _Pdf:
    """极简排版器：按行流式排，遇到底边自动翻页。"""

    W, H = 595.0, 842.0
    M = 58.0

    def __init__(self):
        import pymupdf

        self.mu = pymupdf
        self.doc = pymupdf.open()
        self.font_path = Path("C:/Windows/Fonts/Deng.ttf")
        self.bold_path = Path("C:/Windows/Fonts/Dengb.ttf")
        self.page = None
        self.y = 0.0
        self._new_page()

    # -- 基础
    def _new_page(self):
        self.page = self.doc.new_page(width=self.W, height=self.H)
        self.page.insert_font(fontname="cn", fontfile=str(self.font_path))
        self.page.insert_font(fontname="cnb", fontfile=str(self.bold_path))
        self.y = self.M + 6

    def _font(self, bold: bool):
        return self.mu.Font(fontfile=str(self.bold_path if bold else self.font_path))

    def need(self, h: float):
        if self.y + h > self.H - self.M:
            self._new_page()

    def width(self, text: str, size: float, bold: bool = False) -> float:
        return self._font(bold).text_length(text, size, )

    def image(self, path: Path, max_w: float, max_h: float = 300.0):
        ratio = image_ratio(path)
        w = min(max_w, self.W - 2 * self.M)
        h = w / ratio
        if h > max_h:
            h = max_h
            w = h * ratio
        self.need(h + 6)
        rect = self.mu.Rect(self.M + (self.W - 2 * self.M - w) / 2, self.y, 0, 0)
        rect = self.mu.Rect(rect.x0, self.y, rect.x0 + w, self.y + h)
        self.page.insert_image(rect, filename=str(path))
        self.y += h + 6

    # -- 富文本一行行排
    def rich(self, text: str, size: float = 10.5, bold: bool = False,
             color=DARK, indent: float = 0.0, gap: float = 3.0):
        avail = self.W - 2 * self.M - indent
        # 先把 token 量化成 (宽, 类型, 内容)
        toks: list[tuple[float, str, object]] = []
        for kind, val in rich_segments(text):
            if kind == "text":
                # 按字符切，便于在中文里换行
                buf = ""
                for ch in val:
                    cand = buf + ch
                    if self.width(cand, size, bold) > avail:
                        if buf:
                            toks.append((self.width(buf, size, bold), "t", buf))
                        buf = ch
                    else:
                        buf = cand
                if buf:
                    toks.append((self.width(buf, size, bold), "t", buf))
            else:
                r = math_png(val, fontsize=size)
                if r:
                    png, ratio, h = r
                    toks.append((h * ratio, "m", (png, h)))
                else:
                    s = f" {val} "
                    toks.append((self.width(s, size, bold), "t", s))

        # 单个 token 比整行还宽时按字符切开，避免溢出右边距
        flat: list[tuple[float, str, object]] = []
        for w, k, v in toks:
            if w <= avail:
                flat.append((w, k, v))
                continue
            if k == "m":
                png, h = v  # type: ignore[misc]
                ratio = w / h
                flat.append((avail, "m", (png, avail / ratio)))
            else:
                text = str(v)
                piece, pw = "", 0.0
                for ch in text:
                    cw = self.width(ch, size, bold)
                    if pw + cw > avail and piece:
                        flat.append((pw, "t", piece))
                        piece, pw = ch, cw
                    else:
                        piece += ch
                        pw += cw
                if piece:
                    flat.append((pw, "t", piece))

        # 组装成行
        lines: list[list[tuple[float, str, object]]] = []
        cur: list[tuple[float, str, object]] = []
        cur_w = 0.0
        for w, k, v in flat:
            if cur_w + w > avail and cur:
                lines.append(cur)
                cur, cur_w = [], 0.0
            cur.append((w, k, v))
            cur_w += w
        if cur:
            lines.append(cur)

        for line in lines:
            line_h = size * 1.45
            for _w, k, v in line:
                if k == "m":
                    line_h = max(line_h, v[1] + size * 0.30)  # type: ignore[index]
            self.need(line_h + gap)
            x = self.M + indent
            base = self.y + line_h - size * 0.28   # 文本基线（留出公式高度）
            for w, k, v in line:
                if k == "t":
                    self.page.insert_text((x, base), v, fontname="cnb" if bold else "cn",
                                          fontsize=size, color=color)
                else:
                    png, h = v  # type: ignore[misc]
                    top = base - h + size * 0.22     # 公式底部略低于基线
                    rect = self.mu.Rect(x, top, x + w, top + h)
                    self.page.insert_image(rect, stream=png)
                x += w
            self.y += line_h
        self.y += gap
        return self.y

    def line(self, text: str, size: float = 10.5, bold: bool = False,
             color=DARK, indent: float = 0.0, gap: float = 3.0, align: str = "left"):
        self.need(size * 1.6 + gap)
        w = self.width(text, size, bold)
        x = self.M + indent
        if align == "center":
            x = (self.W - w) / 2
        self.page.insert_text((x, self.y + size), text, fontname="cnb" if bold else "cn",
                              fontsize=size, color=color)
        self.y += size * 1.45 + gap
        return self.y

    def hr(self, gap: float = 8.0):
        self.need(4 + gap)
        self.page.draw_line((self.M, self.y), (self.W - self.M, self.y),
                            color=(0.8, 0.82, 0.85), width=0.8)
        self.y += gap

    def page_break(self):
        self._new_page()

    def bytes(self) -> bytes:
        buf = io.BytesIO()
        self.doc.subset_fonts()
        self.doc.save(buf, garbage=3, deflate=True)
        return buf.getvalue()


def build_pdf(questions: list[dict], opts: dict | None = None) -> bytes:
    o = _opts(opts or {})
    f = _Pdf()

    f.line(o["title"], size=19, bold=True, align="center", gap=4)
    meta_bits = [datetime.now().strftime("%Y-%m-%d"), f"共 {len(questions)} 题"]
    if o["subtitle"]:
        meta_bits.insert(0, o["subtitle"])
    f.line(" · ".join(meta_bits), size=9, color=GREY, align="center", gap=6)
    f.hr(12)

    for i, q in enumerate(questions, 1):
        f.need(48)
        f.line(question_head(i, q), size=11.5, bold=True, gap=2)
        if (q.get("stem") or "").strip():
            f.rich(q["stem"], size=10.5, gap=3)
        sx = image_path(q.get("stem_image"))
        if sx:
            f.image(sx, max_w=(f.W - 2 * f.M) * 0.86, max_h=f.H * 0.40)
        opt = options_text(q)
        if opt:
            f.rich(opt, size=10.5, indent=12, gap=3)
        if o["answers"] == "inline":
            ans = (q.get("answer") or "").strip()
            ana = (q.get("analysis") or "").strip() if o["analysis"] else ""
            ax = image_path(q.get("answer_image"))
            if ans or ana or ax:
                f.rich(f"答案（{i}）：{ans if ans else '（见下图）'}", size=10,
                       color=(0.16, 0.36, 0.36), indent=12, gap=2)
                if ax:
                    f.image(ax, max_w=(f.W - 2 * f.M) * 0.7, max_h=160)
                if ana:
                    f.rich("解析：" + ana, size=9.5, color=GREY, indent=12, gap=6)
        tail = []
        if o["tags"] and q.get("tags"):
            tail.append("标签：" + "、".join(q["tags"]))
        if o["source"] and source_line(q):
            tail.append(source_line(q))
        if tail:
            f.line("    ".join(tail), size=8.5, color=GREY, gap=8)
        else:
            f.y += 4

    if o["answers"] == "appendix" and questions:
        f.page_break()
        f.line("参考答案与解析", size=15, bold=True, align="center", gap=10)
        for i, q in enumerate(questions, 1):
            ans = (q.get("answer") or "").strip()
            ana = (q.get("analysis") or "").strip() if o["analysis"] else ""
            ax = image_path(q.get("answer_image"))
            f.rich(f"{i}. 答案：{ans if ans else '（见下图）'}", size=10.5, gap=2)
            if ax:
                f.image(ax, max_w=(f.W - 2 * f.M) * 0.62, max_h=150)
            if ana:
                f.rich("解析：" + ana, size=9.5, color=GREY, indent=12, gap=8)

    return f.bytes()


# ------------------------------------------------------------------ Markdown
def build_markdown(questions: list[dict], opts: dict | None = None) -> str:
    o = _opts(opts or {})
    out: list[str] = [f"# {o['title']}", ""]
    meta = [datetime.now().strftime("%Y-%m-%d"), f"共 {len(questions)} 题"]
    if o["subtitle"]:
        meta.insert(0, o["subtitle"])
    out += ["*" + " · ".join(meta) + "*", ""]

    for i, q in enumerate(questions, 1):
        out.append(f"## {question_head(i, q)}")
        out.append("")
        if (q.get("stem") or "").strip():
            out += [q["stem"].strip(), ""]
        if q.get("stem_image"):
            out += [f"![题干图]({q['stem_image']})", ""]
        opt = options_text(q)
        if opt:
            for j, line in enumerate(opt.split("    ")):
                out.append("- " + line if j else line)
            out.append("")
        if o["answers"] == "inline":
            out.append(f"**答案（{i}）：** {(q.get('answer') or '').strip() or '（见下图）'}")
            if q.get("answer_image"):
                out += ["", f"![答案图]({q['answer_image']})"]
            if o["analysis"] and (q.get("analysis") or "").strip():
                out.append(f"\n**解析：** {q['analysis'].strip()}")
            out.append("")
        tail = []
        if o["tags"] and q.get("tags"):
            tail.append("标签：" + "、".join(q["tags"]))
        if o["source"] and source_line(q):
            tail.append(source_line(q))
        if tail:
            out += ["", "> " + " ｜ ".join(tail)]
        out.append("")

    if o["answers"] == "appendix" and questions:
        out += ["---", "", "## 参考答案与解析", ""]
        for i, q in enumerate(questions, 1):
            ans = (q.get("answer") or "").strip() or "（见下图）"
            out.append(f"**{i}. 答案：** {ans}")
            if q.get("answer_image"):
                out.append("")
                out.append(f"![答案图]({q['answer_image']})")
            if o["analysis"] and (q.get("analysis") or "").strip():
                out.append(f"**解析：** {q['analysis'].strip()}")
            out.append("")
    return "\n".join(out)
