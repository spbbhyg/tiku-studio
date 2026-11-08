"""人工整卷排版：题目树 → A4 HTML → Playwright PDF。

与 pdf_render.py 的分工：
- pdf_render 处理「题库选题卷」（每题一张图/一段文字，按题型分组）；
- 本模块处理「人工整卷」（大题 / 小问 / 小小问四级、自动编号、图注、答案卷），
  版式按样卷实测值给默认模板（90/80/72.5/82pt 边距、正文 10.45pt、行距 15.6pt）。

数据形状（paper.config + paper_questions.snapshot）：
  config = {"kind": "manual", "template": {...}, "exam": {"subtitle": "..."}}
  snapshot = {"title": "未知物的推断", "score": 14, "percent": 8,
              "intro": "黄色二元化合物……",            # 大题共同题干（\n 分段）
              "images": [{"path": "...", "caption": "图 1 ……", "width": 415}],
              "subs": [{"stem": "写出X_{1}…", "score": 2, "answer": "……",
                        "points": ["……（1 分）"], "images": [], "subs": []}],
              "answer": "…", "answer_images": [], "source": {"doc_id","page","kind"}}
公式：$...$ 交给 KaTeX（带 \ce{} 时由 mhchem 处理）；纯文本里的 X_{1} / SO_{4}^{2-}
转成 <sub>/<sup>，字号 6.85pt，与样卷一致。
"""
from __future__ import annotations

import html
import io
import re
import time
import uuid
from pathlib import Path

from ..config import API_PORT, DATA_DIR, LIBRARY_DIR

# ---------------------------------------------------------------- 模板默认值

DEFAULT_TEMPLATE: dict = {
    "margin": {"left": 90, "right": 80, "top": 72.5, "bottom": 82},
    "font": {
        "body": '"Times New Roman", SimSun, "宋体", serif',
        "head": '"Times New Roman", SimSun, "宋体", serif',
    },
    "size": 10.45,
    "subSize": 6.85,
    "lineHeight": 15.6,
    "numbering": {"depth": 4, "style": "arabic"},
    "scoreFormat": "第{n} 题{title}（{score} 分，占{percent}%）",
    "heading": {"gapBefore": 15.6, "size": 10.45},
    "figure": {"maxWidth": 415, "maxHeight": 430, "captionSize": 6.8,
               "captionColor": "#7030A0", "autoNumber": True},
    "table": {"border": 0.5, "size": 10.45},
    "answer": {"color": "#FF0000", "scoreColor": "#0000FF"},
    "header": None,     # {"text": "...", "y": 40}
    "footer": None,     # {"format": "第 {page} 页 共 {pages} 页", "y": 810}
    "title": {"size": 10.45, "bold": True},
}


def _deep_merge(base: dict, over: dict | None) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def norm_template(t: dict | None) -> dict:
    return _deep_merge(DEFAULT_TEMPLATE, t or {})


# ---------------------------------------------------------------- 小工具

def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


_SUBSUP = re.compile(r"(_\{[^}]*\}|\^\{[^}]*\}|_[A-Za-z0-9]|\^[A-Za-z0-9+\-])")
_MATH = re.compile(r"\$[^$]+\$")


def _inline(text) -> str:
    """转义 + 上下标 + 公式标记。$...$ 原样留给 KaTeX，\\ce{} 自动包成公式。"""
    raw = str(text if text is not None else "")
    out: list[str] = []
    pos = 0
    for m in _MATH.finditer(raw):
        out.append(_inline_plain(raw[pos:m.start()]))
        seg = m.group(0)
        out.append(_math_seg(seg))        # $\ce{...}$ 出纯文本，其它公式交给 KaTeX
        pos = m.end()
    out.append(_inline_plain(raw[pos:]))
    return "".join(out)


_CE_OK = re.compile(r"^[A-Za-z0-9+\-=().,<>\s^_\[\]\u00b7]+$")
_CE_RE = re.compile(r"\\ce\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")


def _ce_text(inner: str):
    """把简单 \ce{} 内容转成带 sub/sup 的纯文本（保证导出 PDF 的文字层可搜索）。"""
    if "\\" in inner or not _CE_OK.match(inner):
        return None
    s = inner.replace("<=>", "\u21cc").replace("->", "\u2192").replace("=>", "\u2192")
    out = []
    prev = ""
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "^":
            i += 1
            body = ""
            if i < n and s[i] == "{":
                j = s.index("}", i)
                body, i = s[i + 1:j], j + 1
            else:
                m = re.match(r"[0-9]*[+-]", s[i:]) or re.match(r"[0-9]+", s[i:])
                if m:
                    body, i = m.group(0), i + len(m.group(0))
            out.append("<sup>" + _esc(body) + "</sup>")
            prev = "+"
            continue
        if c == "_":
            i += 1
            body = ""
            if i < n and s[i] == "{":
                j = s.index("}", i)
                body, i = s[i + 1:j], j + 1
            else:
                m = re.match(r"[0-9]+", s[i:])
                if m:
                    body, i = m.group(0), i + len(m.group(0))
            out.append("<sub>" + _esc(body) + "</sub>")
            prev = "0"
            continue
        if c.isdigit() and (prev.isalpha() or prev in ")]"):
            m = re.match(r"[0-9]+", s[i:])
            out.append("<sub>" + m.group(0) + "</sub>")
            i += len(m.group(0))
            prev = "0"
            continue
        out.append(_esc(c))
        prev = c
        i += 1
    return "".join(out)


