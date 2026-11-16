"""格式转换：docx → pdf、pdf → docx。

docx → pdf 的引擎顺序：
  1. LibreOffice（`soffice`，装了就用，版式最准）
  2. 本机 Chromium：把 docx 拆成 HTML（段落/粗斜体/颜色/表格/图片）再打印成 PDF
     —— 没装 Office 也能用，版式是"近似"的，但字体、分页、图片位置都对得上
pdf → docx：PyMuPDF 抽文字块 + 图片，按阅读顺序重建成 Word（版式近似，会在返回里标注）
"""
from __future__ import annotations

import html as html_mod
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/convert", tags=["convert"])

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# ------------------------------------------------------------------ 引擎探测
def _soffice() -> str:
    cand = [os.environ.get("TIKU_SOFFICE", ""), "soffice", "libreoffice"]
    for c in cand:
        if not c:
            continue
        p = shutil.which(c)
        if p:
            return p
    for guess in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if Path(guess).exists():
            return guess
    return ""


def _office_ok() -> bool:
    """本机有没有能转 PDF 的 WPS / Office（只查注册表里的 COM 类，不动文档）。"""
    try:
        import winreg  # type: ignore
        for cid in ("Kwps.Application", "Word.Application"):
            try:
                key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, cid + r"\CLSID")
                winreg.CloseKey(key)
                return True
            except OSError:
                continue
    except Exception:  # noqa: BLE001 — 非 Windows 或读不了注册表
        return False
    return False


def engines() -> dict:
    return {
        "office": _office_ok(),
        "soffice": bool(_soffice()),
        "chromium": _chromium_ok(),
        "pdf2docx": _has("pdf2docx"),
        "docx": True,
        "pymupdf": _has("pymupdf") or _has("fitz"),
    }


