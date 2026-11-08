"""按「第 N 题」标题把 PDF 切成题目图片；支持自动去掉四周白边。

用像素切分出题目图，同时从同一矩形里抽**文字层**（PDF 自带文本，非 OCR），
调用方传入 PDF 路径，返回 [{no, title, path, files, text, paras}]。
"""
from __future__ import annotations

import io
import os
import re
import time
from pathlib import Path

import fitz
from PIL import Image, ImageChops

HEADPAT = ("第{no} 题", "第{no}题", "第 {no} 题", "第 {no}题")
TITLE = re.compile(r"第\s*(\d{1,2})\s*题\s*([^\n（(]{0,40})[（(]([^）)]{0,20})[）)]")
MAX_SPAN = 3          # 一道题最多跨 3 页
MIN_H = 40            # 太窄的条带丢弃
MARGIN = 10           # 白边裁剪后保留的呼吸空间
DPI = 150
TABLE_DPI = 300       # 表格抽不出可信单元格时，整块按 300dpi 裁成图

# 版心左右边界（A4 595.3×841.9，见 paper.css）；用来判断图是左/中/右对齐
PAGE_X0 = 90.0
PAGE_X1 = 505.6
# 正文区上下边界：页眉基线 89.8、页脚基线 782.3，再让 6pt 呼吸
BODY_TOP = 96.0
BODY_BOTTOM = 776.0

# PDF 抽取残格：「0.」这种只剩小数点的，或「05」+「.」被拆到两行
RESIDUAL = re.compile(r"^\s*(?:\d+\s*[.．]\s*|[.,;:、]\s*\d*\s*)$")


def _ink_box(im: Image.Image):
    """白边内墨迹的像素框；整幅白纸返回 None。"""
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    diff = ImageChops.difference(im.convert("RGB"), bg)
    gray = diff.convert("L").point(lambda v: 255 if v > 12 else 0)
    return gray.getbbox()


def _trim_with_box(im: Image.Image):
    """去白边，同时把裁切框（含呼吸边）一起返回 —— 图片几何要靠它算。"""
    box = _ink_box(im)
    if not box:
        return im, (0, 0, im.width, im.height)
    l, t, r, b = box
    l = max(0, l - MARGIN)
    t = max(0, t - MARGIN)
    r = min(im.width, r + MARGIN)
    b = min(im.height, b + MARGIN)
    return im.crop((l, t, r, b)), (l, t, r, b)


def _trim(im: Image.Image) -> Image.Image:
    """去掉四周纯白（或近白）边。"""
    return _trim_with_box(im)[0]


def _marks(doc: fitz.Document) -> dict[int, tuple[int, float, str]]:
    """找出每道题标题所在 (页号, y, 标题文字)。"""
    out: dict[int, tuple[int, float, str]] = {}
    for pi in range(doc.page_count):
        page = doc[pi]
        text = page.get_text()
        for m in TITLE.finditer(text):
            no = int(m.group(1))
            if no in out:
                continue
            rects = []
            for k in range(1, 4):
                for pat in HEADPAT:
                    rects = page.search_for(pat.format(no=no))
                    if rects:
                        break
                if rects:
                    break
            if not rects:
                continue
            out[no] = (pi, float(rects[0].y0), (m.group(2) or "").strip())
    return out


_ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}


def _esc(t: str) -> str:
    return "".join(_ESC.get(c, c) for c in t)


def _line_html(line: dict) -> str:
    """把一行里的 span 拼起来；明显小一号且下沉/上浮的当上下标。"""
    spans = [x for x in (line.get("spans") or []) if (x.get("text") or "").strip()]
    if not spans:
        return ""
    base = max(float(x["origin"][1]) for x in spans)
    mx = max(float(x.get("size") or 0) for x in spans) or 10.0
    out = []
    for sp in spans:
        t = sp.get("text") or ""
        if not t.strip():
            continue
        dy = float(sp["origin"][1]) - base
        if float(sp.get("size") or mx) < mx * 0.86 and dy > 0.8:
            t = f"<sub>{_esc(t)}</sub>"
        elif float(sp.get("size") or mx) < mx * 0.86 and dy < -0.8:
            t = f"<sup>{_esc(t)}</sup>"
        else:
            t = _esc(t)
        out.append(t)
    return "".join(out).strip()


