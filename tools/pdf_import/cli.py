# -*- coding: utf-8 -*-
"""PDF → 结构化题面 doc JSON（独立命令行，主程序只通过 subprocess 调它）。

为什么单独一个工具：
- PyMuPDF / pymupdf4llm 是 AGPL，主程序不 import 它们，隔离后换库/换许可证都不返工；
- 解析规则（页眉页脚、题号、跨页、图区、颜色分流）需要反复调，独立进程好单测。

用法：
    python tools/pdf_import/cli.py --pdf 卷.pdf [--answer 参考答案.pdf] \
        --img-dir out/img --img-prefix import/<doc_id> --out out.json

产物：
    {"page_count": 6, "items": [ {doc, ...} ], "stats": {...}}
每个 item 就是 questions.layout 里那份 doc（kind = "text"）。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys

import pymupdf as fitz

RE_ITEM = re.compile(r"^第\s*(\d+)\s*题\s*(.*?)（\s*(\d+)\s*分\s*[，,]\s*占\s*(\d+)\s*%\s*）")
RE_ITEM_CN = re.compile(r"^第\s*([一二三四五六七八九十]+)\s*题\s*(.*?)（\s*(\d+)\s*分\s*[，,]\s*占\s*(\d+)\s*%\s*）")
RE_SUB = re.compile(r"^(\d+)-(\d+(?:-\d+)*)\s*(.*)$")
RE_ANS_HEAD = re.compile(r"^(\d+(?:-\d+)*)\s*共\s*(\d+)\s*分$")
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


# --------------------------------------------------------------------- 去水印
def _is_light_overlay(raw):
    """水印图层的特征：几乎全白、灰阶。"""
    try:
        from PIL import Image
        import numpy as np
        im = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return False
    a = np.asarray(im, dtype=np.float32)
    if a.size == 0:
        return False
    l = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
    sat = a.max(axis=2) - a.min(axis=2)
    return float(l.mean()) >= 220 and float(sat.mean()) <= 12


def strip_watermarks(page, doc):
    """删掉占页 ≥30% 的浅色大图层（整页底纹/水印）。返回删除个数。"""
    w, h = page.rect.width, page.rect.height
    if w <= 0 or h <= 0:
        return 0
    killed, seen = 0, set()
    for img in page.get_images(full=True):
        xref = img[0]
        if xref in seen:
            continue
        seen.add(xref)
        rects = page.get_image_rects(xref)
        if not any(((r.width * r.height) / (w * h)) >= 0.30 for r in rects):
            continue
        try:
            raw = doc.extract_image(xref)["image"]
        except Exception:
            continue
        if not _is_light_overlay(raw):
            continue
        try:
            page.delete_image(xref)
            killed += 1
        except Exception:
            pass
    return killed


def strip_doc(doc):
    n = 0
    for i in range(len(doc)):
        try:
            n += strip_watermarks(doc.load_page(i), doc)
        except Exception:
            continue
    return n


# --------------------------------------------------------------------- 取行
# PDF 里的私有区/替换字符：Symbol、Dingbats 字体被 pymupdf 直接吐成 PUA，
# 排进卡片就是一堆方块和乱码，这里一律清掉。
RE_JUNK = re.compile("[-�​­]")


def clean_text(t):
    t = RE_JUNK.sub("", str(t or ""))
    return re.sub(r"[ 	]{2,}", " ", t).strip()


# Adobe Symbol 字体的私有序：PDF 里存的是 ASCII 码，字形却是希腊字母/数学符号。
# 不做映射的话，`≠` 会变成 `e`、`−` 会变成 `-`、`α` 会变成 `a`（看起来像乱码字母）。
SYMBOL_MAP = str.maketrans({
    "!": "!", '"': "∀", "#": "#", "$": "∃", "%": "%", "&": "&", "'": "∋",
    "(": "(", ")": ")", "*": "∗", "+": "+", ",": ",", "-": "−", ".": ".",
    "/": "/", ":": ":", ";": ";", "<": "<", "=": "=", ">": ">", "?": "?",
    "@": "≅",
    "A": "Α", "B": "Β", "C": "Χ", "D": "Δ", "E": "Ε", "F": "Φ", "G": "Γ",
    "H": "Η", "I": "Ι", "J": "ϑ", "K": "Κ", "L": "Λ", "M": "Μ", "N": "Ν",
    "O": "Ο", "P": "Π", "Q": "Θ", "R": "Ρ", "S": "Σ", "T": "Τ", "U": "Υ",
    "V": "ς", "W": "Ω", "X": "Ξ", "Y": "Ψ", "Z": "Ζ",
    "[": "[", "\\": "∴", "]": "]", "^": "⊥", "_": "_", "`": "‾",
    "a": "α", "b": "β", "c": "χ", "d": "δ", "e": "ε", "f": "φ", "g": "γ",
    "h": "η", "i": "ι", "j": "ϕ", "k": "κ", "l": "λ", "m": "μ", "n": "ν",
    "o": "ο", "p": "π", "q": "θ", "r": "ρ", "s": "σ", "t": "τ", "u": "υ",
    "v": "ϖ", "w": "ω", "x": "ξ", "y": "ψ", "z": "ζ",
    "{": "{", "|": "|", "}": "}", "~": "∼",
})


def symbol_fix(text, font):
    """Symbol 字体按 Adobe 编码表还原；其它字体原样返回。"""
    f = str(font or "").lower()
    if "symbol" in f:
        return text.translate(SYMBOL_MAP)
    return text


def line_text(spans):
    """把一行 span 拼起来，顺便把「小一号的字」还原成 _{...} 上下标。

    样卷里 X₁ / BaCl₂ / SO₄²⁻ 的角标是独立 span、字号 6.85（正文 10.45），
    所以按字号差就能判断，比猜「字母后面的数字」准得多。
    """
    base = max(float(s.get("size") or 0) for s in spans)
    out = ""
    for sp in spans:
        t = symbol_fix(str(sp.get("text") or ""), sp.get("font"))
        if not t:
            continue
        small = float(sp.get("size") or 0) < base * 0.85
        core = t.strip()
        if small and core and re.fullmatch(r"[0-9]+", core):
            lead = " " if t[:1].isspace() else ""
            tail = " " if t[-1:].isspace() else ""
            out += f"{lead}_{{{core}}}{tail}"
        elif small and core and re.fullmatch(r"[0-9]*[+\-]+", core):
            out += f"^{{{core}}}"
        else:
            out += t
    return re.sub(r"[ 	]{2,}", " ", out).strip()


def page_lines(page):
    """一行一条，带字号/粗体/颜色，按阅读顺序（y 后 x）。"""
    out = []
    d = page.get_text("dict")
    for blk in d.get("blocks") or []:
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines") or []:
            spans = [s for s in ln.get("spans") or [] if (s.get("text") or "").strip()]
            if not spans:
                continue
            raw = line_text(spans)
            bad = "\ufffd" in raw      # 有不可还原的字形（Cambria Math 之类）
            text = clean_text(raw)
            if not text and not bad:
                continue
            size = max(round(float(s.get("size") or 0), 1) for s in spans)
            bold = any("Bold" in str(s.get("font") or "") for s in spans)
            colors = {int(s.get("color") or 0) for s in spans}
            b = ln["bbox"]
            out.append({"text": text, "size": size, "bold": bold, "bad": bad,
                        "color": min(colors), "bbox": [round(x, 1) for x in b]})
    out.sort(key=lambda r: (round(r["bbox"][1], 1), r["bbox"][0]))
    return merge_same_row(out)


def merge_same_row(rows, tol=3.0, gapmax=60.0):
    """同一水平行被切成多段（编号与正文分属不同字体）时按 x 顺序接回去。

    只在水平间隙 < gapmax 时合并，避免把双栏版面糊成一行。
    """
    out = []
    for r in rows:
        if out and abs(r["bbox"][1] - out[-1]["bbox"][1]) <= tol:
            prev = out[-1]
            gap = r["bbox"][0] - prev["bbox"][2]
            if gap <= gapmax:
                prev["text"] = (prev["text"] + (" " if gap > 2 else "") + r["text"]).strip()
                prev["bbox"] = [prev["bbox"][0], min(prev["bbox"][1], r["bbox"][1]),
                                max(prev["bbox"][2], r["bbox"][2]), max(prev["bbox"][3], r["bbox"][3])]
                prev["size"] = max(prev["size"], r["size"])
                prev["bad"] = bool(prev.get("bad") or r.get("bad"))
                # 合并后保留「有颜色」的那个：编号是黑字、答案正文是红/蓝，取黑的就全丢了
                if (prev["color"] & 0xFFFFFF) <= 0x10 < (r["color"] & 0xFFFFFF):
                    prev["color"] = r["color"]
                prev["bold"] = prev["bold"] or r["bold"]
                continue
        out.append(dict(r))
    return out


def read_pages(pdf):
    doc = fitz.open(pdf)
    pages = [page_lines(doc.load_page(i)) for i in range(len(doc))]
    return doc, pages


def strip_running(pages):
    """出现在 ≥60% 页面上的首行/末行 —— 判为页眉页脚删掉。"""
    n = len(pages)
    if n < 3:
        return pages
    heads, tails = {}, {}
    for pg in pages:
        if not pg:
            continue
        heads[pg[0]["text"]] = heads.get(pg[0]["text"], 0) + 1
        tails[pg[-1]["text"]] = tails.get(pg[-1]["text"], 0) + 1
    bad_head = {t for t, c in heads.items() if c >= 0.6 * n}
    bad_tail = {t for t, c in tails.items() if c >= 0.6 * n}
    out = []
    for pg in pages:
        rows = list(pg)
        if rows and rows[0]["text"] in bad_head:
            rows = rows[1:]
        if rows and rows[-1]["text"] in bad_tail:
            rows = rows[:-1]
        out.append(rows)
    return out


# --------------------------------------------------------------------- 切题
def cn2int(s):
    if s.isdigit():
        return int(s)
    if s in CN_NUM:
        return CN_NUM[s]
    if s.startswith("十"):
        return 10 + CN_NUM.get(s[1:], 0)
    if "十" in s:
        a, _, b = s.partition("十")
        return CN_NUM.get(a, 1) * 10 + (CN_NUM.get(b, 0) if b else 0)
    return 0


def deep_find(subs, no):
    for s in subs:
        if s.get("no") == no:
            return s
        r = deep_find(s.get("subs") or [], no)
        if r:
            return r
    return None


def leaves(subs, out=None):
    out = [] if out is None else out
    for s in subs:
        if s.get("subs"):
            leaves(s["subs"], out)
        else:
            out.append(s)
    return out


def parse_items(pages, body_size):
    items, cur, sub = [], None, None
    for pno, rows in enumerate(pages, 1):
        for ln in rows:
            txt = ln["text"]
            m = RE_ITEM.match(txt) or RE_ITEM_CN.match(txt)
            if m:
                num = m.group(1)
                num = int(num) if num.isdigit() else cn2int(num)
                cur = {"number": num, "title": m.group(2).strip(), "score": int(m.group(3)),
                       "percent": int(m.group(4)), "intro": "", "subs": [],
                       "page": pno, "y": ln["bbox"][1]}
                items.append(cur)
                sub = None
                continue
            if cur is None:
                continue
            m2 = RE_SUB.match(txt)
            if m2 and int(m2.group(1)) == cur["number"]:
                no = f"{m2.group(1)}-{m2.group(2)}"
                node = {"no": no, "text": m2.group(3).strip(), "score": 0,
                        "answer": "", "points": [], "imgs": [], "answer_images": [], "subs": []}
                parts = no.split("-")
                parent = None
                for cut in range(len(parts) - 1, 1, -1):
                    parent = deep_find(cur["subs"], "-".join(parts[:cut]))
                    if parent:
                        break
                (parent["subs"] if parent else cur["subs"]).append(node)
                sub = node
                continue
            # 续行：上一块不是终止符就拼上
            target = sub if sub is not None else cur
            key = "text" if sub is not None else "intro"
            prev = target.get(key) or ""
            if prev and prev[-1] in "。？！：）」”'":
                target[key] = prev + txt
            elif prev:
                target[key] = prev + txt
            else:
                target[key] = txt
    return items


# --------------------------------------------------------------------- 答案卷
RE_POINT = re.compile(r"\d+\s*分|每\s*\S{0,3}\s*\d*\s*分|其余|共\s*\d+\s*分")


def is_point_line(text):
    """给分说明的形态：短句 + 含「N 分」，且不是方程式/结构式。

    「19683 个（2 分）」这种是答案本身带着分值，不算给分说明 —— 只有整句都在讲
    给分（每个/其余/共/答出/得分）时才归到 points，否则宁可当答案，版面上更可读。
    """
    t = (text or "").strip()
    if not t or len(t) > 42:
        return False
    if any(ch in t for ch in "=→⇌"):
        return False
    if not RE_POINT.search(t):
        return False
    return any(k in t for k in ("每个", "其余", "共", "答出", "得分", "扣", "各"))


def color_answers(pages, pic_doc=None, dpi=300, img_dir=None, img_prefix=""):
    """答案卷分流：颜色分不出时按内容判；页面上的图按位置归属到最近的小问。

    返回 {no: {answer:[], points:[], total:int, answer_images:[{path,caption,source}]}}
    """
    ans = {}
    cur = None
    for pno, rows in enumerate(pages, 1):
        events = [("line", r["bbox"][1], r) for r in rows]
        if pic_doc is not None and img_dir:
            page_obj = pic_doc.load_page(pno - 1)
            H = page_obj.rect.height
            for rect in page_pictures(page_obj, dpi):
                events.append(("pic", rect[1] * H, rect))
        events.sort(key=lambda e: e[1])
        page_obj = pic_doc.load_page(pno - 1) if pic_doc is not None else None
        for kind, _y, payload in events:
            if kind == "pic":
                if cur and cur in ans and page_obj is not None:
                    name = f"a{pno}-{len(ans[cur]['answer_images']) + 1}.png"
                    saved = save_picture(page_obj, payload, img_dir, name, dpi)
                    ans[cur]["answer_images"].append({
                        "path": f"{img_prefix.rstrip('/')}/{name}" if img_prefix else name,
                        "caption": "",
                        "w": round(min(415.0, max(40.0, float(saved.get("w_pt") or 415))), 1),
                        "source": {"page": pno, "kind": "figure",
                                   "rect": [round(x, 4) for x in payload], "dpi": dpi},
                    })
                continue
            ln = payload
            m = RE_ANS_HEAD.match(ln["text"])
            if m:
                cur = m.group(1)
                ans.setdefault(cur, {"answer": [], "points": [], "total": int(m.group(2)),
                                     "answer_images": [], "_page": pno, "_rows": []})
                continue
            if RE_SUB.match(ln["text"]):
                cur = None
                continue
            if cur is None:
                continue
            c = ln["color"] & 0xFFFFFF
            if c <= 0x10:                       # 黑字 = 抄回来的题干，丢掉
                continue
            st = ans[cur]
            st["_rows"].append(ln)
            if ln.get("bad") or "\ufffd" in ln["text"]:
                st["_bad"] = True
            # 颜色不能可靠区分「答案」和「给分说明」（样卷里方程式是蓝的、结论句是红的），
            # 所以按内容判：短句 + 带「分」= 给分说明，其余 = 答案。
            if is_point_line(ln["text"]):
                ans[cur]["points"].append(ln["text"])
            else:
                ans[cur]["answer"].append(ln["text"])

    # 有些卷子用 Cambria Math / MathType 排公式，PDF 里没有 ToUnicode，
    # 抽出来是一堆 U+FFFD 和假字母（`= e`、`−0`）。这种区块不硬猜，
    # 直接按原样裁成一张图，答案区显示图 —— 至少和卷面一致，不会给人看乱码。
    for no, st in ans.items():
        rows = st.pop("_rows", [])
        pno = st.pop("_page", 1)
        bad = st.pop("_bad", False)
        if not (bad and rows and pic_doc is not None and img_dir):
            continue
        x0 = min(r["bbox"][0] for r in rows) - 2
        y0 = min(r["bbox"][1] for r in rows) - 2
        x1 = max(r["bbox"][2] for r in rows) + 2
        y1 = max(r["bbox"][3] for r in rows) + 2
        try:
            page_obj = pic_doc.load_page(pno - 1)
            W, H = page_obj.rect.width, page_obj.rect.height
            rect = [max(0.0, x0 / W), max(0.0, y0 / H), min(1.0, x1 / W), min(1.0, y1 / H)]
            name = f"f{pno}-{no.replace('-', '_')}.png"
            saved = save_picture(page_obj, rect, img_dir, name, dpi)
        except Exception:
            continue
        st["answer"] = []
        st["answer_images"].insert(0, {
            "path": f"{img_prefix.rstrip('/')}/{name}" if img_prefix else name,
            "caption": "",
            "w": round(min(415.0, max(60.0, float(saved.get("w_pt") or 415))), 1),
            "source": {"page": pno, "kind": "block",
                       "rect": [round(x, 4) for x in rect], "dpi": dpi},
        })
    return ans


# --------------------------------------------------------------------- 图区
def page_pictures(page, dpi):
    """返回本页真实插图（跳过面积 > 35% 的水印/底纹）。"""
    W, H = page.rect.width, page.rect.height
    out = []
    for info in page.get_image_info():
        b = info["bbox"]
        if ((b[2] - b[0]) * (b[3] - b[1])) / (W * H) > 0.35:
            continue
        out.append([b[0] / W, b[1] / H, b[2] / W, b[3] / H])
    return out


def save_picture(page, rect, out_dir, name, dpi):
    import pymupdf as fz
    W, H = page.rect.width, page.rect.height
    clip = fz.Rect(rect[0] * W, rect[1] * H, rect[2] * W, rect[3] * H)
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fz.Matrix(zoom, zoom), clip=clip, alpha=False)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    pix.save(path)
    # 去白边：PDF 里图区的 bbox 常比实际内容宽一圈，不裁的话版面宽度会虚大
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        gray = im.convert("L").point(lambda v: 255 if v > 246 else 0)
        box = gray.getbbox()
        if box:
            pad = max(2, int(round(6 * dpi / 72.0)))
            box = (max(0, box[0] - pad), max(0, box[1] - pad),
                   min(im.width, box[2] + pad), min(im.height, box[3] + pad))
            if box[2] - box[0] < im.width or box[3] - box[1] < im.height:
                im.crop(box).save(path)
                im = Image.open(path)
    except Exception:
        im = None
    w_px, h_px = (im.width, im.height) if im is not None else (pix.width, pix.height)
    return {"path": path, "width": w_px, "height": h_px,
            "w_pt": round(w_px * 72.0 / dpi, 1), "h_pt": round(h_px * 72.0 / dpi, 1)}


# --------------------------------------------------------------------- 组装
def build_docs(pdf, answer_pdf=None, dpi=300, img_dir=None, img_prefix="", clean=True):
    doc, pages = read_pages(pdf)
    body_size = 10.45
    sizes = [ln["size"] for pg in pages for ln in pg]
    if sizes:
        from collections import Counter
        body_size = Counter(sizes).most_common(1)[0][0]
    pages = strip_running(pages)
    # 渲染之前把整页底纹图层删掉：裁出来的图、页码图都不再带水印
    if clean:
        strip_doc(doc)
    items = parse_items(pages, body_size)

    ans = {}
    if answer_pdf:
        adoc, apages = read_pages(answer_pdf)
        if clean:
            strip_doc(adoc)
        ans = color_answers(strip_running(apages), pic_doc=adoc, dpi=dpi,
                            img_dir=img_dir, img_prefix=img_prefix)

    pics = {i + 1: page_pictures(doc.load_page(i), dpi) for i in range(len(doc))}
    raw_pics = {i + 1: len(doc.load_page(i).get_image_info()) for i in range(len(doc))}
    heads = [(it["page"], it["y"], it) for it in items]

    stats = {"items": len(items), "subs": 0, "leaves": 0, "score": sum(it["score"] for it in items),
             "percent": [it["percent"] for it in items], "images": 0,
             "pages": len(doc), "page_images": {k: len(v) for k, v in pics.items()}}
    out = []
    # 图归谁：按「图上方最近的那一行属于哪个小问」判，
    # 不能只按大题落到 stem —— 否则同一页里第二张图（属于 7-2 的数据）会跑到题首，
    # 看起来就像把答案裁进了题目。
    anchors = {}
    for pno, pg in enumerate(pages, 1):
        cur_item, cur_sub, seq = None, None, []
        for ln in pg:
            t = ln["text"]
            mi = RE_ITEM.match(t) or RE_ITEM_CN.match(t)
            if mi:
                cur_item, cur_sub = int(mi.group(1)), None
            else:
                ms = RE_SUB.match(t)
                if ms:
                    cur_item, cur_sub = int(ms.group(1)), ms.group(0).split()[0].strip()
            seq.append((ln["bbox"][1], cur_item, cur_sub))
        anchors[pno] = seq

    def owner_of(pno, y):
        best = (None, None)
        for ay, ai, asub in anchors.get(pno, []):
            if ay <= y:
                best = (ai, asub)
            else:
                break
        return best

    for it in items:
        it["imgs"] = []
        for pno, arr in pics.items():
            for k, rect in enumerate(arr, 1):
                y_img = rect[1] * 841.9
                ai, asub = owner_of(pno, y_img)
                if ai != it["number"]:
                    continue
                name = f"p{pno}-{k}.png"
                saved = save_picture(doc.load_page(pno - 1), rect, img_dir, name, dpi)
                rel = f"{img_prefix.rstrip('/')}/{name}" if img_prefix else saved["path"]
                bw = min(415.0, max(40.0, float(saved.get("w_pt") or 415)))
                fig = {"path": rel, "caption": "", "w": round(bw, 1),
                       "source": {"page": pno, "kind": "figure",
                                  "rect": [round(x, 4) for x in rect], "dpi": dpi}}
                node = deep_find(it["subs"], asub) if asub else None
                if node is not None:
                    node.setdefault("imgs", []).append(fig)
                else:
                    it["imgs"].append(fig)
                stats["images"] += 1

        # 分值：父题按叶子均分，再自底向上把父级补成子问之和（保证 子和 = 父）
        # 答案与分值：先按答案卷的「共 N 分」落到各小问，再自底向上补父级
        for s in walk_subs(it["subs"]):
            a = ans.get(s["no"])
            if not a:
                continue
            if a.get("total"):
                s["score"] = int(a["total"])
            s["answer"] = chr(10).join(a["answer"]).strip()
            s["points"] = a["points"]
            if a.get("answer_images"):
                s["answer_images"] = a["answer_images"]
        lv = leaves(it["subs"])
        if lv:
            if not any(int(x.get("score") or 0) > 0 for x in lv):
                base, rem = divmod(it["score"], len(lv))
                for k, s in enumerate(lv):
                    s["score"] = base + (1 if k < rem else 0)
            fix_parent_scores(it["subs"])        # 父级一律 = 子问之和
        stats["subs"] += len([x for x in walk_subs(it["subs"])])
        stats["leaves"] += len(leaves(it["subs"]))

        stem = []
        if it["intro"].strip():
            stem.append({"t": "p", "text": it["intro"].strip()})
        stem.extend({"t": "img", "path": im["path"], "caption": im["caption"], "w": im["w"]}
                    for im in it["imgs"])
        out.append({
            "v": 1, "kind": "text",
            "meta": {"title": it["title"], "number": it["number"],
                     "score": it["score"], "percent": it["percent"], "numbering": "dash"},
            "stem": stem,
            "subs": [clean_sub(s) for s in it["subs"]],
            "source": {"page": it["page"], "locator": f"第 {it['number']} 题", "origin": "pdf"},
        })
    stats["page_images_p5"] = stats["page_images"].get(5)
    stats["page_images_raw"] = raw_pics
    stats["page_images_raw_p5"] = raw_pics.get(5)
    stats["doc_id"] = hashlib.sha1(open(pdf, "rb").read()).hexdigest()[:12]
    return {"page_count": len(doc), "items": out, "stats": stats}


def fix_parent_scores(subs):
    for s in subs or []:
        if s.get("subs"):
            fix_parent_scores(s["subs"])
            s["score"] = sum(int(x.get("score") or 0) for x in s["subs"])


def walk_subs(subs):
    for s in subs:
        yield s
        yield from walk_subs(s.get("subs") or [])


def clean_sub(s):
    return {"no": s["no"], "text": s["text"], "score": s["score"],
            "answer": s["answer"], "points": s["points"],
            "imgs": list(s.get("imgs") or []),
            "answer_images": list(s.get("answer_images") or []),
            "subs": [clean_sub(c) for c in s.get("subs") or []]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="PDF → 结构化题面 doc JSON")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--answer", default=None)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--img-dir", default=None)
    ap.add_argument("--img-prefix", default="")
    ap.add_argument("--out", default=None)
    ap.add_argument("--keep-watermark", action="store_true",
                    help="保留整页底纹/水印图层（默认会删掉）")
    a = ap.parse_args(argv)
    data = build_docs(a.pdf, a.answer, a.dpi, a.img_dir, a.img_prefix,
                      clean=not a.keep_watermark)
    text = json.dumps(data, ensure_ascii=False, indent=1)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
        open(a.out, "w", encoding="utf-8").write(text)
        print(json.dumps(data["stats"], ensure_ascii=False))
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