def _math_seg(seg: str) -> str:
    """$...$ 段：若整体只是一个 \\ce{...}，转成纯文本（PDF 文字层可搜索），否则原样。"""
    if seg.startswith("$$") and seg.endswith("$$") and len(seg) > 4:
        return seg
    inner = seg[1:-1].strip() if len(seg) > 2 else ""
    m = _CE_RE.fullmatch(inner) if inner else None
    if m:
        ce = _ce_text(m.group(1))
        if ce is not None:
            return ce
    return seg


_SUPTAG = re.compile(r"<(sub|sup)>(.*?)</(?:sub|sup)>", re.S)


def _plain_piece(text: str) -> str:
    """普通文本段：转义 + X_{1} / SO_{4}^{2-} → <sub>/<sup>。

    拆题会把上下标写成真的 <sub>/<sup> 标签，这里要放行这几个标签（只转义里面的文字），
    否则会显示成字面量。
    """
    pieces = []

    def emit(chunk: str):
        pos = 0
        for m in _SUBSUP.finditer(chunk):
            pieces.append(_esc(chunk[pos:m.start()]))
            token = m.group(0)
            tag = "sub" if token.startswith("_") else "sup"
            body = token[2:-1] if token[1] == "{" else token[1:]
            pieces.append("<" + tag + ">" + _esc(body) + "</" + tag + ">")
            pos = m.end()
        pieces.append(_esc(chunk[pos:]))

    pos = 0
    for m in _SUPTAG.finditer(text):
        emit(text[pos:m.start()])
        pieces.append("<" + m.group(1) + ">" + _esc(m.group(2)) + "</" + m.group(1) + ">")
        pos = m.end()
    emit(text[pos:])
    return "".join(pieces)


def _inline_plain(text: str) -> str:
    out = []
    pos = 0
    for m in _CE_RE.finditer(text):
        out.append(_plain_piece(text[pos:m.start()]))
        ce = _ce_text(m.group(1))
        if ce is not None:
            out.append(ce)
        else:
            out.append("$\\ce{" + _esc(m.group(1)) + "}$")
        pos = m.end()
    out.append(_plain_piece(text[pos:]))
    return "".join(out)


def _paras(text, first_no_indent: bool = True, cls: str = "") -> str:
    """纯文本按 \\n 分段：第一段顶格，后续 text-indent:2em，全部两端对齐。"""
    s = str(text if text is not None else "").replace("\r\n", "\n")
    if not s.strip():
        return ""
    lines = [ln.strip() for ln in s.split("\n")]
    lines = [ln for ln in lines if ln != ""]
    out = []
    for i, ln in enumerate(lines):
        # 小问编号行（1-1 / 1-4-1 / （1））顶格，正文段落缩进 2 字符
        numbered = bool(re.match(r"^(\d+\s*[-–]\s*\d+|（\d+）|\(\d+\))", ln))
        ind = "" if (i == 0 and first_no_indent) or numbered else " ind"
        out.append(f'<p class="body{ind}{(" " + cls) if cls else ""}">{_inline(ln)}</p>')
    return "".join(out)


