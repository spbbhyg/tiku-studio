"""paperdoc/v2 渲染器：doc → HTML → PDF。

分工（《重改导出-提示词》§3）：
    数据 paperdoc/v2  →  templates/paper.html + paper.css  →  Paged.js 分页  →  Playwright 打印

这里只做「把数据摆好」和「把浏览器叫起来」，一个尺寸都不猜：
图片宽度来自 figure.w，字号行距来自 paper.css 的变量，页眉页脚来自 meta。

命令行：
    python -m app.services.paperdoc.render <doc.json> --part questions --out out.pdf
"""

from __future__ import annotations

import argparse
import html
import io
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from app.config import LIBRARY_DIR

from .schema import CONTENT_W, MARGIN_X0, MARGIN_X1, PAGE_H, PAGE_W

log = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = APP_DIR / "templates"
CSS_PATH = TEMPLATE_DIR / "paper.css"
PAGED_JS = TEMPLATE_DIR / "vendor" / "paged.polyfill.min.js"

# ---------------------------------------------------------------- 页面几何
#
# 样张实测（220 页题目合集）：
#   页眉基线 y=74.23、正文首行基线 y=100.2、末行最大 y≈768、页脚基线 y=782.3。
# @page 只吃字面量 —— Chromium 解析 @page 时不认 var()，
# 所以这一组数字必须和 paper.css 里的变量保持一致，改一处要改两处。
MARGIN_TOP = 119.1          # 正文区上边界 → 首行文字顶 121.03（样张实测）
MARGIN_BOTTOM = 69.0        # 正文区下边界 = 841.9 - 69.0 = 772.9
MARGIN_LEFT = MARGIN_X0     # 90.0
MARGIN_RIGHT = PAGE_W - MARGIN_X1   # 595.3 - 505.6 = 89.7 → 版心 415.6

HEADER_BASELINE = 89.83     # 页眉文字顶（样张实测 89.83）
FOOTER_BASELINE = 782.3     # 页脚文字顶（样张实测 782.30）
HEADER_SIZE = 10.56
FOOTER_SIZE = 9.0

# 页眉页脚都用 vertical-align:bottom 贴在页边距框底部，
# 再用 padding 往上抬，让文字顶落到样张的位置。数值由「渲染 → 量 PDF → 回填」标定。
HEADER_LIFT = 16.2          # 119.1 - (89.83 + 11.69)
FOOTER_LIFT = 48.2          # 841.9 - (782.30 + 9.96) ≈ 49.6 → 量出来差 5pt，取 47

PAGE_BREAK_TALL_RATIO = 0.75  # 超过 0.75 页的大题允许跨页（§3）


# ---------------------------------------------------------------- 文本
_ALLOWED_INLINE = ("sub", "sup", "b", "i")


def inline(text: str) -> Markup:
    """把契约里的内联标记转成 HTML。

    只放行 <sub> <sup> <b> <i> <br> <score> 六种（§1.1），其余一律转义。
    """
    s = html.escape(text or "", quote=False)
    for tag in _ALLOWED_INLINE:
        s = s.replace(f"&lt;{tag}&gt;", f"<{tag}>").replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    s = s.replace("&lt;br&gt;", "<br>").replace("&lt;br/&gt;", "<br>")
    s = s.replace("&lt;score&gt;", '<span class="score">').replace("&lt;/score&gt;", "</span>")
    return Markup(s)


_ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)
_ENV.filters["inline"] = inline


def _num(v) -> str:
    """14.0 → 14；12.5 → 12.5。分值/百分比不该印成 14.0 分。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if abs(f - round(f)) < 1e-9 else f"{f:g}"


_ENV.filters["num"] = _num


# ---------------------------------------------------------------- 图片路径


def _resolve_figure(block: dict) -> dict:
    """figure.path（相对 data/library）→ file:// 绝对地址。"""
    path = block.get("path") or ""
    out = dict(block)
    out["missing"] = True
    out["src"] = ""
    if path:
        candidate = (LIBRARY_DIR / path).resolve()
        if candidate.is_file():
            out["missing"] = False
            out["src"] = candidate.as_uri()
    return out