def rich_paras(page, clip) -> list[str]:
    """一个矩形里的文字 → 段落 HTML（行按纵向间隔合并成段）。"""
    try:
        d = page.get_text("dict", clip=clip)
    except Exception:
        return []
    lines = []
    for blk in d.get("blocks") or []:
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines") or []:
            html = _line_html(ln)
            if not html:
                continue
            x0, y0, x1, y1 = ln.get("bbox") or (0, 0, 0, 0)
            lines.append((round(y0, 1), x0, y1 - y0, html))
    if not lines:
        return []
    lines.sort(key=lambda t: (t[0], t[1]))
    paras, cur, last_y, last_h = [], [], None, 0.0
    for y, _x, h, html in lines:
        gap = (y - last_y) if last_y is not None else 0
        # 新段落：空行、或行首是「第 N 题」「1-1」「（1）」这类编号
        starts_new = bool(re.match(r"^(第\s*\d+\s*题|\d+\s*[-–]\s*\d+|\d+\s*[.．、]\s)", re.sub(r"<[^>]+>", "", html)))
        prev_txt = re.sub(r"<[^>]+>", "", cur[-1]) if cur else ""
        ends_sentence = bool(re.search(r"[。！？!?；;]$", prev_txt.rstrip()))
        # 只在「行首是新编号」或「上一行已结句且间距明显变大」时断段，
        # 避免公式行、上下标行把一句话切成两段。
        if cur and (starts_new or (gap > max(7.5, last_h * 1.6) and ends_sentence) or gap > 18):
            paras.append("".join(cur))
            cur = []
        if cur and re.search(r"[A-Za-z0-9]$", cur[-1]) and re.match(r"[A-Za-z0-9]", html):
            cur.append(" ")
        cur.append(html)
        last_y = y
        last_h = max(last_h, h)
    if cur:
        paras.append("".join(cur))
    # 后处理：若上一段没有以句末标点收尾，说明是被公式行/上下标行切断的，
    # 把下一段并回去（除非下一段本身是新的编号行）。
    merged: list[str] = []
    for p in paras:
        p = p.strip()
        if not p:
            continue
        if merged:
            prev = merged[-1]
            plain = re.sub(r"<[^>]+>", "", prev).rstrip()
            new_num = bool(re.match(r"^(第\s*\d+\s*题|\d+\s*[-–]\s*\d+|\d+\s*[.．、]\s|\(?（\d+）)", re.sub(r"<[^>]+>", "", p)))
            if not new_num and not re.search(r"[。！？!?；;]$", plain):
                sep = " " if re.search(r"[A-Za-z0-9]$", plain) and re.match(r"[A-Za-z0-9]", p) else ""
                merged[-1] = prev + sep + p
                continue
        merged.append(p)
    return merged


def _text_rects(page, clip) -> list[fitz.Rect]:
    out = []
    try:
        d = page.get_text("dict", clip=clip)
    except Exception:
        return out
    for blk in d.get("blocks") or []:
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines") or []:
            b = ln.get("bbox")
            if b:
                out.append(fitz.Rect(b))
    return out


def _merge(boxes: list[fitz.Rect]) -> list[fitz.Rect]:
    # 单个原子的位图只有 5×7pt，这里不能按大框过滤，否则结构式全丢
    boxes = [fitz.Rect(b) for b in boxes if b.width > 1.5 and b.height > 1.5]
    changed = True
    while changed:
        changed = False
        out: list[fitz.Rect] = []
        for b in boxes:
            for i, o in enumerate(out):
                grown = fitz.Rect(o.x0 - 6, o.y0 - 12, o.x1 + 6, o.y1 + 12)
                if grown.intersects(b):
                    out[i] = fitz.Rect(min(o.x0, b.x0), min(o.y0, b.y0), max(o.x1, b.x1), max(o.y1, b.y1))
                    changed = True
                    break
            else:
                out.append(fitz.Rect(b))
        boxes = out
    return boxes