def _fmt(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    return f"{f:g}"


def sub_numbers(subs: list, prefix: str, depth: int, max_depth: int, out: list) -> None:
    """递归生成 1-1 / 1-4-1 / 3-2-1-1，同时把编号写回节点（导出与前端一致）。"""
    for i, sub in enumerate(subs or [], 1):
        sub["_no"] = f"{prefix}-{i}"
        out.append(sub)
        if depth < max_depth:
            sub_numbers(sub.get("subs") or [], sub["_no"], depth + 1, max_depth, out)
        else:
            sub["subs"] = sub.get("subs") or []


def walk_subs(subs: list, prefix: str, depth: int, max_depth: int):
    """yield (node, level)；level 从 0 开始。"""
    for i, sub in enumerate(subs or [], 1):
        no = f"{prefix}-{i}"
        yield sub, no, depth
        if depth < max_depth:
            yield from walk_subs(sub.get("subs") or [], no, depth + 1, max_depth)


def sum_scores(subs: list) -> float:
    """小问分值合计：有子问的节点算子问之和（父问的分值只是汇总，不重复计）。"""
    total = 0.0
    for sub in subs or []:
        kids = sub.get("subs") or []
        if kids:
            total += sum_scores(kids)
            continue
        try:
            total += float(sub.get("score") or 0)
        except (TypeError, ValueError):
            pass
    return round(total, 2)


# ---------------------------------------------------------------- 图片

def img_url(path: str) -> str:
    p = str(path or "").strip().replace("\\", "/").lstrip("/")
    if not p:
        return ""
    if p.startswith("http") or p.startswith("data:"):
        return p
    if p.startswith("api/"):
        return "/" + p
    return "/api/images/" + p


def image_exists(path: str) -> bool:
    p = str(path or "").strip()
    if not p or p.startswith("data:") or p.startswith("http"):
        return True
    try:
        return (LIBRARY_DIR / p.replace("\\", "/")).is_file()
    except OSError:
        return False


def _natural_size(path: str, dpi: float = 150.0) -> tuple[float, float]:
    """图片按原卷 DPI 还原后的点尺寸 (宽, 高)；读不到就返回 (0, 0)。"""
    p = str(path or "").strip().replace("\\", "/")
    if not p or p.startswith("data:") or p.startswith("http"):
        return 0.0, 0.0
    try:
        from PIL import Image
        f = LIBRARY_DIR / p
        if not f.is_file():
            return 0.0, 0.0
        with Image.open(f) as im:
            w, h = im.size
        k = 72.0 / float(dpi or 150.0)
        return float(w) * k, float(h) * k
    except Exception:      # noqa: BLE001
        return 0.0, 0.0


def _natural_pt(path: str, dpi: float = 150.0) -> float:
    """按图片像素与渲染 DPI 还原它在原卷里的物理点数宽度（拆题图 = 150dpi）。"""
    p = str(path or "").strip().replace("\\", "/")
    if not p or p.startswith("data:") or p.startswith("http"):
        return 0.0
    try:
        from PIL import Image
        f = LIBRARY_DIR / p
        if not f.is_file():
            return 0.0
        with Image.open(f) as im:
            px = float(im.size[0])
        return px * 72.0 / float(dpi or 150.0)
    except Exception:
        return 0.0


def _figure(img: dict, tpl: dict, ctx: dict | None = None, maxw: float | None = None,
            boxh: float | None = None, maxh: float | None = None) -> str:
    if isinstance(img, str):
        img = {"path": img}
    path = img.get("path") or ""
    if not path:
        return ""
    full = float(tpl["figure"]["maxWidth"])
    # 没有显式 width 时按图片自身的物理尺寸（像素 / 150dpi × 72）排版，
    # 这样同一份卷子里所有图都按原样大小出现，不会一张 400pt 一张 60pt。
    natural, natural_h = _natural_size(path)
    maxh0 = float(tpl["figure"].get("maxHeight") or 430)
    if img.get("width") or img.get("w"):
        width = float(img.get("width") or img.get("w"))
    elif natural > 0:
        # 普通插图最多 260pt 宽（版心 ~60%），太高再按 200pt 高度上限收窄
        width = min(max(natural, 80.0), min(full, 200.0))
        if natural_h > 0:
            cap_w = min(maxh0, 150.0) * (natural / natural_h)
            width = min(width, cap_w)
    else:
        width = full
    cap_w = float(maxw if maxw is not None else full)
    maxw = min(width, cap_w, full)
    maxh = float(maxh or tpl["figure"].get("maxHeight") or 430)
    cap = str(img.get("caption") or "").strip()
    # 没写图注就不编号、不显示；写了才自动补「图 N」（老师只写说明文字）
    if cap and ctx is not None and tpl["figure"].get("autoNumber", True) and not cap.startswith("图"):
        ctx["fig"] = int(ctx.get("fig") or 0) + 1
        cap = f"图 {ctx['fig']}" + (f" {cap}" if cap else "")
    cap_html = ""
    if cap:
        cap_html = (f'<div class="figcap">{_inline(cap)}</div>')
    if boxh:
        # 同一行的图放进等高盒子，图按 contain 缩放 —— 视觉上「一样大」
        style = f' style="max-width:100%;max-height:100%"'
        return (f'<div class="fig fbox" style="width:{maxw:g}pt;height:{boxh:g}pt">'
                f'<img src="{_esc(img_url(path))}"{style} alt=""/>{cap_html}</div>')
    style = f' style="max-width:{maxw:g}pt;max-height:{maxh:g}pt"'
    al = (img.get("align") or "").strip()
    cls = "fig" + (f" al-{al}" if al in ("left", "center", "right") else "")
    return (f'<div class="{cls}"><img src="{_esc(img_url(path))}"{style} alt=""/>{cap_html}</div>')


def _figures(imgs, tpl: dict, ctx: dict | None = None) -> str:
    if not imgs:
        return ""
    if isinstance(imgs, (str, dict)):
        imgs = [imgs]
    imgs = [im for im in imgs if im]
    if not imgs:
        return ""
    if len(imgs) == 1:
        return _figure(imgs[0], tpl, ctx)
    # 多张图并排一行（样卷里两张电极图就是并排的），单张再大也不会溢出到下一页
    total = float(tpl["figure"]["maxWidth"])
    gap = 8.0
    # 两张并排；三张以上每行两张（原来把 N 张平分一行，7 张会缩成 52pt 的缩略图）
    cols = 2 if len(imgs) >= 2 else 1
    each = (total - gap * (cols - 1)) / cols
    boxh = float(tpl["figure"].get("rowHeight") or 150)
    rows = []
    row_max = float(tpl["figure"].get("rowMaxHeight") or 120)
    for i in range(0, len(imgs), cols):
        chunk = imgs[i:i + cols]
        sizes = []
        for im in chunk:
            path = im.get("path") if isinstance(im, dict) else im
            w, h = _natural_size(path)
            if im.get("width") and h:
                w = float(im["width"])
            sizes.append((w, h))
        asps = [w / h for w, h in sizes if w and h]
        if len(asps) == len(chunk) and asps:
            # 同一行统一高度：谁高谁矮，最后都一样高，看起来才整齐
            hh = min(row_max, (total - gap * (cols - 1)) * 0.90 / sum(asps))
            hh = max(60.0, hh)
            cell = (total - gap * (cols - 1)) / cols
            rows.append('<div class="figrow">'
                        + "".join(_figure(im, tpl, ctx, maxw=min(cell, max(40.0, a * hh)), maxh=hh)
                                  for im, a in zip(chunk, asps))
                        + "</div>")
            continue
        # 拿不到原始尺寸（比如远程图）时退回等宽
        ws = []
        for im in chunk:
            path = im.get("path") if isinstance(im, dict) else im
            explicit = im.get("width") if isinstance(im, dict) else None
            nat = float(explicit) if explicit else (_natural_pt(path) or each)
            ws.append(min(nat, each))
        w = max(70.0, min(ws))
        rows.append('<div class="figrow">'
                    + "".join(_figure(im, tpl, ctx, maxw=w) for im in chunk)
                    + "</div>")
    return "".join(rows)


def _table(tbl: dict, tpl: dict) -> str:
    head = tbl.get("head") or []
    rows = tbl.get("rows") or []
    cap = str(tbl.get("caption") or "").strip()
    parts = ['<table class="tbl">']
    if head:
        parts.append("<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead>")
    parts.append("<tbody>")
    for row in rows:
        parts.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
    parts.append("</tbody></table>")
    cap_html = f'<div class="tblcap">{_inline(cap)}</div>' if cap else ""
    return f'<div class="tblwrap">{parts[0]}{"".join(parts[1:])}{cap_html}</div>'


def _blocks(node: dict, tpl: dict, ctx: dict | None = None, key_imgs: str = "images") -> str:
    """节点里的图 / 表按出现顺序排（图在前，表在后）。"""
    out = _figures(node.get(key_imgs) or node.get("images"), tpl, ctx)
    for tbl in node.get("tables") or []:
        out += _table(tbl, tpl)
    return out


# ---------------------------------------------------------------- 参考文献

def collect_refs(items: list) -> list[dict]:
    """按出现顺序收集出处，同一 (doc_id, page) 合并，编号从 1 起。"""
    refs: list[dict] = []
    seen: dict[str, int] = {}
    for item in items or []:
        src = item.get("source") or {}
        for node in [item] + [s for s, _no, _lv in walk_subs(item.get("subs") or [], "0", 0, 99)]:
            s = node.get("source") or {}
            if not s and node is item:
                s = src
            if not s:
                continue
            key = f"{s.get('doc_id') or ''}|{s.get('page') or ''}|{s.get('kind') or ''}"
            if not (s.get("doc_id") or s.get("title")):
                continue
            if key in seen:
                node["_ref"] = seen[key]
                continue
            refs.append({
                "no": len(refs) + 1,
                "doc_id": s.get("doc_id") or "",
                "page": s.get("page") or 0,
                "title": s.get("title") or "",
                "kind": s.get("kind") or "",
                "quote": s.get("quote") or "",
            })
            seen[key] = len(refs)
            node["_ref"] = seen[key]
    for item in items or []:
        src = item.get("source") or {}
        if src:
            key = f"{src.get('doc_id') or ''}|{src.get('page') or ''}|{src.get('kind') or ''}"
            if key in seen:
                item["_ref"] = seen[key]
    return refs


def _ref_mark(node: dict) -> str:
    no = node.get("_ref")
    if not no:
        return ""
    return f'<sup class="refmark">[{no}]</sup>'


# ---------------------------------------------------------------- HTML

_CSS = """
@page { size: A4; margin: __MT__pt __MR__pt __MB__pt __ML__pt; }
* { box-sizing: border-box; }
html { color-scheme: light; }
body {
  margin: 0; padding: 0; background: #fff; color: #000;
  font-family: __BODY_FONT__;
  font-size: __SIZE__pt; line-height: __LHPX__px;
  text-align: left;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
p.body { margin: 0; padding: 0; text-align: justify; text-justify: inter-ideograph; }
p.body.ind { text-indent: 2em; }
sub, sup { font-size: __SUBSIZE__pt; line-height: 0; }
sub { vertical-align: -0.22em; }
sup { vertical-align: 0.42em; }
sup.refmark { font-size: __SUBSIZE__pt; vertical-align: 0.5em; margin-left: 1pt; }

h1.title {
  margin: 0; padding: 0; text-align: center;
  font-family: __HEAD_FONT__;
  font-size: __TITLE_SIZE__pt; line-height: __LH__pt; font-weight: __TITLE_WEIGHT__;
}
p.subtitle { margin: 0; padding: 0; text-align: center; }
p.stu { margin: 0; padding: 0; text-align: center; letter-spacing: .5pt; }

h2.qhead {
  margin: __GAP__pt 0 0; padding: 0;
  font-family: __HEAD_FONT__;
  font-size: __HEAD_SIZE__pt; line-height: __LH__pt; font-weight: 700;
  break-after: avoid; page-break-after: avoid;
}
h2.qhead .qnum { font-weight: 700; }

.item { break-inside: auto; }
.sub { margin: 0; padding: 0; break-inside: auto; page-break-inside: auto; }
.sub.long { break-inside: auto; page-break-inside: auto; }
.sub > p.body { orphans: 2; widows: 2; }
span.no { font-weight: 700; }

.fig { text-align: center; margin: 3.2pt 0 3.2pt; break-inside: avoid; page-break-inside: avoid; }
.fig img { display: block; margin: 0 auto; max-width: __FIGW__pt; }
.fig.al-left { text-align: left; }
.fig.al-left img { margin-left: 0; margin-right: auto; }
.fig.al-right { text-align: right; }
.fig.al-right img { margin-left: auto; margin-right: 0; }
.figrow { display: flex; gap: 8pt; justify-content: center; align-items: flex-end; flex-wrap: wrap; margin: 3.2pt 0 0; break-inside: auto; page-break-inside: auto; }
.figrow .fig { margin: 0; flex: 0 0 auto; }
.figrow img { max-height: none; }
.fig.fbox { display: flex; flex-direction: column; align-items: center; justify-content: center; overflow: hidden; }
.fig.fbox img { width: 100%; height: 100%; object-fit: contain; }
.figcap { text-align: center; font-size: __CAPSIZE__pt; color: __CAPCOLOR__; line-height: 12pt; }

.tblwrap { margin: 3.2pt 0; break-inside: avoid; page-break-inside: avoid; }
table.tbl { border-collapse: collapse; margin: 0 auto; font-size: __TBLSIZE__pt; line-height: 16pt; }
table.tbl th, table.tbl td {
  border: __TBLBORDER__pt solid #000; padding: 0 3pt; text-align: center; font-weight: normal;
}
table.tbl th { font-weight: 700; }
.tblcap { text-align: center; font-size: __CAPSIZE__pt; color: __CAPCOLOR__; }

.ansblock { margin: 0; padding: 0; color: __ANSCOLOR__; }
.ansblock p.body { color: __ANSCOLOR__; }
.scmark { color: __SCORECOLOR__; }
.pointline { color: __ANSCOLOR__; }
p.pointline .scmark { margin-left: 2pt; }

.refs { margin-top: __GAP__pt; break-inside: avoid; }
.refs h2 { margin: 0; font-size: __HEAD_SIZE__pt; line-height: __LH__pt; font-weight: 700; }
.refs p { margin: 0; font-size: 8.4pt; line-height: 12.6pt; text-indent: -1.2em; padding-left: 1.2em; }
.pagebreak { break-before: page; page-break-before: always; }

/* 屏幕预览：加一点页面留白，打印时自动去掉 */
@media screen {
  body { background: #f3f4f6; }
  .sheet { background: #fff; width: 210mm; min-height: 297mm; padding: __MT__pt __MR__pt __MB__pt __ML__pt; margin: 0 auto; box-shadow: 0 2px 12px rgba(16,24,40,.16); }
  .sheet .sheet-inner { width: auto; }
}
/* 由 __paginate() 生成的固定尺寸页框：每框正好一页，换页只发生在块与块之间 */
.pgwrap { display: block; }
.pg { width: __CW__pt; height: __CH__pt; overflow: hidden; }

@media print {
  .sheet { padding: 0; margin: 0; box-shadow: none; width: auto; }
  .pg { break-after: page; page-break-after: always; }
  .pg:last-child { break-after: auto; page-break-after: auto; }
}
"""


def _css(tpl: dict) -> str:
    m = tpl["margin"]
    vals = {
        "MT": _fmt(m["top"]), "MR": _fmt(m["right"]), "MB": _fmt(m["bottom"]), "ML": _fmt(m["left"]),
        "BODY_FONT": tpl["font"]["body"], "HEAD_FONT": tpl["font"]["head"],
        "SIZE": _fmt(tpl["size"]), "LH": _fmt(tpl["lineHeight"]),
        "LHPX": _fmt(round(float(tpl["lineHeight"]) * 96 / 72, 3)),
        "SUBSIZE": _fmt(tpl.get("subSize", 6.85)),
        "TITLE_SIZE": _fmt(tpl["title"]["size"]),
        "TITLE_WEIGHT": "700" if tpl["title"]["bold"] else "400",
        "GAP": _fmt(tpl["heading"]["gapBefore"]), "HEAD_SIZE": _fmt(tpl["heading"]["size"]),
        "FIGW": _fmt(tpl["figure"]["maxWidth"]), "CAPSIZE": _fmt(tpl["figure"]["captionSize"]),
        "CAPCOLOR": tpl["figure"]["captionColor"],
        "TBLSIZE": _fmt(tpl["table"]["size"]), "TBLBORDER": _fmt(tpl["table"]["border"]),
        "ANSCOLOR": tpl["answer"]["color"], "SCORECOLOR": tpl["answer"]["scoreColor"],
        "CW": _fmt(595.276 - float(m["left"]) - float(m["right"])),
        "CH": _fmt(841.89 - float(m["top"]) - float(m["bottom"])),
    }
    out = _CSS
    for k, v in vals.items():
        out = out.replace(f"__{k}__", str(v))
    return out


_SCRIPT = """
(function () {
  function go() {
    try {
      if (window.renderMathInElement) {
        window.renderMathInElement(document.body, {
          delimiters: [
            { left: '$$', right: '$$', display: true },
            { left: '$', right: '$', display: false }
          ],
          throwOnError: false,
          output: 'html',
          ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'],
          ignoredClasses: ['katex']
        });
      }
    } catch (e) { /* 公式渲染失败不影响文字导出 */ }
    // 图片没加载完之前高度是 0，分页会算错；等它们都就位再分页
    var waiting = Array.prototype.map.call(document.images, function (im) {
      if (im.complete && im.naturalWidth) return null;
      return new Promise(function (res) {
        im.addEventListener('load', res);
        im.addEventListener('error', res);
      });
    }).filter(Boolean);
    var done = function () {
      try {
        if (new URLSearchParams(location.search).has('paged') && window.__paginate) window.__paginate();
      } catch (e) { /* 分页失败就退回浏览器的自动分页 */ }
      window.__examReady = true;
      document.documentElement.setAttribute('data-ready', '1');
    };
    if (!waiting.length) { done(); return; }
    Promise.race([
      Promise.all(waiting),
      new Promise(function (r) { setTimeout(r, 6000); })
    ]).then(done);
  }
  /* 按「块」分页：先量每个块的实测高度，装满一页就换页，
     只在段落 / 图 / 表格之间断开，不会把一张图劈成两半。 */
  window.__paginate = function () {
    var root = document.documentElement;
    var CW = parseFloat(root.getAttribute('data-cw')) || 415;
    var CH = parseFloat(root.getAttribute('data-ch')) || 687;
    var CHPX = CH * 96 / 72;
    var inner = document.querySelector('.sheet-inner');
    if (!inner || inner.getAttribute('data-paged')) return;
    var units = [];
    Array.prototype.forEach.call(inner.children, function (el) {
      if (el.classList && el.classList.contains('item')) {
        Array.prototype.forEach.call(el.children, function (c) {
          if ((c.classList.contains('sub') || c.classList.contains('ansblock')) && c.children.length > 1) {
            Array.prototype.forEach.call(c.children, function (g) { units.push(g); });
          } else { units.push(c); }
        });
      } else { units.push(el); }
    });
    var host = document.createElement('div');
    host.className = 'pgwrap';
    // 必须先挂到文档里，否则 scrollHeight 永远是 0，判断不出溢出
    inner.replaceChildren(host);
    var page = null;
    function newPage() {
      page = document.createElement('div');
      page.className = 'pg';
      host.appendChild(page);
    }
    newPage();
    units.forEach(function (u) {
      page.appendChild(u);
      if (page.scrollHeight > CHPX + 0.5 && page.children.length > 1) {
        page.removeChild(u);
        var carry = [];
        while (page.children.length > 1 && page.lastElementChild.classList.contains('qhead')) {
          carry.unshift(page.removeChild(page.lastElementChild));
        }
        newPage();
        carry.forEach(function (c) { page.appendChild(c); });
        page.appendChild(u);
      }
    });
    inner.setAttribute('data-paged', '1');
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go);
  else go();
})();
"""


def _head(doc: dict, tpl: dict, part: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN" data-cw="{_fmt(595.276 - float(tpl["margin"]["left"]) - float(tpl["margin"]["right"]))}" data-ch="{_fmt(841.89 - float(tpl["margin"]["top"]) - float(tpl["margin"]["bottom"]))}"><head><meta charset="utf-8">
<title>{_esc(doc.get("title") or "试卷")}</title>
<link rel="stylesheet" href="/katex/katex.min.css">
<style>{_css(tpl)}</style>
<script defer src="/katex/katex.min.js"></script>
<script defer src="/katex/contrib/mhchem.min.js"></script>
<script defer src="/katex/contrib/auto-render.min.js"></script>
</head><body>
"""


def _tail() -> str:
    return f"<script>{_SCRIPT}</script>\n</body></html>"


def _sub_html(sub: dict, no: str, part: str, tpl: dict, level: int, ctx: dict | None = None) -> str:
    score = sub.get("score")
    sc = _fmt(score) if score not in (None, "", 0, "0") else ""
    numspan = f'<span class="no">{_esc(no)}</span> ' if no else ''
    head = (f'<p class="body">{numspan}'
            f'{_inline(str(sub.get("stem") or "").strip())}</p>')
    extra = _paras(sub.get("stem2") or "", True)
    figs = _blocks(sub, tpl, ctx)
    inner = head + figs + extra
    ans_html = ""
    if part in ("answers", "all"):
        ans = str(sub.get("answer") or "").strip()
        points = sub.get("points") or []
        aimg = _figures(sub.get("answer_images") or [], tpl, ctx)
        bits = ['<div class="ansblock">']
        if sc and not (sub.get("subs") or []):
            # 与样卷答案卷一致：蓝字「1-1 共5 分」，父问只由子问给分，不重复标总数
            lbl = (f"{_esc(no)} " if no else "本题") + f"共{sc} 分"
            bits.append(f'<p class="body"><span class="scmark">{lbl}</span></p>')
        if ans:
            bits.append(_paras(ans, True))
        bits.append(aimg)
        for pt in points:
            if isinstance(pt, dict):
                txt = str(pt.get("text") or "").strip()
                psc = pt.get("score")
            else:
                txt = str(pt or "").strip()
                psc = None
            if not txt:
                continue
            mark = f' <span class="scmark">（{_fmt(psc)} 分）</span>' if psc not in (None, "") else ""
            bits.append(f'<p class="body pointline">{_inline(txt)}{mark}</p>')
        bits.append("</div>")
        ans_html = "".join(bits)
    nested = ""
    for i, child in enumerate(sub.get("subs") or [], 1):
        nested += _sub_html(child, f"{no}-{i}", part, tpl, level + 1, ctx)
    long_cls = " long" if len(str(sub.get("stem") or "")) > 320 else ""
    return f'<div class="sub{long_cls}">{inner}{ans_html}{nested}</div>'


def build_doc_html(doc: dict, part: str = "questions") -> str:
    """doc = {title, subtitle, duration, total_score, template, exam, items:[...]}"""
    doc = dict(doc or {})
    items = [dict(it or {}) for it in (doc.get("items") or [])]
    tpl = norm_template(doc.get("template") or (doc.get("config") or {}).get("template"))
    max_depth = int(tpl["numbering"].get("depth") or 4)
    refs = collect_refs(items)
    ctx = {"fig": 0}

    total = 0.0
    for it in items:
        try:
            total += float(it.get("score") or 0)
        except (TypeError, ValueError):
            pass
    if total <= 0:
        try:
            total = float(doc.get("total_score") or 0)
        except (TypeError, ValueError):
            total = 0.0

    out: list[str] = []
    out.append(_head(doc, tpl, part))
    out.append('<div class="sheet"><div class="sheet-inner">')
    title = str(doc.get("title") or "试卷").strip()
    out.append(f'<h1 class="title">{_inline(title)}</h1>')
    sub = str(doc.get("subtitle") or "").strip()
    if sub:
        out.append(f'<p class="subtitle">{_inline(sub)}</p>')

    for idx, item in enumerate(items, 1):
        number = int(item.get("number") or idx)
        score = item.get("score") or 0
        percent = item.get("percent")
        if percent in (None, ""):
            percent = round(float(score or 0) / total * 100) if total else 0
        head_txt = tpl["scoreFormat"].format(
            n=number, title=_inline(str(item.get("title") or "").strip()),
            score=_fmt(score), percent=_fmt(percent))
        out.append(f'<div class="item" data-number="{number}">')
        out.append(f'<h2 class="qhead"><span class="qnum">{head_txt}</span>{_ref_mark(item)}</h2>')
        out.append(_paras(item.get("intro") or "", True))
        out.append(_blocks(item, tpl, ctx))
        for i, sub_node in enumerate(item.get("subs") or [], 1):
            subs = item.get("subs") or []
            # 只有一个没有子问的「小问」时，不显示 1-1 编号（单块题目）
            no = "" if (len(subs) == 1 and not (subs[0].get("subs") or [])) else f"{number}-{i}"
            out.append(_sub_html(sub_node, no, part, tpl, 0, ctx))
        if part in ("answers", "all"):
            ans = str(item.get("answer") or "").strip()
            aimg = _figures(item.get("answer_images") or [], tpl, ctx)
            if ans or aimg:
                lead = ""
                if not (item.get("subs") or []) and float(score or 0) > 0:
                    lead = (f'<p class="body"><span class="scmark">本题共{_fmt(score)} 分</span></p>')
                out.append(f'<div class="ansblock">{lead}{_paras(ans, True)}{aimg}</div>')
        out.append("</div>")

    if refs:
        out.append('<div class="refs"><h2>参考文献</h2>')
        for r in refs:
            bits = []
            if r.get("title"):
                bits.append(r["title"])
            elif r.get("doc_id"):
                bits.append(f"文献 {r['doc_id']}")
            if r.get("page"):
                bits.append(f"第 {r['page']} 页")
            if r.get("quote"):
                bits.append(f"「{r['quote'][:60]}」")
            out.append(f'<p>[{r["no"]}] {"，".join(bits)}</p>')
        out.append("</div>")

    out.append("</div></div>")
    out.append(_tail())
    return "".join(out)


# ---------------------------------------------------------------- 校验

def check_issues(doc: dict) -> list[dict]:
    """导出前校验：空题干 / 0 分 / 题号断档 / 子问分值和≠父题 / 图未加载。"""
    issues: list[dict] = []
    items = doc.get("items") or []
    if not items:
        issues.append({"level": "error", "code": "empty", "msg": "整卷还没有题目"})
    total = 0.0
    for i, item in enumerate(items, 1):
        number = int(item.get("number") or i)
        if number != i:
            issues.append({"level": "warn", "code": "number",
                           "msg": f"题号断档：第 {i} 个位置写的是第 {number} 题"})
        try:
            sc = float(item.get("score") or 0)
        except (TypeError, ValueError):
            sc = 0.0
        total += sc
        if sc <= 0:
            issues.append({"level": "warn", "code": "zero",
                           "msg": f"第 {number} 题没有分值"})
        if not str(item.get("intro") or "").strip() and not (item.get("subs") or []):
            issues.append({"level": "error", "code": "empty",
                           "msg": f"第 {number} 题题干为空"})
        subs = item.get("subs") or []
        if subs:
            s = sum_scores(subs)
            if abs(s - sc) > 0.01:
                issues.append({"level": "warn", "code": "sum",
                               "msg": f"第 {number} 题：小问合计 {_fmt(s)} 分 ≠ 大题 {_fmt(sc)} 分"})
        for node, no, _lv in walk_subs(subs, str(number), 0, 99):
            if not str(node.get("stem") or "").strip() and not (node.get("images") or []):
                issues.append({"level": "error", "code": "empty", "msg": f"{no} 题干为空"})
            try:
                if float(node.get("score") or 0) <= 0:
                    issues.append({"level": "warn", "code": "zero", "msg": f"{no} 没有分值"})
            except (TypeError, ValueError):
                issues.append({"level": "warn", "code": "zero", "msg": f"{no} 分值不是数字"})
            for img in (node.get("images") or []) + (node.get("answer_images") or []):
                p = img.get("path") if isinstance(img, dict) else img
                if p and not image_exists(p):
                    issues.append({"level": "error", "code": "image",
                                   "msg": f"{no} 的图找不到：{p}"})
        for img in (item.get("images") or []):
            p = img.get("path") if isinstance(img, dict) else img
            if p and not image_exists(p):
                issues.append({"level": "error", "code": "image",
                               "msg": f"第 {number} 题的图找不到：{p}"})
    return issues


# ---------------------------------------------------------------- 预览缓存

CACHE: dict[str, tuple[float, str]] = {}
CACHE_TTL = 1800.0
CACHE_MAX = 40


def cache_put(html_doc: str) -> str:
    now = time.time()
    for k, (ts, _h) in list(CACHE.items()):
        if now - ts > CACHE_TTL:
            CACHE.pop(k, None)
    while len(CACHE) >= CACHE_MAX:
        oldest = min(CACHE.items(), key=lambda kv: kv[1][0])[0]
        CACHE.pop(oldest, None)
    token = uuid.uuid4().hex
    CACHE[token] = (now, html_doc)
    return token


def cache_get(token: str) -> str | None:
    hit = CACHE.get(token)
    if not hit:
        return None
    ts, doc = hit
    if time.time() - ts > CACHE_TTL:
        CACHE.pop(token, None)
        return None
    return doc


# ---------------------------------------------------------------- 导出

def base_url() -> str:
    return f"http://127.0.0.1:{API_PORT}"


def _launch(p, env: dict | None = None):
    last = None
    for kwargs in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
        try:
            return p.chromium.launch(env=env, **kwargs)
        except Exception as e:      # noqa: BLE001
            last = e
    raise RuntimeError(f"没有可用的浏览器内核（{last}）")


def _browser_env() -> dict:
    """让 Chromium 把临时文件写到可写目录。

    系统 %TEMP% 被策略/杀软挡住时，Chromium 生成 PDF 会报
    `FILE_ERROR_ACCESS_DENIED`，Playwright 再抛
    `Page.pdf: Protocol error (IO.read): Read failed`。
    """
    import os
    import tempfile
    from pathlib import Path

    env = dict(os.environ)
    for base in (Path(DATA_DIR) / "tmp", Path(tempfile.gettempdir())):
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = tempfile.mkdtemp(prefix="examrender-", dir=str(base))
            env.update({"TMP": probe, "TEMP": probe, "TMPDIR": probe})
            return env
        except OSError:
            continue
    return env


def export_pdf(doc: dict, part: str = "questions", url_base: str | None = None) -> bytes:
    """题目树 → PDF。走本机 HTTP 加载（KaTeX 字体、图片都需要真实同源）。"""
    if part not in ("questions", "answers", "all"):
        part = "questions"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:      # pragma: no cover
        raise RuntimeError("未安装 playwright：pip install playwright && playwright install chromium") from e

    tpl = norm_template(doc.get("template"))
    html_doc = build_doc_html(doc, part)
    token = cache_put(html_doc)
    url = f"{(url_base or base_url()).rstrip('/')}/api/exam/html/{token}?paged=1"
    buf = io.BytesIO()
    try:
        with sync_playwright() as p:
            browser = _launch(p, _browser_env())
            kwargs = {"format": "A4", "print_background": True, "prefer_css_page_size": True}
            header = tpl.get("header") or {}
            footer = tpl.get("footer") or {}
            if (header.get("text") or footer.get("format")):
                kwargs["display_header_footer"] = True
                kwargs["header_template"] = (
                    "<div style='width:100%;font-size:8.4pt;color:#000;text-align:center;"
                    "font-family:SimSun;padding:0 12mm;'>" + _esc(header.get("text") or "") + "</div>"
                )
                fmt = str(footer.get("format") or "第 {page} 页 共 {pages} 页")
                fmt = fmt.replace("{page}", "@P@").replace("{pages}", "@T@")
                if "页" in fmt:  # 老师一般直接写「第 X 页 共 Y 页」
                    fmt = re.sub(r"(?<![A-Za-z])X(?![A-Za-z])", "@P@", fmt)
                    fmt = re.sub(r"(?<![A-Za-z])Y(?![A-Za-z])", "@T@", fmt)
                fmt = fmt.replace("@P@", "<span class=pageNumber></span>").replace("@T@", "<span class=totalPages></span>")
                kwargs["footer_template"] = (
                    "<div style='width:100%;font-size:8.4pt;color:#000;text-align:center;"
                    "font-family:SimSun;padding:0 12mm;'>" + fmt + "</div>"
                )
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=60000)
            try:
                page.wait_for_function("window.__examReady === true", timeout=20000)
            except Exception:
                page.wait_for_timeout(600)
            pdf = page.pdf(**kwargs)
            browser.close()
            buf.write(pdf)
    except Exception as e:      # noqa: BLE001
        raise RuntimeError(f"PDF 导出失败（{e}）。可先打开网页打印视图另存为 PDF。") from e
    return buf.getvalue()


def write_html_file(doc: dict, part: str, path: Path) -> None:
    path.write_text(build_doc_html(doc, part), encoding="utf-8")