# ---------------------------------------------------------------- 块归一化


def _block(block: dict, where: str) -> dict:
    """补默认值，让模板里不用写一堆 if。"""
    kind = block["t"]
    if kind == "para":
        return {
            "t": "para",
            "text": block.get("text") or "",
            "style": block.get("style") or "body",
            # 题内段落默认 2 字缩进；前言「提示：」1)~6) 是 3 字缩进（CSS 里处理）
            "indent": block.get("indent", where == "item"),
        }
    if kind == "figure":
        out = _resolve_figure(block)
        out["align"] = out.get("align") or "center"
        out["caption"] = out.get("caption") or ""
        return out
    if kind == "table":
        head = block["head"]
        styles = block.get("style")
        if styles is None:
            col_styles = ["body"] * len(head)
        elif isinstance(styles, str):
            col_styles = [styles] * len(head)
        else:
            col_styles = list(styles)
        return {
            "t": "table",
            "head": head,
            "rows": block["rows"],
            "col_styles": col_styles,
            "caption": block.get("caption") or "",
        }
    if kind == "ansblock":
        return {
            "t": "ansblock",
            "for": block.get("for") or "",
            "label": block.get("label") or "",
            "blocks": [_block(b, "answer") for b in block.get("blocks") or []],
        }
    if kind == "sub":
        return {"t": "sub", "no": block["no"], "text": block.get("text") or ""}
    if kind == "heading":
        return {"t": "heading", "text": block.get("text") or "", "level": block.get("level", 1)}
    return {"t": kind}


def _blocks(blocks: list[dict], where: str) -> list[dict]:
    return [_block(b, where) for b in blocks or []]


def _interleave(blocks: list[dict], answers: list[dict]) -> list[dict]:
    """答案卷的排法：题干 → 小问 → 该小问的答案 → 下一个小问（与样张一致）。

    ansblock.for 指向 sub.no。注意一个小问常常不止一个块（1-2 后面还跟着表格和
    一段「分别写出…」），所以答案是插到「下一个小问之前」，而不是紧跟 sub 块。
    没有 for 或对不上任何小问的，统一放到题末。
    """
    by_no: dict[str, list[dict]] = {}
    tail: list[dict] = []
    known = {b["no"] for b in blocks if b.get("t") == "sub"}
    for ans in answers:
        key = ans.get("for")
        if key and key in known:
            by_no.setdefault(key, []).append(ans)
        else:
            tail.append(ans)

    out: list[dict] = []
    pending: list[dict] = []          # 已遇到、等下一段结束再落位的答案
    for b in blocks:
        if b.get("t") == "sub":
            out.extend(pending)       # 上一个小问的地盘到此为止
            pending = by_no.pop(b["no"], [])
        out.append(b)
    out.extend(pending)
    for leftovers in by_no.values():   # for 指向重复小问时剩下的
        tail.extend(leftovers)
    out.extend(tail)
    return out


def prepare_items(doc: dict, part: str = "questions") -> list[dict]:
    """把 items 摊平成模板要的形状（含答案交错）。"""
    items: list[dict] = []
    for item in doc.get("items") or []:
        blocks = item.get("blocks") or []
        if part == "answers":
            blocks = _interleave(blocks, (item.get("answer") or {}).get("blocks") or [])
        items.append(
            {
                "number": item.get("number"),
                "title": item.get("title") or "",
                "score": item.get("score"),
                "percent": item.get("percent"),
                "blocks": _blocks(blocks, "item"),
                "tall": bool(item.get("tall")),
            }
        )
    return items


# ---------------------------------------------------------------- @page


def _css_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


_PLACEHOLDER = re.compile(r"\{(page|pages|title|subject)\}")


def _footer_content(fmt: str) -> str:
    """「第 {page} 页 共 {pages} 页」→ CSS content（真页码由 counter() 填）。"""
    parts: list[str] = []
    pos = 0
    for m in _PLACEHOLDER.finditer(fmt):
        if m.start() > pos:
            parts.append(_css_string(fmt[pos:m.start()]))
        parts.append("counter(pages)" if m.group(1) == "pages" else "counter(page)")
        pos = m.end()
    if pos < len(fmt):
        parts.append(_css_string(fmt[pos:]))
    return " ".join(parts) if parts else _css_string(fmt)