def figure_rects(page, clip, text_rects: list[fitz.Rect], skip_sigs: set | None = None) -> list[fitz.Rect]:
    """clip 里不是文字的墨迹区域（图 / 结构式 / 表格线）→ 图块矩形。

    结构式常被拆成几十个单个原子的位图（5×7pt）+ 若干矢量键线，所以候选框的
    下限必须很小，靠 _merge 把它们并成一个整图；并完再按 40×20 过滤噪声。
    """
    cand: list[fitz.Rect] = []
    skip_sigs = skip_sigs or set()
    try:
        for info in page.get_image_info():
            if (info.get("width"), info.get("height")) in skip_sigs:
                continue  # 每页重复出现的水印 / 底纹
            b = info.get("bbox")
            if not b:
                continue
            r = fitz.Rect(b) & clip
            if r.is_empty or r.width < 2 or r.height < 2:
                continue
            cand.append(r)
    except Exception:
        pass
    try:
        for dr in page.get_drawings():
            b = dr.get("rect")
            if not b:
                continue
            r = fitz.Rect(b)
            if r.width < 4 and r.height < 4:
                continue
            if r.y1 < clip.y0 or r.y0 > clip.y1:
                continue
            cand.append(r & clip)
    except Exception:
        pass
    boxes = []
    for r in cand:
        inter = 0.0
        for t in text_rects:
            o = r & t
            if not o.is_empty:
                inter += o.width * o.height
        area = max(r.width * r.height, 1e-6)
        if inter / area > 0.45:
            continue
        boxes.append(r)
    out = []
    for r in _merge(boxes):
        r = fitz.Rect(r) & clip
        if r.width < 40 or r.height < 20:
            continue
        # 高度过小且宽度接近整行 → 多半是分隔线/表格线
        if r.height < 26 and r.width > clip.width * 0.8:
            continue
        out.append(r)
    out.sort(key=lambda r: r.y0)
    return out


def _cell(v) -> str:
    t = "" if v is None else str(v)
    t = re.sub(r"\s*\n\s*\.", ".", t)      # 行内换行 + 孤立小数点
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _cell_words(page, rect) -> str:
    """一个单元格里的字：按行聚类、按 x 排序重拼。

    extract() 会把「0.5」拼成「05\n.」（小数点被甩到下一行），这里按原始坐标
    逐词重排，间隙 <1.2pt 就不加空格，能还原「0.5」「14.66」。
    """
    if rect is None:
        return ""
    try:
        words = page.get_text("words", clip=fitz.Rect(rect))
    except Exception:
        return ""
    if not words:
        return ""
    words.sort(key=lambda w: (round(float(w[1]), 1), float(w[0])))
    lines: list[list] = []
    for w in words:
        x0, y0, x1, y1, txt = float(w[0]), float(w[1]), float(w[2]), float(w[3]), w[4]
        if lines and abs(y0 - lines[-1][0]) <= 2.0:
            lines[-1][1].append((x0, x1, txt))
        else:
            lines.append([y0, [(x0, x1, txt)]])
    out = []
    for _y, parts in lines:
        parts.sort(key=lambda p: p[0])
        s = ""
        prev_x1 = None
        for x0, x1, txt in parts:
            if s and prev_x1 is not None and x0 - prev_x1 > 1.2:
                s += " "
            s += txt
            prev_x1 = x1
        out.append(s.strip())
    return "\n".join(o for o in out if o)


def _merge_rows(boxes: list) -> list:
    """同一行上的多个图块并成一个（样张题末两个结构式是并排的）。

    7 种 block 里没有「行」的概念，并排关系只能在导入时定死：纵向重叠超过
    较矮者 60% 的框合成一个整行图。
    """
    out: list[fitz.Rect] = []
    for r in sorted(boxes, key=lambda b: (b.y0, b.x0)):
        r = fitz.Rect(r)
        for i, o in enumerate(out):
            ov = min(o.y1, r.y1) - max(o.y0, r.y0)
            h = min(o.height, r.height)
            if h > 0 and ov / h > 0.6:
                out[i] = fitz.Rect(min(o.x0, r.x0), min(o.y0, r.y0),
                                   max(o.x1, r.x1), max(o.y1, r.y1))
                break
        else:
            out.append(r)
    return out


