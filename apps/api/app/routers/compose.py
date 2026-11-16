"""组卷：从本地题库勾题 → A4 实时预览 → 导出 PDF / DOCX。

预览与 PDF 走同一条渲染链（exam_render 的 HTML → Chromium 打印），所以
"预览什么样、导出就什么样"；DOCX 另走 python-docx，只保证内容与版心一致。

题库只读：这里从不写题库，勾题不产生任何数据。
"""
from __future__ import annotations

import html as html_mod
import io
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..db import SessionLocal
from ..services import chemy_client, exam_render, rich_text

router = APIRouter(prefix="/api/compose", tags=["compose"])

PX_PER_PT = 150.0 / 72.0      # 题库里的图按 150dpi 存，转 pt 要除以这个
MAX_FIG_W = 418.0            # 版心 424pt，图最多铺满正文宽（原来卡 300pt，表格图看不清）


class ComposeIn(BaseModel):
    ids: list[str] = Field(default_factory=list)
    title: str = "化学竞赛练习卷"
    subtitle: str = ""
    school: str = ""
    reviewer: str = ""
    footer: str = ""                  # 支持 {page} / {pages}
    answers: bool = False             # 预览时同时显示答案
    scores: bool = True               # 题号后面跟分值
    part: str = "questions"           # questions | answers | all
    opts: dict[str, Any] = {}         # 导出选项（字体/行距/页边距/答案位置…），空则取设置


def _bank_text(raw: str) -> str:
    """题库的 HTML 题干 → 纯文本行；\\ce{} 里的公式直接取内容。"""
    s = str(raw or "")
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|div|li|tr|h\d)>", "\n", s, flags=re.I)
    s = re.sub(r"<sub>", "", s, flags=re.I)
    s = re.sub(r"</sub>", "", s, flags=re.I)
    s = re.sub(r"<sup>", "", s, flags=re.I)
    s = re.sub(r"</sup>", "", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s)
    s = re.sub(r"\\ce\{([^{}]*)\}", r"\1", s)
    s = s.replace("$", "")
    lines = [ln.strip() for ln in s.split("\n")]
    return "\n".join([ln for ln in lines if ln])


def _figure(img: dict) -> dict | None:
    asset = str(img.get("asset") or "").replace("\\", "/").lstrip("/")
    if not asset:
        return None
    w = float(img.get("w") or 0)
    h = float(img.get("h") or 0)
    out = {"path": f"{chemy_client.base_url().rstrip('/')}/{asset}",
           "caption": str(img.get("caption") or "")}
    if w and h:
        width = min(MAX_FIG_W, max(80.0, w / PX_PER_PT))
        out["width"] = round(width, 1)
    return out


def _rich(raw: str) -> tuple[list[dict], str]:
    """题库 HTML → (结构化块, 网页 HTML)。保住上下标/加粗/表格，软换行接成正常段落。"""
    blocks = rich_text.to_blocks(str(raw or ""))
    return blocks, rich_text.blocks_html(blocks)


def _items(questions: list[dict]) -> list[dict]:
    items = []
    for i, q in enumerate(questions, 1):
        parts = []
        for p in (q.get("parts") or []):
            blocks, htm = _rich(p.get("text"))
            parts.append({
                "stem": rich_text.blocks_text(blocks),
                "stem_html": htm,
                "score": float(p.get("points") or 0),
                "images": [], "answer": "", "points": [], "answer_images": [], "subs": [],
            })
        figs = [x for x in (_figure(f) for f in (q.get("figures") or [])) if x]
        total = sum(float(p.get("points") or 0) for p in parts) or 10.0
        ans = (q.get("answer") or {}).get("text") if isinstance(q.get("answer"), dict) else q.get("answer")
        a_blocks, a_html = _rich(ans)
        i_blocks, i_html = _rich(q.get("stem"))
        items.append({
            "number": i,
            "score": round(total, 1),
            "title": str(q.get("title") or "").strip(),
            "intro": rich_text.blocks_text(i_blocks),
            "intro_html": i_html,
            "images": figs,
            "tables": [],
            "subs": parts,
            "answer": rich_text.blocks_text(a_blocks),
            "answer_html": a_html,
            "answer_images": [],
        })
    return items


def _opts(o: "ComposeIn") -> dict:
    """导出选项：请求里带的优先，没带的取设置里存的（设置 → 导出）。"""
    try:
        from .settings import read_export_opts
        out = dict(read_export_opts(SessionLocal()))
    except Exception:  # noqa: BLE001 — 设置读不到不影响出卷
        out = {}
    out.update({k: v for k, v in (o.opts or {}).items() if v not in (None, "")})
    return out