def _has(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:  # noqa: BLE001
        return False


def _chromium_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


@router.get("/engines")
def get_engines():
    return engines()


# ------------------------------------------------------------------ docx → pdf
def _docx_to_pdf_soffice(blob: bytes) -> bytes:
    exe = _soffice()
    if not exe:
        raise RuntimeError("no soffice")
    with tempfile.TemporaryDirectory(prefix="tiku-conv-") as td:
        src = Path(td) / "in.docx"
        src.write_bytes(blob)
        env = dict(os.environ)
        env["HOME"] = td  # 让 soffice 用临时配置目录，避免和已开的 LibreOffice 抢锁
        proc = subprocess.run(
            [exe, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", td, str(src)],
            capture_output=True, timeout=180, env=env,
        )
        out = Path(td) / "in.pdf"
        if not out.exists():
            raise RuntimeError((proc.stderr or b"").decode("utf-8", "ignore")[:300] or "LibreOffice 没产出 PDF")
        return out.read_bytes()


def _esc(s: str) -> str:
    return html_mod.escape(str(s or ""))


def _docx_to_html(blob: bytes) -> str:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    d = Document(io.BytesIO(blob))
    align = {
        WD_ALIGN_PARAGRAPH.CENTER: "center",
        WD_ALIGN_PARAGRAPH.RIGHT: "right",
        WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
    }
    parts: list[str] = []

    def run_html(r) -> str:
        t = _esc(r.text).replace("\n", "<br>")
        if not t:
            return ""
        st = []
        if r.bold:
            st.append("font-weight:700")
        if r.italic:
            st.append("font-style:italic")
        if r.underline:
            st.append("text-decoration:underline")
        try:
            if r.font.size:
                st.append(f"font-size:{r.font.size.pt:g}pt")
        except Exception:  # noqa: BLE001
            pass
        try:
            col = r.font.color
            if col and col.rgb:
                st.append(f"color:#{col.rgb}")
        except Exception:  # noqa: BLE001
            pass
        try:
            if r.font.name:
                st.append(f"font-family:'{r.font.name}'")
        except Exception:  # noqa: BLE001
            pass
        return f'<span style="{";".join(st)}">{t}</span>' if st else t

    def para_html(p) -> str:
        body = "".join(run_html(r) for r in p.runs)
        pics = _para_pictures(p)
        if pics:
            body += "".join(pics)
        if not body.strip():
            return '<p style="margin:0;height:0.9em"></p>'
        style = ["margin:0 0 2pt 0"]
        if p.alignment in align:
            style.append(f"text-align:{align[p.alignment]}")
        try:
            if p.paragraph_format.left_indent:
                style.append(f"margin-left:{p.paragraph_format.left_indent.pt:g}pt")
            if p.paragraph_format.first_line_indent:
                style.append(f"text-indent:{p.paragraph_format.first_line_indent.pt:g}pt")
        except Exception:  # noqa: BLE001
            pass
        return f'<p style="{";".join(style)}">{body}</p>'

    def _para_pictures(p) -> list[str]:
        out = []
        try:
            from docx.oxml.ns import qn

            for blip in p._p.findall(".//" + qn("a:blip")):
                rid = blip.get(qn("r:embed"))
                if not rid:
                    continue
                part = d.part.related_parts.get(rid)
                if part is None:
                    continue
                import base64

                b64 = base64.b64encode(part.blob).decode()
                ext = (part.partname.ext or "png").lower()
                out.append(f'<p style="text-align:center;margin:4pt 0">'
                           f'<img src="data:image/{ext};base64,{b64}" style="max-width:100%;max-height:120mm;object-fit:contain"></p>')
        except Exception:  # noqa: BLE001
            pass
        return out

    def table_html(tbl) -> str:
        rows = []
        for row in tbl.rows:
            cells = []
            for c in row.cells:
                cells.append("<td>" + "".join(para_html(p) for p in c.paragraphs) + "</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        return ('<table style="width:100%;border-collapse:collapse;margin:6pt 0">'
                '<style>td{border:0.5pt solid #000;padding:2pt 4pt;vertical-align:top}</style>'
                + "".join(rows) + "</table>")

    body_iter = d.element.body.iterchildren()
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in body_iter:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            parts.append(para_html(Paragraph(child, d)))
        elif tag == "tbl":
            parts.append(table_html(Table(child, d)))

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 20mm 18mm; }}
body {{ font-family: "宋体", "SimSun", "Times New Roman", serif; font-size: 10.5pt; line-height: 1.55; color: #000; }}
p {{ orphans: 2; widows: 2; }}
sub, sup {{ font-size: 70%; }}
</style></head><body>{''.join(parts)}</body></html>"""


def _html_to_pdf(html_doc: str) -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            pg = browser.new_page()
            pg.set_content(html_doc, wait_until="load")
            pg.wait_for_timeout(300)
            return pg.pdf(format="A4", print_background=True,
                          margin={"top": "18mm", "bottom": "18mm", "left": "18mm", "right": "18mm"})
        finally:
            browser.close()


_WPS_PS = r"""
$ErrorActionPreference = 'Stop'
$src = $env:SF_SRC
$dst = $env:SF_DST
$app = $null
foreach ($id in @('Kwps.Application', 'Word.Application')) {
  try { $app = New-Object -ComObject $id; break } catch {}
}
if (-not $app) { Write-Output 'NOAPP'; exit 3 }
try {
  $app.Visible = $false
  try { $app.DisplayAlerts = 0 } catch {}
  $doc = $app.Documents.Open($src, $false, $true)
  $doc.ExportAsFixedFormat($dst, 17)
  $doc.Close(0)
  Write-Output 'OK'
} catch {
  Write-Output ('ERR ' + $_.Exception.Message)
  try { $app.Quit() } catch {}
  exit 2
}
try { $app.Quit() } catch {}
"""


def _docx_to_pdf_office(blob: bytes) -> bytes:
    """用本机的 WPS / Office 转 PDF：版式和原文件几乎一致（本机装了 WPS 就能用）。"""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="docx2pdf-") as td:
        src = Path(td) / "in.docx"
        dst = Path(td) / "out.pdf"
        src.write_bytes(blob)
        script = Path(td) / "conv.ps1"
        script.write_text(_WPS_PS, encoding="utf-8-sig")
        env = dict(os.environ)
        env["SF_SRC"] = str(src)
        env["SF_DST"] = str(dst)
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            capture_output=True, timeout=180, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (proc.stdout or b"").decode("utf-8", "replace").strip()
        if not dst.exists() or dst.stat().st_size < 1000:
            raise RuntimeError(out or "Office 转换没有产物")
        return dst.read_bytes()


def docx_to_pdf(blob: bytes) -> tuple[bytes, str]:
    try:
        return _docx_to_pdf_office(blob), "office"
    except Exception as exc:  # noqa: BLE001 — 本机没装 WPS/Office 或转换失败
        print(f"[convert] WPS/Office 转换不可用，降级：{exc}")
    if _soffice():
        try:
            return _docx_to_pdf_soffice(blob), "soffice"
        except Exception as exc:  # noqa: BLE001 — 降级到内置渲染
            print(f"[convert] soffice 失败，改用内置渲染：{exc}")
    return _html_to_pdf(_docx_to_html(blob)), "builtin-chromium"


# ------------------------------------------------------------------ pdf → docx
def _split_columns(rects: list, width: float) -> list[tuple[float, float]]:
    """按 x 分布判断这页是单栏还是双栏，返回 [(x0, x1)]。"""
    if not rects:
        return [(0.0, width)]
    mid = width / 2.0
    left = [r for r in rects if (r.x0 + r.x1) / 2 < mid - width * 0.04]
    right = [r for r in rects if (r.x0 + r.x1) / 2 > mid + width * 0.04]
    if len(left) >= 3 and len(right) >= 3:
        gap_l = max(r.x1 for r in left)
        gap_r = min(r.x0 for r in right)
        if gap_r - gap_l > 6:
            return [(0.0, (gap_l + gap_r) / 2), ((gap_l + gap_r) / 2, width)]
    return [(0.0, width)]


def _merge_para(texts: list[str]) -> str:
    """把 PDF 里被切断的行接回一段：中文直接接，西文补空格，行尾连字符去掉。"""
    out = ""
    for raw in texts:
        t = raw.strip()
        if not t:
            continue
        if not out:
            out = t
            continue
        if out.endswith("-") and len(out) > 1 and out[-2].isalpha() and t[:1].islower():
            out = out[:-1] + t
            continue
        a, b = out[-1], t[0]
        cjk = lambda ch: "⺀" <= ch <= "鿿" or "＀" <= ch <= "￯"
        if not (cjk(a) or cjk(b)):
            out += " "
        out += t
    return out


def _figure_bands(page, col_rects: list) -> list:
    """在一列里找「没有文字、却有画的」竖直区间，当作插图区（整幅方案图也能整块框住）。"""
    try:
        drawings = [dr["rect"] for dr in page.get_drawings() if dr.get("rect")]
    except Exception:  # noqa: BLE001
        return []
    if not drawings:
        return []
    rects = sorted(col_rects, key=lambda r: r.y0)
    if not rects:
        return []
    x0 = min(r.x0 for r in rects)
    x1 = max(r.x1 for r in rects)
    bands = []
    cur = min(r.y0 for r in rects) - 4
    for r in rects:
        gap = r.y0 - cur
        if gap > 30:
            band = (cur, r.y0)
            hit = [d for d in drawings
                   if d.y0 < band[1] - 4 and d.y1 > band[0] + 4 and d.x0 < x1 and d.x1 > x0]
            if hit:
                yy0 = min([band[0]] + [d.y0 for d in hit])
                yy1 = max([band[1]] + [d.y1 for d in hit])
                if yy1 - yy0 > 50:
                    bands.append((x0, yy0, x1, yy1))
        cur = max(cur, r.y1)
    tail = (cur, max(cur + 4, max(d.y1 for d in drawings) if drawings else cur + 4))
    if tail[1] - tail[0] > 50:
        hit = [d for d in drawings if d.y0 < tail[1] and d.y1 > tail[0] and d.x0 < x1 and d.x1 > x0]
        if hit:
            bands.append((x0, min([tail[0]] + [d.y0 for d in hit]),
                          x1, max([tail[1]] + [d.y1 for d in hit])))
    return bands


def pdf_to_docx(blob: bytes) -> tuple[bytes, str]:
    try:
        import pymupdf  # type: ignore
    except ImportError:  # 老版本只有 fitz
        import fitz as pymupdf  # type: ignore

    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    src = pymupdf.open(stream=blob, filetype="pdf")
    d = Document()
    st = d.styles["Normal"]
    st.font.name = "宋体"
    st.font.size = Pt(10.5)
    try:
        from docx.oxml.ns import qn
        st.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    except Exception:  # noqa: BLE001
        pass
    sec = d.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin = sec.bottom_margin = Cm(2.0)

    def ea(run):
        run.font.name = "宋体"
        try:
            from docx.oxml.ns import qn
            run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
        except Exception:  # noqa: BLE001
            pass

    for pno, page in enumerate(src):
        if pno:
            d.add_page_break()
        W, H = page.rect.width, page.rect.height
        blocks = page.get_text("dict").get("blocks", [])
        text_blocks = [b for b in blocks if b.get("type") != 1]
        text_rects = [pymupdf.Rect(b["bbox"]) for b in text_blocks]
        cols = _split_columns(text_rects, W)
        figs = []
        for a, b in cols:
            in_col = [r for r in text_rects if r.x0 >= a - 6 and r.x1 <= b + 6]
            for (bx0, by0, bx1, by1) in _figure_bands(page, in_col):
                figs.append(pymupdf.Rect(bx0, by0, bx1, by1))

        def col_of(x: float) -> int:
            for i, (a, b) in enumerate(cols):
                if a - 4 <= x <= b + 4:
                    return i
            return 0

        elems = []
        for b in text_blocks:
            rect = pymupdf.Rect(b["bbox"])
            if rect.height < 3:
                continue
            # 落在插图里的文字（图注/标注）不要重复排
            if any(rect in f or (rect & f).get_area() > rect.get_area() * 0.75 for f in figs):
                continue
            lines = []
            size = 0.0
            bold = italic = False
            for ln in b.get("lines", []):
                t = "".join(sp.get("text", "") for sp in ln.get("spans", []))
                if t.strip():
                    lines.append(t)
                for sp in ln.get("spans", []):
                    size = max(size, float(sp.get("size") or 0))
                    fl = int(sp.get("flags") or 0)
                    bold = bold or bool(fl & 16)
                    italic = italic or bool(fl & 2)
            if not lines:
                continue
            elems.append((col_of(rect.x0), rect.y0, "text", (lines, size, bold, italic)))

        for f in figs:
            elems.append((col_of(f.x0), f.y0, "fig", f))

        elems.sort(key=lambda e: (e[0], e[1]))
        for _col, _y, kind, payload in elems:
            if kind == "fig":
                rect = payload
                # 太高/太窄的框多半是几处图被合并了，塞不进页面就跳过，免得整页空白
                if rect.height > page.rect.height * 0.72 or rect.width < 90:
                    continue
                try:
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(2.2, 2.2), clip=rect, alpha=False)
                    img = io.BytesIO(pix.tobytes("png"))
                    max_w, max_h = 16.6, 18.5      # cm：版心宽、半页高再留一点
                    w_cm = min(max_w, rect.width * 0.0353)
                    h_cm = w_cm * (rect.height / max(1.0, rect.width))
                    if h_cm > max_h:
                        w_cm = w_cm * (max_h / h_cm)
                    par = d.add_paragraph()
                    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    par.paragraph_format.space_before = Pt(4)
                    par.paragraph_format.space_after = Pt(4)
                    par.add_run().add_picture(img, width=Cm(max(3.0, w_cm)))
                except Exception:  # noqa: BLE001
                    pass
                continue
            lines, size, bold, italic = payload
            text = _merge_para(lines)
            if not text:
                continue
            par = d.add_paragraph()
            par.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            pf = par.paragraph_format
            pf.space_before, pf.space_after = Pt(0), Pt(3)
            pf.line_spacing = 1.25
            r = par.add_run(text)
            ea(r)
            if size:
                r.font.size = Pt(max(7.0, min(22.0, round(size, 1))))
            r.bold = bold
            r.italic = italic

    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue(), "pymupdf-layout"

def _stem(name: str) -> str:
    from urllib.parse import quote

    base = Path(name or "file").stem[:40] or "file"
    return quote(base)


@router.post("/docx2pdf")
async def api_docx2pdf(file: UploadFile = File(...)):
    blob = await _read(file)
    if not blob[:2] == b"PK":
        raise HTTPException(400, "这不是 docx（看着不像 Word 文件）")
    try:
        # playwright 的同步 API 不能在事件循环里跑，扔到线程池
        out, engine = await run_in_threadpool(docx_to_pdf, blob)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"转 PDF 失败：{exc or type(exc).__name__}") from exc
    return Response(out, media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{_stem(file.filename)}.pdf",
        "X-Engine": engine,
    })


@router.post("/pdf2docx")
async def api_pdf2docx(file: UploadFile = File(...)):
    blob = await _read(file)
    if b"%PDF" not in blob[:1024]:
        raise HTTPException(400, "这不是 PDF 文件")
    try:
        out, engine = await run_in_threadpool(pdf_to_docx, blob)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"转 Word 失败：{exc or type(exc).__name__}") from exc
    return Response(out, media_type=DOCX_MIME, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{_stem(file.filename)}.docx",
        "X-Engine": engine,
    })
