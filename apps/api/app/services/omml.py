"""把一小段 LaTeX（化学里常用的那部分）变成 Word 原生公式 OMML。

为什么不用 MML2OMML.XSL：本机（以及绝大多数老师的电脑）没有装 Office，
找不到那个 XSL。这里直接手写 OMML，实测 WPS / Word 都能正常显示、可双击编辑。

支持（覆盖面按竞赛题统计）：
  H_2O / SO_4^{2-}      上下标（含多重）
  \\frac{a}{b}           分式
  \\sqrt{x} / \\sqrt[3]{x} 根号
  \\ce{...}              化学式：元素后面的数字自动变下标、^ 后面变上标、
                         -> / <- / <=> / <-> 变箭头、+ 原样、空格原样
  \\rightarrow \\leftarrow \\rightleftharpoons \\uparrow \\downarrow
  \\alpha…\\omega \\Delta 等希腊字母
  \\cdot \\times \\pm \\approx \\le \\ge \\neq \\% \\degree \\circ
  普通文字、数字、运算符原样保留
"""

from __future__ import annotations

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "Delta": "Δ",
    "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "Sigma": "Σ", "tau": "τ", "phi": "φ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω", "Omega": "Ω",
}

SYMBOLS = {
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓",
    "approx": "≈", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥",
    "neq": "≠", "ne": "≠", "to": "→", "rightarrow": "→", "leftarrow": "←",
    "rightleftharpoons": "⇌", "leftrightarrow": "↔", "uparrow": "↑",
    "downarrow": "↓", "infty": "∞", "degree": "°", "circ": "°",
    "sum": "∑", "int": "∫", "partial": "∂", "nabla": "∇",
    "ldots": "…", "cdots": "…", "quad": "  ", "qquad": "    ", " ": " ",
    "%": "%", "{": "{", "}": "}", "_": "_", "&": "&", "#": "#",
}

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _esc(t: str) -> str:
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _run(text: str, *, sub: str | None = None, sup: str | None = None) -> str:
    """一个文字片段；带 sub/sup 时输出 m:sSub / m:sSup / m:sSubSup。"""
    if not text and not sub and not sup:
        return ""
    base = f'<m:r><m:t xml:space="preserve">{_esc(text)}</m:t></m:r>' if text else ""
    if sub and sup:
        return (f"<m:sSubSup><m:e>{base}</m:e>"
                f'<m:sub><m:r><m:t xml:space="preserve">{_esc(sub)}</m:t></m:r></m:sub>'
                f'<m:sup><m:r><m:t xml:space="preserve">{_esc(sup)}</m:t></m:r></m:sup></m:sSubSup>')
    if sub:
        return (f"<m:sSub><m:e>{base}</m:e>"
                f'<m:sub><m:r><m:t xml:space="preserve">{_esc(sub)}</m:t></m:r></m:sub></m:sSub>')
    if sup:
        return (f"<m:sSup><m:e>{base}</m:e>"
                f'<m:sup><m:r><m:t xml:space="preserve">{_esc(sup)}</m:t></m:r></m:sup></m:sSup>')
    return base


def _group(s: str, i: int) -> tuple[str, int]:
    """从 i 处读一个 {…} 或单个字符，返回 (内容, 新下标)。"""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        return "", i
    if s[i] == "{":
        depth = 0
        j = i
        while j < len(s):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], len(s)
    if s[i] == "\\":
        j = i + 1
        while j < len(s) and (s[j].isalpha()):
            j += 1
        return s[i:j], j
    return s[i], i + 1