def _footer(o: ComposeIn) -> str:
    if (o.footer or "").strip():
        return o.footer.strip()
    saved = str(_opts(o).get("footer") or "").strip()
    if saved:
        return saved
    bits = []
    if (o.school or "").strip():
        bits.append(f"学校：{o.school.strip()}")
    if (o.reviewer or "").strip():
        bits.append(f"命题人：{o.reviewer.strip()}")
    bits.append("第 {page} 页 共 {pages} 页")
    return "　　".join(bits)


def _doc(o: ComposeIn, items: list[dict]) -> dict:
    head = "　　".join(x for x in [
        (o.school or "").strip() and f"学校：{o.school.strip()}",
        (o.subtitle or "").strip(),
    ] if x)
    return {
        "title": (o.title or "").strip() or "化学竞赛练习卷",
        "subtitle": head,
        "total_score": round(sum(float(it.get("score") or 0) for it in items), 1),
        "template": {
            "header": {"text": ""},
            "footer": {"format": _footer(o)},
        },
        "items": items,
        "status": "done",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _fetch(ids: list[str]) -> list[dict]:
    out = []
    for qid in ids:
        try:
            out.append(chemy_client.question(qid))
        except Exception as exc:  # noqa: BLE001 — 单题取不到就跳过，别让整卷失败
            print(f"[compose] 跳过 {qid}: {exc}")
    if not out:
        raise HTTPException(400, "没有可用的题目：先在左边勾几道（题库要先启动）")
    return out


@router.post("/preview")
def preview(body: ComposeIn):
    if not body.ids:
        raise HTTPException(400, "先勾题")
    items = _items(_fetch(body.ids))
    doc = _doc(body, items)
    part = body.part if body.part in ("questions", "answers", "all") else "questions"
    html_doc = exam_render.build_doc_html(doc, part)
    token = exam_render.cache_put(html_doc)
    return {"url": f"/api/exam/html/{token}?paged=1", "count": len(items),
            "total_score": doc["total_score"], "title": doc["title"]}


@router.post("/export")
def export(body: ComposeIn, fmt: str = "pdf"):
    if not body.ids:
        raise HTTPException(400, "先勾题")
    items = _items(_fetch(body.ids))
    doc = _doc(body, items)
    part = body.part if body.part in ("questions", "answers", "all") else "questions"
    name = re.sub(r"[\\/:*?\"<>|]", "", doc["title"])[:40] or "试卷"
    name = quote(f"{name}-{datetime.now().strftime('%Y%m%d-%H%M')}")
    if fmt == "docx":
        try:
            blob = _docx(doc, body)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"生成 Word 失败：{exc or type(exc).__name__}") from exc
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return Response(blob, media_type=media, headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{name}.docx"})
    if fmt != "pdf":
        raise HTTPException(400, "只支持 pdf 或 docx")
    try:
        blob = exam_render.export_pdf(doc, part)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"生成 PDF 失败：{exc}") from exc
    return Response(blob, media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{name}.pdf"})


# ------------------------------------------------------------------ DOCX

def _plain_blocks(text) -> list[dict]:
    """兜底：纯文本 → blocks（每行一段）。"""
    out = []
    for ln in str(text or "").splitlines():
        ln = ln.strip()
        if ln:
            out.append({"kind": "p", "runs": [{"t": ln, "sub": False, "sup": False,
                                               "b": False, "i": False}]})
    return out


def _docx(doc: dict, o: ComposeIn) -> bytes:
    import httpx
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    def ea(run, name="宋体"):
        run.font.name = name
        rpr = run._element.get_or_add_rPr()
        rf = rpr.get_or_add_rFonts()
        rf.set(qn("w:eastAsia"), name)

    eo = _opts(o)
    try:
        fs = float(eo.get("font_size") or 10.5)
    except (TypeError, ValueError):
        fs = 10.5
    try:
        lh = float(eo.get("line_height") or 1.5)
    except (TypeError, ValueError):
        lh = 1.5
    try:
        mlr = float(eo.get("margin_lr") or 2.0)
        mtb = float(eo.get("margin_tb") or 1.8)
    except (TypeError, ValueError):
        mlr, mtb = 2.0, 1.8

    d = Document()
    normal = d.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(fs)
    normal.paragraph_format.line_spacing = lh
    try:
        rf0 = normal.element.get_or_add_rPr().get_or_add_rFonts()
        rf0.set(qn("w:eastAsia"), "宋体")
    except Exception:  # noqa: BLE001 — 样式改不了不影响正文用字
        pass

    sec = d.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(mlr)
    sec.top_margin = sec.bottom_margin = Cm(mtb)
    body_w = Cm(21.0 - 2 * mlr)

    # 页脚：自动页码（{page}/{pages}）
    fmt = _footer(o)
    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for piece in re.split(r"(\{page\}|\{pages\})", fmt):
        if piece == "{page}":
            r = fp.add_run()
            fld = OxmlElement("w:fldSimple")
            fld.set(qn("w:instr"), "PAGE")
            r._element.addnext(fld)
        elif piece == "{pages}":
            r = fp.add_run()
            fld = OxmlElement("w:fldSimple")
            fld.set(qn("w:instr"), "NUMPAGES")
            r._element.addnext(fld)
        elif piece:
            r = fp.add_run(piece)
            r.font.size = Pt(9)
            ea(r)

    def para(text="", size=10.5, bold=False, before=0, after=3, align=None, line=1.45):
        p = d.add_paragraph()
        pf = p.paragraph_format
        pf.space_before, pf.space_after, pf.line_spacing = Pt(before), Pt(after), line
        if align is not None:
            p.alignment = align
        if text:
            r = p.add_run(text)
            r.font.size, r.bold = Pt(size), bold
            ea(r)
        return p

    tp = para(doc["title"], size=17, bold=True, after=2, align=WD_ALIGN_PARAGRAPH.CENTER)
    tp.runs[0].font.name = "微软雅黑"
    ea(tp.runs[0], "微软雅黑")
    bits = [str(doc.get("subtitle") or "").strip(),
            f"共 {len(doc['items'])} 题　满分 {doc['total_score']} 分" if doc.get("total_score") else
            f"共 {len(doc['items'])} 题"]
    para("　　".join([b for b in bits if b]), size=9, after=10, align=WD_ALIGN_PARAGRAPH.CENTER)

    def add_img(path_w: dict, maxw=body_w):
        url = str(path_w.get("path") or "")
        if not url.startswith("http"):
            return
        try:
            blob = httpx.get(url, timeout=20).content
        except Exception:  # noqa: BLE001
            return
        try:
            from PIL import Image
            with Image.open(io.BytesIO(blob)) as im:
                ratio = im.width / max(1, im.height)
                # python-docx 只认 png/jpeg，题库里是 webp：先转一遍
                if im.format not in ("PNG", "JPEG"):
                    buf2 = io.BytesIO()
                    im.convert("RGB").save(buf2, "PNG")
                    blob = buf2.getvalue()
        except Exception as exc:  # noqa: BLE001
            print(f"[compose] 图片处理失败 {url}: {exc}")
            ratio = 1.6
        w = maxw
        width_pt = float(path_w.get("width") or 0)
        if width_pt:
            w = min(maxw, Pt(width_pt))
        p = d.add_paragraph()
        p.paragraph_format.space_after = Pt(4)
        p.add_run().add_picture(io.BytesIO(blob), width=w)
        cap = str(path_w.get("caption") or "").strip()
        if cap:
            cp = para(cap, size=8.5, after=6, align=WD_ALIGN_PARAGRAPH.CENTER)

    cn_no = str(eo.get("numbering") or "arabic") == "cn"
    show_scores = bool(o.scores) and bool(eo.get("scores", True))
    CN = "零一二三四五六七八九十"
    def cn_num(n: int) -> str:
        if n <= 10:
            return CN[n]
        if n < 20:
            return "十" + CN[n - 10]
        return str(n)

    for it in doc["items"]:
        no = it.get("number")
        head = (f"第{cn_num(int(no or 1))}题　" if cn_no else f"{no}. ") + (it.get("title") or "")
        if show_scores and it.get("score"):
            head += f"（{it['score']:g} 分）"
        para(head, size=fs + 0.5, bold=True, before=6, after=3)

        body = rich_text.to_blocks(it.get("intro_html") or "") or _plain_blocks(it.get("intro"))
        rich_text.docx_add(d, body, fs, line_spacing=lh)
        for im in (it.get("images") or []):
            add_img(im)

        for sub in (it.get("subs") or []):
            sblocks = rich_text.to_blocks(sub.get("stem_html") or "") or _plain_blocks(sub.get("stem"))
            if not sblocks:
                continue
            rich_text.docx_add(d, sblocks, fs, indent_cm=0.4, line_spacing=lh)

        if o.answers:
            abody = rich_text.to_blocks(it.get("answer_html") or "") or _plain_blocks(it.get("answer"))
            if abody:
                first = abody[0]
                if first["kind"] == "p" and first["runs"]:
                    first["runs"].insert(0, {"t": "【答案】", "sub": False, "sup": False,
                                             "b": True, "i": False})
                else:
                    abody.insert(0, {"kind": "p", "runs": [{"t": "【答案】", "sub": False,
                                                            "sup": False, "b": True, "i": False}]})
                rich_text.docx_add(d, abody, fs, line_spacing=lh)

    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