def page_rule(doc: dict) -> str:
    meta = doc.get("meta") or {}
    header = (meta.get("header") or "").strip()
    footer_fmt = (meta.get("footer") or "{page}/{pages}").strip()

    rules = [
        "@page {",
        f"  size: {PAGE_W}pt {PAGE_H}pt;",
        f"  margin: {MARGIN_TOP}pt {MARGIN_RIGHT}pt {MARGIN_BOTTOM}pt {MARGIN_LEFT}pt;",
    ]
    if header:
        rules += [
            "  @top-center {",
            f"    content: {_css_string(header)};",
            f"    font-family: var(--font-body);",
            f"    font-size: {HEADER_SIZE}pt;",
            f"    line-height: 15.6pt;",
            "    vertical-align: bottom;",
            f"    padding-bottom: {HEADER_LIFT}pt;",
            "  }",
        ]
    if footer_fmt:
        rules += [
            "  @bottom-center {",
            f"    content: {_footer_content(footer_fmt)};",
            f"    font-family: var(--font-body);",
            f"    font-size: {FOOTER_SIZE}pt;",
            "    line-height: 12.6pt;",
            "    vertical-align: bottom;",
            f"    padding-bottom: {FOOTER_LIFT}pt;",
            "  }",
        ]
    rules.append("}")
    return "\n".join(rules)


# ---------------------------------------------------------------- 组装


def build_html(doc: dict, part: str = "questions") -> str:
    if part not in ("questions", "answers"):
        part = "questions"

    meta = doc.get("meta") or {}
    header = (meta.get("header") or "").replace("{title}", meta.get("title") or "").replace(
        "{subject}", meta.get("subject") or ""
    )
    # 答案卷的页眉带「参考答案」；答案卷不印试卷级前言（「提示：…」只在题目卷上）
    if part == "answers" and header and "参考答案" not in header:
        header = f"{header} 参考答案"
    meta = {**meta, "header": header}
    doc = {**doc, "meta": meta}
    if part == "answers":
        doc = {**doc, "front": []}

    return _ENV.get_template("paper.html").render(
        doc=doc,
        part=part,
        items=prepare_items(doc, part),
        page_rule=page_rule(doc),
        css_text=CSS_PATH.read_text(encoding="utf-8"),
        paged_url=PAGED_JS.as_uri(),
    )


# ---------------------------------------------------------------- 打印


def _data_tmp() -> Path:
    """优先把浏览器的临时文件放到数据目录下（一般是可写的）。"""
    try:
        from app.config import DATA_DIR
    except Exception:                                  # noqa: BLE001
        return Path(tempfile.gettempdir())
    d = Path(DATA_DIR) / "tmp"
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError:
        return Path(tempfile.gettempdir())


def _scratch_dir() -> Path:
    """给 Chromium 用的临时目录。

    系统 %TEMP% 被策略/杀软挡住时，Chromium 生成 PDF 会退化到
    `devtools_stream_file.cc: Failed to create temporary file: FILE_ERROR_ACCESS_DENIED`，
    再抛 `Page.pdf: Protocol error (IO.read): Read failed`。
    这里先试数据目录，失败再退回系统临时目录。
    """
    for base in (_data_tmp(), Path(tempfile.gettempdir())):
        try:
            return Path(tempfile.mkdtemp(prefix="paperdoc-", dir=str(base)))
        except OSError:
            continue
    return Path(tempfile.mkdtemp(prefix="paperdoc-"))


def _launch(p, env: dict | None = None):
    """本机没有 playwright 自带内核时，退回 Edge / Chrome（和旧导出器一致）。"""
    last = None
    for kwargs in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
        try:
            return p.chromium.launch(env=env, **kwargs)
        except Exception as e:      # noqa: BLE001
            last = e
    raise RuntimeError(f"没有可用的浏览器内核（{last}）")