def _ce(inner: str) -> str:
    """\ce{...} → 带上下标的 OMML。

    数字紧跟元素/右括号时变下标（H2、SO4），出现在开头或 +、空格、箭头之后
    则是系数，保持正常大小（2H2 + O2 里的前两个 2）。
    """
    out: list[str] = []
    buf: list[str] = []
    last = ""          # 上一个“内容”字符，用来判断数字是不是下标
    i = 0
    n = len(inner)

    def flush(sub: str = "", sup: str = "") -> None:
        text = "".join(buf)
        if text or sub or sup:
            out.append(_run(text, sub=sub or None, sup=sup or None))
        buf.clear()

    while i < n:
        ch = inner[i]
        if ch == "\\":
            j = i + 1
            while j < n and (inner[j].isalpha() or inner[j] in "<=>"):
                j += 1
            cmd = inner[i + 1:j]
            flush()
            sym = {"rightarrow": "→", "leftarrow": "←", "leftrightarrow": "↔",
                   "rightleftharpoons": "⇌", "to": "→"}.get(cmd, SYMBOLS.get(cmd, cmd))
            out.append(_run(sym))
            last = "→"
            i = j
            continue
        if ch == "^":
            if i + 1 < n and inner[i + 1] == "{":
                val, i = _group(inner, i + 1)
                out.append(_run("", sup=val))
            else:
                flush()
                out.append(_run("↑"))
                last = "↑"
                i += 1
            continue
        if ch == "_":
            val, i = _group(inner, i + 1)
            out.append(_run("", sub=val))
            continue
        if ch.isdigit() or ch == ".":
            j = i
            while j < n and (inner[j].isdigit() or inner[j] == "."):
                j += 1
            num = inner[i:j]
            if last and (last.isalpha() or last in ")]"):
                flush(sub=num)                       # 下标
            else:
                buf.append(num)                      # 系数
                last = num[-1]
            i = j
            continue
        if ch in "+-" and (i + 1 >= n or inner[i + 1] == " ") and last and (
                last.isalpha() or last in ")]" or last.isdigit()):
            # 化学式末尾的电荷：COO- / H+ / Fe3+
            flush()
            out.append(_run("", sup=ch))
            last = ch
            i += 1
            continue
        if ch in "<>=":
            two = inner[i:i + 2]
            if two in ("->", "<-"):
                flush()
                out.append(_run("→" if two == "->" else "←"))
                last = "→"
                i += 2
                continue
            three = inner[i:i + 3]
            if three in ("<=>", "<->"):
                flush()
                out.append(_run("⇌" if three == "<=>" else "↔"))
                last = "⇌"
                i += 3
                continue
            flush()
            out.append(_run(ch))
            last = ch
            i += 1
            continue
        if ch == " ":
            buf.append(" ")
            i += 1
            continue
        buf.append(ch)
        if not ch.isspace():
            last = ch
        i += 1
    flush()
    return "".join(out)


def _build(tex: str) -> str:
    """只返回公式体（不含外层 <m:oMath>）。"""
    s = (tex or "").strip()
    body: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(s)

    def flush() -> None:
        if buf:
            body.append(_run("".join(buf)))
            buf.clear()

    while i < n:
        ch = s[i]
        if ch == "\\":
            j = i + 1
            if j < n and s[j].isalpha():
                while j < n and s[j].isalpha():
                    j += 1
                cmd = s[i + 1:j]
                if cmd == "ce" or cmd == "pu":
                    inner, i = _group(s, j)
                    flush()
                    body.append(_ce(inner))
                    continue
                if cmd == "frac":
                    a, j2 = _group(s, j)
                    b, j3 = _group(s, j2)
                    flush()
                    body.append("<m:f><m:num>" + _build(a) + "</m:num><m:den>"
                                + _build(b) + "</m:den></m:f>")
                    i = j3
                    continue
                if cmd == "sqrt":
                    deg = None
                    k = j
                    if k < n and s[k] == "[":
                        close = s.find("]", k)
                        if close > k:
                            deg = s[k + 1:close]
                            k = close + 1
                    a, k2 = _group(s, k)
                    inner = _build(a)
                    flush()
                    if deg:
                        body.append("<m:rad><m:deg>" + _build(deg) +
                                    "</m:deg><m:e>" + inner + "</m:e></m:rad>")
                    else:
                        body.append('<m:rad><m:radPr><m:degHide m:val="1"/></m:radPr>'
                                    "<m:deg/><m:e>" + inner + "</m:e></m:rad>")
                    i = k2
                    continue
                flush()
                body.append(_run(GREEK.get(cmd) or SYMBOLS.get(cmd) or cmd))
                i = j
                continue
            # 单个符号
            sym = SYMBOLS.get(s[i + 1:i + 2], s[i + 1:i + 2])
            flush()
            body.append(_run(sym))
            i += 2
            continue
        if ch in "_^":
            base_text = "".join(buf)
            buf.clear()
            val, i = _group(s, i + 1)
            if ch == "_":
                body.append(_run(base_text, sub=val))
            else:
                body.append(_run(base_text, sup=val))
            continue
        if ch == "{":
            inner, i = _group(s, i)
            buf.append(inner)
            continue
        if ch == "}":
            i += 1
            continue
        if ch in "$ ":
            if ch == " ":
                buf.append(" ")
            i += 1
            continue
        buf.append(ch)
        i += 1
    flush()

    return "".join(body)


def latex_to_omml(tex: str, display: bool = False) -> str:
    """返回 <m:oMath>（display=True 时再包一层 <m:oMathPara>）。"""
    inner_xml = _build(tex) or _run("")
    omml = f'<m:oMath xmlns:m="{M_NS}" xmlns:w="{W_NS}">{inner_xml}</m:oMath>'
    if display:
        return f"<m:oMathPara>{omml}</m:oMathPara>"
    return omml


def plain_text(tex: str) -> str:
    """给「搜索/复制/无障碍」用的纯文字版本。"""
    s = tex or ""
    s = s.replace("\\ce{", "").replace("\\ce", "")
    for k, v in {**GREEK, **SYMBOLS}.items():
        s = s.replace("\\" + k, v)
    for k in ("frac", "sqrt", "mathrm", "text", "left", "right", "displaystyle"):
        s = s.replace("\\" + k, "")
    return s.replace("{", "").replace("}", "").replace("$", "").strip()