def _align_of(rect) -> str:
    """图相对版心的对齐：贴左 / 贴右 / 居中。"""
    left = float(rect.x0) - PAGE_X0
    right = PAGE_X1 - float(rect.x1)
    if left <= 2.0 <= right:
        return "left"
    if right <= 2.0 <= left:
        return "right"
    return "center"


def rich_blocks(page, clip, save_fig=None, skip_sigs: set | None = None) -> list[dict]:
    """一个矩形 → 有序块 [{t:p,text} | {t:fig,rect}]（文字段落 + 图块，按纵坐标交错）。"""
    try:
        d = page.get_text("dict", clip=clip)
    except Exception:
        return []
    lines = []          # (y0, x0, x1, y1, h, html)
    for blk in d.get("blocks") or []:
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines") or []:
            html = _line_html(ln)
            if not html:
                continue
            x0, y0, x1, y1 = ln.get("bbox") or (0, 0, 0, 0)
            lines.append((round(y0, 1), x0, x1, round(y1, 1), y1 - y0, html))
    lines.sort(key=lambda t: (t[0], t[1]))

    def _inside(rects, y0, y1) -> bool:
        cy = (y0 + y1) / 2.0
        return any(r.y0 - 1 <= cy <= r.y1 + 1 for r in rects)

    # 表格：先找出来，落在表区域里的文字行不再当普通段落。
    # 每个单元格用原始 words 按坐标重拼（extract() 会把「0.5」拆成「05\n.」）；
    # 重拼后仍有残格，就整块按 300dpi 裁成 figure，绝不导出「1466.」这种断数字。
    tbl_blocks: list[dict] = []
    tbl_rects: list = []
    bad_tables: list = []
    try:
        for tb in page.find_tables(clip=clip).tables:
            r = fitz.Rect(tb.bbox)
            if r.x0 < clip.x0 - 8 or r.y0 < clip.y0 - 8 or r.y1 > clip.y1 + 8:
                continue
            grid: list[list[str]] = []
            for row in getattr(tb, "rows", []) or []:
                cells = [_cell_words(page, c) for c in (row.cells or [])]
                if any(c for c in cells):
                    grid.append(cells)
            if len(grid) < 2:
                continue
            width = max(len(g) for g in grid)
            grid = [(g + [""] * width)[:width] for g in grid]
            if any(RESIDUAL.match(c) for row in grid for c in row if c):
                bad_tables.append(r)
                continue
            tbl_rects.append(r)
            tbl_blocks.append({"t": "table", "head": grid[0], "rows": grid[1:], "y": r.y0})
    except Exception:
        tbl_blocks, tbl_rects, bad_tables = [], [], []
    if tbl_rects:
        lines = [ln for ln in lines if not _inside(tbl_rects, ln[0], ln[3])]

    # 图块：注意 fitz 的 Rect.intersect()/include_point() 是**原地修改**，
    # 老代码里 r.intersect(t) 把图矩形改成了交集，导致结构式全被吞掉 —— 用 & 代替。
    figs = []
    for r in figure_rects(page, clip, _text_rects(page, clip), skip_sigs):
        if any((r & t).get_area() > 0.35 * r.get_area() for t in tbl_rects):
            continue
        figs.append(r)
    fig_rects: list[fitz.Rect] = []
    absorbed: set[int] = set()
    for r in _merge_rows(figs):
        box = fitz.Rect(r)
        for i, (ly0, lx0, lx1, ly1, _lh, _html) in enumerate(lines):
            # 结构式下面的小标签（Et₄TDS / 2-MBI）紧贴图，短且横向落在图内：
            # 一起裁进图里，否则它们会变成散落的正文段落。
            if (r.y1 - 2 <= ly0 <= r.y1 + 24 and r.x0 - 4 <= (lx0 + lx1) / 2.0 <= r.x1 + 4
                    and (lx1 - lx0) <= clip.width * 0.5):
                box.x0 = min(box.x0, lx0)
                box.x1 = max(box.x1, lx1)
                box.y1 = max(box.y1, ly1 + 1)
                absorbed.add(i)
        fig_rects.append(fitz.Rect(box) & clip)
    if absorbed:
        lines = [ln for i, ln in enumerate(lines) if i not in absorbed]

    blocks: list[dict] = []
    paras, cur, last_y, last_h = [], [], None, 0.0
    y_top = None
    def flush():
        nonlocal cur, y_top
        txt = "".join(cur).strip()
        if txt:
            prev = blocks[-1] if blocks else None
            is_num = bool(re.match(r"^(第\s*\d+\s*题|\d+\s*[-–]\s*\d+|\d+\s*[.．、]\s|\(?（\d+）)",
                                   re.sub(r"<[^>]+>", "", txt)))
            prev_txt = str((prev or {}).get("text") or "")
            prev_is_head = bool(re.match(r"^(第\s*\d+\s*题|\d+\s*[-–]\s*\d+\s)",
                                         re.sub(r"<[^>]+>", "", prev_txt)))
            if (prev and prev.get("t") == "p" and not is_num and not prev_is_head
                    and not re.search(r"[。！？!?；;]$", prev_txt.rstrip())):
                # 上一段没结句 → 被公式/上下标行切断的，接回去
                join = "" if re.search(r"<[^>]+>$", str(prev.get("text") or "")) else ""
                prev["text"] = str(prev.get("text") or "") + join + txt
            else:
                blocks.append({"t": "p", "text": txt, "y": y_top})
        cur, y_top = [], None
    for y, _x, _x1, _y1, h, html in lines:
        gap = (y - last_y) if last_y is not None else 0
        plain = re.sub(r"<[^>]+>", "", html)
        starts_new = bool(re.match(r"^(第\s*\d+\s*题|\d+\s*[-–]\s*\d+|\d+\s*[.．、]\s)", plain))
        if cur and (gap > max(6.0, last_h * 1.35) or starts_new):
            flush()
        if y_top is None:
            y_top = y
        if cur and re.search(r"[A-Za-z0-9]$", cur[-1]) and re.match(r"[A-Za-z0-9]", html):
            cur.append(" ")
        cur.append(html)
        if re.match(r"^第\s*\d+\s*题", plain):
            flush()          # 「第N题 标题（X分，占Y%）」单独成段，正文另起
            y_top = None
        last_y = y
        last_h = max(last_h, h)
    flush()

    for r in fig_rects:
        blocks.append({"t": "fig", "rect": r, "y": r.y0})
    for r in bad_tables:
        blocks.append({"t": "fig", "rect": r, "y": r.y0, "table": True})
    blocks.extend(tbl_blocks)

    blocks.sort(key=lambda b: (b.get("y") or 0))
    out = []
    for b in blocks:
        if b["t"] == "p":
            if b.get("text"):
                out.append({"t": "p", "text": b["text"]})
        elif b["t"] == "table":
            out.append({"t": "table", "head": b.get("head") or [], "rows": b.get("rows") or []})
        elif b["t"] == "fig" and save_fig is not None:
            try:
                blk = save_fig(b["rect"], TABLE_DPI if b.get("table") else DPI)
            except TypeError:
                blk = save_fig(b["rect"])
            if blk:
                out.append(blk)
    return out