# 量高度只加一个 class，不在页内搬 DOM（§8 禁止的是后者）。
_MARK_TALL_JS = """
async () => {
  const CONTENT_W = %f, CONTENT_H = %f;
  await Promise.all([].map.call(document.images, (im) =>
    im.decode ? im.decode().catch(() => {}) : null));
  const probe = document.createElement('style');
  probe.textContent =
    'body{width:' + CONTENT_W + 'pt !important;margin:0 !important;padding:0 !important}';
  document.head.appendChild(probe);
  let tall = 0;
  [].forEach.call(document.querySelectorAll('section.item'), (el) => {
    if (el.getBoundingClientRect().height > CONTENT_H * 0.75) {
      el.classList.add('item--tall');
      tall += 1;
    }
  });
  probe.remove();
  return tall;
}
""" % (CONTENT_W, PAGE_H - MARGIN_TOP - MARGIN_BOTTOM)


def html_to_pdf(html_doc: str, *, timeout_ms: int = 120_000) -> bytes:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:        # pragma: no cover
        raise RuntimeError("未安装 playwright：pip install playwright") from e

    fd, tmp_name = tempfile.mkstemp(suffix=".html", prefix="paperdoc-")
    os.close(fd)
    tmp = Path(tmp_name)
    scratch = _scratch_dir()
    env = dict(os.environ)
    env.update({"TMP": str(scratch), "TEMP": str(scratch), "TMPDIR": str(scratch)})
    try:
        tmp.write_text(html_doc, encoding="utf-8")
        with sync_playwright() as p:
            browser = _launch(p, env)
            try:
                page = browser.new_page()
                page.goto(tmp.as_uri(), wait_until="load", timeout=timeout_ms)
                # 先量一遍：超过 0.75 页的大题放开 break-inside（§3 的例外条款）
                tall = page.evaluate(_MARK_TALL_JS)
                # Paged.js 在这里分页；preview() 的 promise resolve 时首页已排完。
                page.evaluate(
                    "() => (window.PagedPolyfill ? window.PagedPolyfill.preview() : undefined)"
                )
                page.wait_for_timeout(300)
                log.debug("paperdoc: %d 个大题超过 0.75 页，已放开分页限制", tall)
                pdf = page.pdf(
                    prefer_css_page_size=True,
                    print_background=True,
                    margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                )
            finally:
                browser.close()
        return pdf
    finally:
        tmp.unlink(missing_ok=True)
        shutil.rmtree(scratch, ignore_errors=True)


def export_pdf(doc: dict, part: str = "questions") -> bytes:
    return html_to_pdf(build_html(doc, part))


# ---------------------------------------------------------------- 命令行


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="paperdoc.render", description="paperdoc/v2 → PDF")
    ap.add_argument("src", help="paperdoc/v2 的 JSON 文件")
    ap.add_argument("--part", default="questions", choices=["questions", "answers"])
    ap.add_argument("--out", help="输出 PDF（默认与输入同名 .pdf）")
    ap.add_argument("--html", help="同时把 HTML 落盘，方便用浏览器看")
    ap.add_argument("--png", help="同时把每页渲染成 PNG（前缀，如 out/p1）")
    ap.add_argument("--dpi", type=int, default=110)
    args = ap.parse_args(argv)

    from .schema import load_doc

    doc = load_doc(args.src)
    html_doc = build_html(doc, args.part)
    if args.html:
        Path(args.html).write_text(html_doc, encoding="utf-8")

    pdf = html_to_pdf(html_doc)
    out = Path(args.out or (Path(args.src).with_suffix("").as_posix() + f".{args.part}.pdf"))
    out.write_bytes(pdf)
    print(f"{out}  {len(pdf)} bytes")

    if args.png:
        import pymupdf

        d = pymupdf.open(stream=pdf, filetype="pdf")
        for i, page in enumerate(d):
            pix = page.get_pixmap(dpi=args.dpi)
            dst = Path(f"{args.png}{i + 1:02d}.png")
            dst.parent.mkdir(parents=True, exist_ok=True)
            pix.save(dst)
            print(f"{dst}  {pix.width}x{pix.height}")
    return 0


if __name__ == "__main__":      # pragma: no cover
    raise SystemExit(_main())
