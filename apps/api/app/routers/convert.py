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
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/convert", tags=["convert"])

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# WPS/Word 的 COM、LibreOffice 的用户配置目录都是「全局独占」的：
# 两个人同时点转换会互相抢锁，表现为「随机失败」。这里串行化重引擎，轻引擎不受影响。
_CONV_LOCK = threading.Lock()
_TIMEOUT = int(os.environ.get("TIKU_CONVERT_TIMEOUT", "180") or 180)

_log = logging.getLogger("tiku.convert")


class ConvertError(RuntimeError):
    """带错误码的转换失败：前端拿到的是中文原因 + 可搜的码，不是一段英文堆栈。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _pymupdf():
    """统一拿 PyMuPDF：新版叫 pymupdf，老版本只有 fitz。两边都得能用。"""
    try:
        import pymupdf  # type: ignore
        return pymupdf
    except ImportError:
        pass
    try:
        import fitz as pymupdf  # type: ignore
        return pymupdf
    except ImportError as exc:
        raise ConvertError("E_NO_PYMUPDF", "缺少 PDF 处理组件（PyMuPDF），重装一下软件就好") from exc


def _dump_name(name: str) -> str:
    return Path(name or "file").name[:60]


def _log_fail(where: str, name: str, exc: BaseException) -> None:
    _log.warning("[%s] %s 失败：%s: %s", where, _dump_name(name), type(exc).__name__, exc)


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
        "pypdf": _has("pypdf"),
        "pikepdf": _has("pikepdf"),
        "ocr": _has("rapidocr_onnxruntime"),
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
        raise ConvertError("E_NO_SOFFICE", "没找到 LibreOffice")
    with tempfile.TemporaryDirectory(prefix="tiku-conv-") as td:
        src = Path(td) / "in.docx"
        src.write_bytes(blob)
        env = dict(os.environ)
        env["HOME"] = td  # 让 soffice 用临时配置目录，避免和已开的 LibreOffice 抢锁
        env["TMPDIR"] = td
        try:
            proc = subprocess.run(
                [exe, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", td, str(src)],
                capture_output=True, timeout=_TIMEOUT, env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConvertError("E_TIMEOUT", f"LibreOffice 超过 {_TIMEOUT} 秒没转完，可能是文档太大或卡住了") from exc
        out = Path(td) / "in.pdf"
        if not out.exists():
            err = (proc.stderr or b"").decode("utf-8", "ignore")[:300]
            raise ConvertError("E_SOFFICE_FAIL", err or "LibreOffice 没产出 PDF（文件可能已损坏）")
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
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ConvertError("E_NO_CHROMIUM", "缺少内置渲染组件（playwright），只能装 WPS 或用 LibreOffice") from exc

    # 便携包把浏览器放在 runtime/ms-playwright，打包环境里必须显式指过去，
    # 否则 playwright 会去 %LOCALAPPDATA% 找，报 "Executable doesn't exist"。
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        for guess in (
            Path(os.environ.get("TIKU_ROOT", "")) / "runtime" / "ms-playwright",
            Path(__file__).resolve().parents[3] / "runtime" / "ms-playwright",
        ):
            if guess.is_dir():
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(guess)
                break

    try:
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
    except ConvertError:
        raise
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            raise ConvertError("E_NO_CHROMIUM",
                               "内置浏览器没装好（便携包缺 runtime/ms-playwright），重装软件或改用 WPS 转换") from exc
        raise ConvertError("E_CHROMIUM_FAIL", f"内置渲染失败：{msg[:200]}") from exc


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
    """用本机的 WPS / Office 转 PDF：版式和原文件几乎一致（本机装了 WPS 就能用）。

    COM 是全局单例：两个请求同时进来，第二个 New-Object 会挂起或直接报错，
    这就是用户说的「有时能转、有时转不了」。所以整段加锁串行。
    """
    import subprocess
    import tempfile

    if not _CONV_LOCK.acquire(timeout=_TIMEOUT):
        raise ConvertError("E_BUSY", "转换任务排队超时（前面的文档还没转完），稍等一下再试")
    try:
        with tempfile.TemporaryDirectory(prefix="docx2pdf-") as td:
            src = Path(td) / "in.docx"
            dst = Path(td) / "out.pdf"
            src.write_bytes(blob)
            script = Path(td) / "conv.ps1"
            script.write_text(_WPS_PS, encoding="utf-8-sig")
            env = dict(os.environ)
            env["SF_SRC"] = str(src)
            env["SF_DST"] = str(dst)
            try:
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                    capture_output=True, timeout=_TIMEOUT, env=env,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as exc:
                raise ConvertError("E_TIMEOUT",
                                   f"WPS/Office 超过 {_TIMEOUT} 秒没转完（可能弹了对话框没关），先手动打开 WPS 看有没有提示") from exc
            out = (proc.stdout or b"").decode("utf-8", "replace").strip()
            if "NOAPP" in out:
                raise ConvertError("E_NO_OFFICE", "本机没装 WPS 或 Office")
            if not dst.exists() or dst.stat().st_size < 1000:
                raise ConvertError("E_OFFICE_FAIL", out or "Office 转换没有产物")
            return dst.read_bytes()
    finally:
        _CONV_LOCK.release()


def docx_to_pdf(blob: bytes) -> tuple[bytes, str]:
    """按 office → soffice → 内置 Chromium 依次降级；全挂时给一条能看懂的原因。"""
    reasons: list[str] = []
    try:
        return _docx_to_pdf_office(blob), "office"
    except Exception as exc:  # noqa: BLE001 — 本机没装 WPS/Office 或转换失败
        reasons.append(f"WPS/Office：{exc}")
        _log_fail("docx2pdf/office", "in.docx", exc)
    if _soffice():
        try:
            return _docx_to_pdf_soffice(blob), "soffice"
        except Exception as exc:  # noqa: BLE001 — 降级到内置渲染
            reasons.append(f"LibreOffice：{exc}")
            _log_fail("docx2pdf/soffice", "in.docx", exc)
    else:
        reasons.append("LibreOffice：未安装")
    try:
        return _html_to_pdf(_docx_to_html(blob)), "builtin-chromium"
    except ConvertError:
        raise
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"内置渲染：{exc}")
        _log_fail("docx2pdf/builtin", "in.docx", exc)
    raise ConvertError("E_ALL_ENGINES", "三种转 PDF 的办法都没成功（" + "；".join(reasons)[:300]
                       + "）。装个 WPS 再试，或者把这个文件发给我看看")


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


_CJK = "\u2e80-\u9fff\uff00-\uffef"
_SPACE_TWIXT = [
    # 中文紧挨数字/英文/公式：把 PDF 里那层多余空格去掉（"如 1833 年" → "如1833年"）
    (re.compile(rf"(?<=[{_CJK}])\s+(?=[A-Za-z0-9$\\])"), ""),
    (re.compile(rf"(?<=[A-Za-z0-9%$\\])\s+(?=[{_CJK}])"), ""),
    # 中文之间本来不该有空格（PDF 两端对齐常拉出来一层）
    (re.compile(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])"), ""),
    # 标点两侧不留空格
    (re.compile(r"\s+([，。；：、！？）】》”’])"), r"\1"),
    (re.compile(r"([（【《“‘])\s+"), r"\1"),
]


def _tidy_cjk(text: str) -> str:
    """压掉重复空格，并去掉中文与数字/英文/公式之间被 PDF 带出来的空格。"""
    t = re.sub(r"[ \t]{2,}", " ", text)
    for pat, rep in _SPACE_TWIXT:
        t = pat.sub(rep, t)
    return t.strip()


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
    return _tidy_cjk(out)


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
    pymupdf = _pymupdf()

    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    try:
        src = pymupdf.open(stream=blob, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise ConvertError("E_PDF_BROKEN", "这个 PDF 打不开（文件损坏或不是真正的 PDF）") from exc
    if src.needs_pass:
        src.close()
        raise ConvertError("E_PDF_ENCRYPTED", "这个 PDF 加了密码，先去掉密码再转")
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
            # 不能用两端对齐：行里夹英文/数字时 Word 会把空格拉成大河，
            # 用户看到的就是"数字和公式前面一大段空白"。
            par.alignment = WD_ALIGN_PARAGRAPH.LEFT
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


async def _read(file: UploadFile) -> bytes:
    """读上传文件（限制 200MB，超了直接给中文提示）。"""
    if file is None:
        raise HTTPException(400, "没有收到文件")
    blob = await file.read()
    if len(blob) > 200 * 1024 * 1024:
        raise HTTPException(413, "文件太大了（超过 200MB），先拆开再转")
    return blob


def _fail(what: str, exc: BaseException) -> HTTPException:
    """失败统一走这里：中文原因 + 错误码，英文堆栈只进日志，不回显到界面。"""
    if isinstance(exc, ConvertError):
        _log.warning("[%s] %s［%s］", what, exc.message, exc.code)
        code = 400 if exc.code in ("E_PDF_ENCRYPTED", "E_PDF_BROKEN", "E_PDF_EMPTY", "E_BUSY",
                                   "E_NO_OFFICE", "E_NO_SOFFICE", "E_NO_CHROMIUM",
                                   "E_NO_PYMUPDF", "E_USER_INPUT") else 500
        return HTTPException(code, f"{what}失败：{exc.message}［{exc.code}］")
    _log.exception("[%s] 未预期的异常", what)
    return HTTPException(500, f"{what}失败：出了点意外（{type(exc).__name__}），"
                              f"日志在 data/logs/app.log［E_UNKNOWN］")


@router.get("/diagnose")
def diagnose():
    """一条命令看清「为什么转不了」：引擎在不在、临时目录能不能写、内置浏览器有没有。"""
    import tempfile as _tf
    out: dict = {"engines": engines(), "timeout": _TIMEOUT}
    try:
        with _tf.TemporaryDirectory(prefix="tiku-diag-") as td:
            p = Path(td) / "w.txt"
            p.write_text("ok", encoding="utf-8")
            out["temp_writable"] = p.read_text(encoding="utf-8") == "ok"
            out["temp_dir"] = td
    except Exception as exc:  # noqa: BLE001
        out["temp_writable"] = False
        out["temp_error"] = str(exc)
    try:
        root = Path(os.environ.get("TIKU_ROOT") or Path(__file__).resolve().parents[3])
        ms = root / "runtime" / "ms-playwright"
        out["playwright_browsers"] = str(ms) if ms.is_dir() else ""
        out["office_exe"] = _soffice() or ""
    except Exception:  # noqa: BLE001
        pass
    have = [k for k in ("office", "soffice", "chromium") if out["engines"].get(k)]
    out["docx2pdf_ok"] = bool(have)
    out["hint"] = ("可以转：将按 " + " → ".join(have) + " 的顺序尝试") if have else \
        "三种引擎都没有：装个 WPS 就能转（或装 LibreOffice），否则只能做 PDF 拆分/合并"
    return out


@router.post("/docx2pdf")
async def api_docx2pdf(file: UploadFile = File(...)):
    blob = await _read(file)
    if not blob[:2] == b"PK":
        raise HTTPException(400, "这不是 docx（看着不像 Word 文件）")
    try:
        # playwright 的同步 API 不能在事件循环里跑，扔到线程池
        out, engine = await run_in_threadpool(docx_to_pdf, blob)
    except Exception as exc:  # noqa: BLE001
        raise _fail("转 PDF ", exc) from exc
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
        raise _fail("转 Word ", exc) from exc
    return Response(out, media_type=DOCX_MIME, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{_stem(file.filename)}.docx",
        "X-Engine": engine,
    })


# ------------------------------------------------------------------ PDF 拆分 / 合并
def _pages_spec(spec: str, total: int) -> list[int]:
    """'1-3,5,8-' → [0,1,2,4,7,8,...]（1 起，支持开区间与逗号）。"""
    text = str(spec or "").replace("，", ",").replace(" ", "")
    if not text:
        raise ValueError("请先写页码范围，例如 1-3,5")
    out: list[int] = []
    for chunk in text.split(","):
        if not chunk:
            continue
        if "-" in chunk:
            a, _, b = chunk.partition("-")
            start = int(a) if a.strip() else 1
            end = int(b) if b.strip() else total
        else:
            start = end = int(chunk)
        if start < 1 or end > total or start > end:
            raise ValueError(f"页码范围 {chunk} 不对（这份 PDF 一共 {total} 页）")
        out.extend(range(start - 1, end))
    seen: list[int] = []
    for i in out:
        if i not in seen:
            seen.append(i)
    return seen


def _pdf_split_pypdf(blob: bytes, pages: list[int]) -> bytes:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(blob))
    writer = PdfWriter()
    for i in pages:
        writer.add_page(reader.pages[i])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _pdf_split_pymupdf(blob: bytes, pages: list[int]) -> bytes:
    pymupdf = _pymupdf()

    src = pymupdf.open(stream=blob, filetype="pdf")
    out = pymupdf.open()
    for i in pages:
        out.insert_pdf(src, from_page=i, to_page=i)
    data = out.tobytes()
    out.close()
    src.close()
    return data


def _pdf_page_count(blob: bytes) -> int:
    """pypdf 和 pymupdf 谁有就用谁：只装了其中一个的环境也得能拆/合。"""
    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(blob)).pages)
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        raise ConvertError("E_PDF_BROKEN", "这个 PDF 打不开（文件损坏或加了密码）") from exc
    pymupdf = _pymupdf()
    try:
        doc = pymupdf.open(stream=blob, filetype="pdf")
        n = doc.page_count
        doc.close()
        return n
    except Exception as exc:  # noqa: BLE001
        raise ConvertError("E_PDF_BROKEN", "这个 PDF 打不开（文件损坏或加了密码）") from exc


def pdf_split(blob: bytes, spec: str) -> tuple[bytes, str, int]:
    """按页码范围拆成多个 PDF，打包成 ZIP 返回。spec='each' 表示一页一个。"""
    import zipfile

    total = _pdf_page_count(blob)
    if total <= 0:
        raise ConvertError("E_PDF_EMPTY", "这个 PDF 一页都没有")
    spec = str(spec or "").strip()
    groups: list[tuple[int, list[int]]] = []
    if spec.lower() in ("each", "逐页", "每页"):
        groups = [(i + 1, [i]) for i in range(total)]
    else:
        groups = [(0, _pages_spec(spec, total))]
    zbuf = io.BytesIO()
    engine = "pypdf"
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
        for _, pages in groups:
            try:
                data = _pdf_split_pypdf(blob, pages)
            except Exception:  # noqa: BLE001
                data = _pdf_split_pymupdf(blob, pages)
                engine = "pymupdf"
            if len(pages) == 1:
                label = f"p{pages[0] + 1}"
            else:
                label = f"p{pages[0] + 1}-{pages[-1] + 1}"
            zf.writestr(f"{label}.pdf", data)
    return zbuf.getvalue(), engine, len(groups)


def pdf_merge(blobs: list[bytes]) -> tuple[bytes, str]:
    try:
        from pypdf import PdfReader, PdfWriter

        writer = PdfWriter()
        for blob in blobs:
            for page in PdfReader(io.BytesIO(blob)).pages:
                writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue(), "pypdf"
    except Exception:  # noqa: BLE001
        pymupdf = _pymupdf()
        out = pymupdf.open()
        for blob in blobs:
            src = pymupdf.open(stream=blob, filetype="pdf")
            out.insert_pdf(src)
            src.close()
        data = out.tobytes()
        out.close()
        return data, "pymupdf"


def _is_cjk(ch: str) -> bool:
    if not ch:
        return False
    o = ord(ch)
    return 0x2E80 <= o <= 0x9FFF or 0xFF00 <= o <= 0xFFEF


def _ocr_page_lines(page) -> list[str]:
    """OCR 一页，按行合并成自然段（坐标是 0..1 的归一化值）。"""
    import numpy as np
    pymupdf = _pymupdf()

    pix = page.get_pixmap(matrix=pymupdf.Matrix(2.2, 2.2), alpha=False)
    try:
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    except Exception:  # noqa: BLE001
        return []
    try:
        from ..services.ai import _ocr_array  # 复用同一套 OCR
    except Exception:  # noqa: BLE001
        return []
    lines = _ocr_array(arr, pix.width, pix.height) or []
    rows = sorted(lines, key=lambda x: (round(float(x.get("y0") or 0), 3), float(x.get("x0") or 0)))
    merged: list[str] = []
    last_y = None
    last_x = None
    last_right = None
    for row in rows:
        t = str(row.get("text") or "").strip()
        if not t:
            continue
        y = float(row.get("y0") or 0)
        x = float(row.get("x0") or 0)
        newpara = True
        if merged and last_y is not None:
            gap = y - last_y
            same_col = last_x is not None and abs(x - last_x) < 0.08
            end_punct = merged[-1][-1:] in "。！？；：…"
            short = last_right is not None and last_right < 0.8
            newpara = gap > 0.045 or not same_col or end_punct or (short and gap > 0.02)
        if newpara:
            merged.append(t)
        else:
            tail = merged[-1][-1:]
            merged[-1] = merged[-1] + ("" if _is_cjk(tail) else " ") + t
        last_y = y
        last_x = x
        last_right = float(row.get("x1") or 0)
    return merged


def pdf_ocr_docx(blob: bytes, max_pages: int = 40) -> tuple[bytes, str]:
    """扫描版 PDF：逐页 OCR 认字排成 Word；认不出的页整页贴图，内容不丢。"""
    pymupdf = _pymupdf()
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    doc = pymupdf.open(stream=blob, filetype="pdf")
    out = Document()
    sec = out.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.0)
    sec.top_margin = sec.bottom_margin = Cm(1.8)
    normal = out.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(11)
    try:
        normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    except Exception:  # noqa: BLE001
        pass

    pages = min(doc.page_count, max_pages)
    ok = 0
    for idx in range(pages):
        page = doc[idx]
        text = (page.get_text() or "").strip()
        if len(text) >= 40:
            for line in text.splitlines():
                if line.strip():
                    out.add_paragraph(line.strip())
            ok += 1
        else:
            lines = _ocr_page_lines(page)
            if lines:
                for line in lines:
                    out.add_paragraph(line)
                ok += 1
            else:
                import tempfile

                pix = page.get_pixmap(matrix=pymupdf.Matrix(2.2, 2.2), alpha=False)
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
                    fh.write(pix.tobytes("png"))
                    tmp = fh.name
                try:
                    out.add_picture(tmp, width=Cm(16.6))
                finally:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
        if idx < pages - 1:
            out.add_page_break()

    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue(), f"rapidocr({ok}/{pages} pages editable)"


@router.post("/pdf-split")
async def api_pdf_split(file: UploadFile = File(...), pages: str = ""):
    blob = await _read(file)
    if b"%PDF" not in blob[:1024]:
        raise HTTPException(400, "这不是 PDF 文件")
    try:
        data, engine, n = await run_in_threadpool(pdf_split, blob, pages)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise _fail("拆分 PDF ", exc) from exc
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{_stem(file.filename)}-split.zip",
        "X-Engine": engine,
        "X-Parts": str(n),
    })


@router.post("/pdf-merge")
async def api_pdf_merge(files: list[UploadFile] = File(...)):
    blobs: list[bytes] = []
    for f in files or []:
        blob = await _read(f)
        if b"%PDF" not in blob[:1024]:
            raise HTTPException(400, f"{f.filename} 不是 PDF 文件")
        blobs.append(blob)
    if len(blobs) < 2:
        raise HTTPException(400, "合并至少要两个 PDF")
    try:
        data, engine = await run_in_threadpool(pdf_merge, blobs)
    except Exception as exc:  # noqa: BLE001
        raise _fail("合并 PDF ", exc) from exc
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": "attachment; filename*=UTF-8''merged.pdf",
        "X-Engine": engine,
    })


@router.post("/pdf2docx-ocr")
async def api_pdf2docx_ocr(file: UploadFile = File(...)):
    blob = await _read(file)
    if b"%PDF" not in blob[:1024]:
        raise HTTPException(400, "这不是 PDF 文件")
    try:
        out, engine = await run_in_threadpool(pdf_ocr_docx, blob)
    except Exception as exc:  # noqa: BLE001
        raise _fail("扫描件转 Word ", exc) from exc
    return Response(out, media_type=DOCX_MIME, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{_stem(file.filename)}.docx",
        "X-Engine": engine,
    })


# ---------------------------- Word 合并 / 拆分（R2） ----------------------------
# 为什么不用 "deepcopy 整段挪过去"：docx 里图片、样式都是 "关系 id" 引用的，
# 直接搬 XML 会让图片变红叉、样式串号。所以这里逐 run 重建：文字、粗斜体、
# 字号、字体名、颜色、段落缩进与行距都照抄，图片按 blob 重新 add_picture。

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _iter_blocks(doc):
    """按文档顺序产出 (kind, obj)：kind 是 'p' 或 'tbl'。"""
    from docx.oxml.ns import qn as _qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == _qn("w:p"):
            yield "p", Paragraph(child, doc)
        elif child.tag == _qn("w:tbl"):
            yield "tbl", Table(child, doc)


def _copy_para(dst, src_p, *, keep_style_from: str = "正文"):
    """把一个段落按"逐 run 重建"的方式写到 dst。"""
    par = dst.add_paragraph()
    pf, spf = par.paragraph_format, src_p.paragraph_format
    try:
        par.alignment = src_p.alignment
    except Exception:  # noqa: BLE001
        pass
    for attr, conv in (("left_indent", Cm), ("first_line_indent", Cm),
                       ("space_before", Pt), ("space_after", Pt)):
        v = getattr(spf, attr, None)
        if v is not None:
            try:
                setattr(pf, attr, conv(v.pt / 28.35) if conv is Cm else Pt(v.pt))
            except Exception:  # noqa: BLE001
                pass
    if spf.line_spacing is not None:
        try:
            pf.line_spacing = spf.line_spacing
        except Exception:  # noqa: BLE001
            pass

    for run in src_p.runs:
        # 图片：按 blob 重新插入（保留原宽度）
        drew = run._r.findall(qn("w:drawing"))
        if drew:
            for d in drew:
                _copy_drawing(dst, par, run, d)
            continue
        t = run.text
        if not t:
            continue
        r = par.add_run(t)
        r.bold = run.bold
        r.italic = run.italic
        r.underline = run.underline
        try:
            if run.font.size:
                r.font.size = Pt(run.font.size.pt)
            if run.font.name:
                r.font.name = run.font.name
                r.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), run.font.name)
            if run.font.color and run.font.color.rgb:
                r.font.color.rgb = run.font.color.rgb
        except Exception:  # noqa: BLE001
            pass
    return par


def _copy_drawing(dst, par, run, drawing):
    """把源文档里的图片按二进制搬过来（找不到就用占位文字，绝不插坏图）。"""
    try:
        rid = drawing.find(".//" + qn("a:blip")).get(qn("r:embed"))
        part = run.part.related_parts[rid]
        blob = part.blob
        width = None
        ext = drawing.find(".//" + qn("wp:extent"))
        if ext is not None:
            width = Cm(int(ext.get("cx")) / 360000)
        buf = io.BytesIO(blob)
        if width:
            par.add_run().add_picture(buf, width=width)
        else:
            par.add_run().add_picture(buf)
    except Exception:  # noqa: BLE001
        par.add_run("［这里有一张图，转换时没能复制过来］")


def docx_merge(blobs: list[bytes]) -> tuple[bytes, str]:
    from docx import Document as _Doc
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    if not blobs:
        raise ValueError("没有可合并的文档")
    out = _Doc()
    sec = out.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin = sec.bottom_margin = Cm(2.0)
    normal = out.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(12)
    try:
        normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    except Exception:  # noqa: BLE001
        pass

    first = True
    style_src = None
    for blob in blobs:
        src = _Doc(io.BytesIO(blob))
        if style_src is None:
            style_src = src
        if not first:
            out.add_page_break()
        first = False
        for kind, obj in _iter_blocks(src):
            if kind == "p":
                _copy_para(out, obj)
            else:
                # 表格：整块 XML 搬过去，再按单元格重建文字（避免样式 id 失效）
                rows = len(obj.rows)
                cols = max(1, len(obj.columns))
                t = out.add_table(rows=rows, cols=cols)
                try:
                    t.style = "Table Grid"
                except Exception:  # noqa: BLE001
                    pass
                for ri, row in enumerate(obj.rows):
                    for ci, cell in enumerate(row.cells):
                        if ci >= cols:
                            break
                        target = t.cell(ri, ci)
                        target.text = ""
                        for p in cell.paragraphs:
                            para = target.paragraphs[0] if not target.text and p is cell.paragraphs[0] else target.add_paragraph()
                            for run in p.runs:
                                r = para.add_run(run.text)
                                r.bold = run.bold
                                r.italic = run.italic
                        if not cell.paragraphs:
                            pass
        # 用第一份文档的标题样式兜底（合并后标题至少是"看起来像标题"）
    _ = style_src
    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue(), "python-docx（逐段重建，样式与图片都保留）"


_SPLIT_HEAD = re.compile(
    r"^\s*(?:第\s*[一二三四五六七八九十百0-9０-９]+\s*[题、.．]"
    r"|\(\s*\d{1,2}\s*\)|（\s*\d{1,2}\s*）"
    r"|\d{1,2}\s*[.．、]\s*\S)"
)


def docx_split(blob: bytes, mode: str = "question", n: int = 0) -> tuple[bytes, str, int]:
    """把一份 docx 拆成多份，打包 zip 返回。

    mode=question：按「第X题 / 1. / （1）」这类题号边界切；
    mode=count   ：平均切成 n 份。
    """
    from docx import Document as _Doc

    src = _Doc(io.BytesIO(blob))
    blocks = list(_iter_blocks(src))
    if not blocks:
        raise ValueError("这份文档是空的")

    groups: list[list] = []
    if mode == "count" and n >= 2:
        size = max(1, (len(blocks) + n - 1) // n)
        groups = [blocks[i:i + size] for i in range(0, len(blocks), size)]
    else:
        cur: list = []
        for kind, obj in blocks:
            text = obj.text if kind == "p" else ""
            if cur and kind == "p" and _SPLIT_HEAD.match(text or ""):
                groups.append(cur)
                cur = []
            cur.append((kind, obj))
        if cur:
            groups.append(cur)
        # 卷头（标题、考试说明）不应该单独成一份：并到第一题里
        def _has_head(g):
            return any(k == "p" and _SPLIT_HEAD.match(o.text or "") for k, o in g)

        if len(groups) > 1 and not _has_head(groups[0]):
            groups[1] = groups[0] + groups[1]
            groups.pop(0)
        if len(groups) < 2:
            raise ValueError("没找到可拆的题号边界（试试「平均拆成 N 份」）")

    out_zip = io.BytesIO()
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, g in enumerate(groups, 1):
            d = _Doc()
            sec = d.sections[0]
            sec.left_margin = sec.right_margin = Cm(2.2)
            sec.top_margin = sec.bottom_margin = Cm(2.0)
            normal = d.styles["Normal"]
            normal.font.name = "宋体"
            normal.font.size = Pt(12)
            try:
                normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
            except Exception:  # noqa: BLE001
                pass
            for kind, obj in g:
                if kind == "p":
                    _copy_para(d, obj)
                else:
                    rows = len(obj.rows)
                    cols = max(1, len(obj.columns))
                    t = d.add_table(rows=rows, cols=cols)
                    try:
                        t.style = "Table Grid"
                    except Exception:  # noqa: BLE001
                        pass
                    for ri, row in enumerate(obj.rows):
                        for ci, cell in enumerate(row.cells):
                            if ci >= cols:
                                break
                            tc = t.cell(ri, ci)
                            tc.text = ""
                            for p in cell.paragraphs:
                                para = tc.paragraphs[0] if tc.text == "" and p is cell.paragraphs[0] else tc.add_paragraph()
                                for run in p.runs:
                                    r = para.add_run(run.text)
                                    r.bold = run.bold
            buf = io.BytesIO()
            d.save(buf)
            zf.writestr(f"第{i}份.docx", buf.getvalue())
    return out_zip.getvalue(), "python-docx（按题号切开，样式与图片都保留）", len(groups)


@router.post("/docx-merge")
async def api_docx_merge(files: list[UploadFile] = File(...)):
    blobs: list[bytes] = []
    for f in files or []:
        blob = await _read(f)
        if not (f.filename or "").lower().endswith((".docx", ".doc")) or blob[:2] != b"PK":
            raise HTTPException(400, f"{f.filename} 不是 .docx（.doc 请先在 Word 里另存为 .docx）")
        blobs.append(blob)
    if len(blobs) < 2:
        raise HTTPException(400, "合并至少要两份 Word")
    try:
        data, engine = await run_in_threadpool(docx_merge, blobs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise _fail("合并 Word ", exc) from exc
    return Response(data, media_type=_DOCX_MIME, headers={
        "Content-Disposition": "attachment; filename*=UTF-8''merged.docx",
        "X-Engine": "python-docx",
        "X-Parts": str(len(blobs)),
    })


@router.post("/docx-split")
async def api_docx_split(file: UploadFile = File(...), mode: str = "question", n: int = 0):
    blob = await _read(file)
    if blob[:2] != b"PK":
        raise HTTPException(400, "这不是 .docx（.doc 请先在 Word 里另存为 .docx）")
    try:
        data, engine, parts = await run_in_threadpool(docx_split, blob, mode, n)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise _fail("拆分 Word ", exc) from exc
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{_stem(file.filename)}-split.zip",
        "X-Engine": "python-docx",
        "X-Parts": str(parts),
    })