def _watermark_sigs(doc) -> set:
    """同一尺寸在多页重复出现的位图 = 水印/底纹，导出文字时排除。"""
    from collections import Counter
    c: Counter = Counter()
    n = 0
    for pi in range(min(12, doc.page_count)):
        n += 1
        try:
            for info in doc[pi].get_image_info():
                c[(info.get("width"), info.get("height"))] += 1
        except Exception:
            continue
    return {k for k, v in c.items() if v >= max(3, int(n * 0.5))}


def cut(pdf_path: str, out_dir: Path, prefix: str) -> list[dict]:
    """按题号切图，返回 [{no, title, path, width, height}]（path 相对 out_dir）。"""
    doc = fitz.open(pdf_path)
    skip_sigs = _watermark_sigs(doc)
    marks = _marks(doc)
    order = sorted(marks)
    out_dir.mkdir(parents=True, exist_ok=True)
    result: list[dict] = []

    for idx, no in enumerate(order):
        p0, y0, title = marks[no]
        if idx + 1 < len(order):
            p1, y1, _ = marks[order[idx + 1]]
        else:
            p1, y1 = doc.page_count - 1, doc[doc.page_count - 1].rect.y1

        files: list[str] = []
        chunks: list[str] = []
        paras: list[str] = []
        blocks: list[dict] = []
        for pi in range(p0, min(p1, p0 + MAX_SPAN) + 1):
            page = doc[pi]
            top = (y0 - 14) if pi == p0 else page.rect.y0 + 20
            bot = (y1 - 4) if pi == p1 else page.rect.y1 - 20
            # 页眉/页脚带不进正文（§2：正文区 100.2–772.9），否则「4/220」会变成一段正文
            top = max(BODY_TOP, top)
            bot = min(BODY_BOTTOM, bot)
            if bot - top < MIN_H:
                continue
            clip = fitz.Rect(page.rect.x0 + 30, top, page.rect.x1 - 30, bot)
            pix = page.get_pixmap(clip=clip, dpi=DPI)
            im = Image.open(io.BytesIO(pix.tobytes("png")))
            im = _trim(im)
            name = f"{prefix}{no:02d}_{pi - p0 + 1}.png"
            im.save(out_dir / name, "PNG", optimize=True)
            files.append(name)
            try:
                chunks.append(page.get_text(clip=clip))
            except Exception:
                pass
            try:
                paras += rich_paras(page, clip)
            except Exception:
                pass
            try:
                def _save_fig(r, dpi=DPI, _page=page, _pi=pi, _p0=p0):
                    pix = _page.get_pixmap(clip=r, dpi=dpi)
                    im = Image.open(io.BytesIO(pix.tobytes("png")))
                    im, box = _trim_with_box(im)
                    scale = float(dpi) / 72.0
                    fn = f"{prefix}{no:02d}_f{_pi - _p0 + 1}_{len(files)}.png"
                    im.save(out_dir / fn, "PNG", optimize=True)
                    rel = str((out_dir / fn).relative_to(out_dir.parent.parent))
                    # 几何在导入时定死：渲染器不再按像素÷150dpi 反推（§1/§4）
                    return {
                        "t": "figure",
                        "path": rel,
                        "w": round((box[2] - box[0]) / scale, 1),
                        "h": round((box[3] - box[1]) / scale, 1),
                        "align": _align_of(r),
                        "caption": "",
                        "source": {
                            "file": Path(pdf_path).name,
                            "page": _pi + 1,
                            "y0": round(float(r.y0), 1),
                            "y1": round(float(r.y1), 1),
                        },
                    }
                got = rich_blocks(page, clip, _save_fig, skip_sigs)
                if got:
                    blocks += got
                elif paras:
                    blocks += [{"t": "p", "text": t} for t in rich_paras(page, clip)]
            except Exception:
                blocks += [{"t": "p", "text": t} for t in paras[-20:]]

        if not files:
            continue
        # 首段通常以「第 N 题 标题（X 分，占Y%）」开头，这部分已经进 title，剔掉避免重复
        if blocks and blocks[0].get("t") == "p":
            t0 = re.sub(r"^第\s*\d+\s*题\s*[^（(]{0,40}[（(][^）)]{0,24}[）)]", "", blocks[0]["text"]).strip()
            if t0:
                blocks[0]["text"] = t0
            else:
                blocks.pop(0)
        if paras:
            first = re.sub(r"^第\s*\d+\s*题\s*[^（(]{0,40}[（(][^）)]{0,24}[）)]", "", paras[0]).strip()
            if first:
                paras[0] = first
            else:
                paras.pop(0)
        result.append({
            "no": no,
            "title": title,
            "text": chr(10).join(chunks)[:4000],
            "paras": paras[:200],
            "blocks": blocks[:400],
            "files": files,
            "path": str((out_dir / files[0]).relative_to(out_dir.parent.parent)),
        })

    doc.close()
    return result


def make_job_dir(library_dir: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    d = library_dir / "split" / stamp
    d.mkdir(parents=True, exist_ok=True)
    return d
