"""DOCX 文档编辑（SuperDoc）：在本机存一批 .docx，浏览器里直接编辑、另存。

另外负责文档里要插的「公式」「结构式」贴图：
- `/formula` 拉 KaTeX（带 mhchem，支持 \\ce{}）在 Chromium 里渲成 PNG
- `/structure` 把 Ketcher 画板导出的整块 SVG 栅格化成 PNG

只读写 `data/docx/`、`data/docx-assets/` 两个目录，不碰题库、不碰用户的其它文件。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from ..config import DATA_DIR

router = APIRouter(prefix="/api/doce", tags=["doce"])

DOCX_DIR = DATA_DIR / "docx"
ASSET_DIR = DATA_DIR / "docx-assets"
_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]')


def _ensure() -> None:
    DOCX_DIR.mkdir(parents=True, exist_ok=True)


def _clean(name: str) -> str:
    """只留文件名：去掉路径、非法字符，强制 .docx 结尾。"""
    base = Path(str(name or "")).name
    base = _BAD.sub("_", base).strip(" .")
    if not base:
        base = "未命名"
    if not base.lower().endswith(".docx"):
        base += ".docx"
    return base[:80]


def _path(name: str) -> Path:
    p = (DOCX_DIR / _clean(name)).resolve()
    if DOCX_DIR.resolve() not in p.parents and p.parent != DOCX_DIR.resolve():
        raise HTTPException(400, "文件名不合法")
    return p


def _free(name: str) -> Path:
    """重名就加 (2)(3)…，不覆盖已有文档。"""
    target = _path(name)
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(2, 500):
        cand = target.with_name(f"{stem} ({i}){suffix}")
        if not cand.exists():
            return cand
    raise HTTPException(400, "同名文档太多了，改个名字再传")


def _info(p: Path) -> dict:
    st = p.stat()
    return {
        "name": p.name,
        "size": st.st_size,
        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "url": f"/api/doce/file/{p.name}",
    }


@router.get("/files")
def list_files() -> dict:
    _ensure()
    items = [_info(p) for p in DOCX_DIR.glob("*.docx") if p.is_file()]
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"files": items, "dir": str(DOCX_DIR), "count": len(items)}


@router.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    _ensure()
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(400, "只收 .docx（老式的 .doc 先用 Word 另存为 .docx）")
    target = _free(file.filename or "未命名.docx")
    with target.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    if target.stat().st_size < 100:
        target.unlink(missing_ok=True)
        raise HTTPException(400, "这个文件是空的，可能没传上来")
    return {"ok": True, **_info(target)}


@router.get("/file/{name}")
def get_file(name: str) -> FileResponse:
    p = _path(name)
    if not p.exists():
        raise HTTPException(404, "文档不存在，可能已经被删掉")
    return FileResponse(
        p,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=p.name,
    )


@router.post("/save")
async def save(file: UploadFile = File(...), name: str = Form(""), overwrite: bool = Form(False)) -> dict:
    """把编辑器导出的 docx 存回 data/docx/。overwrite=False 时重名另起一个。"""
    _ensure()
    target = _path(name or file.filename or "编辑稿.docx")
    if not overwrite:
        target = _free(target.name)
    with target.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    if target.stat().st_size < 100:
        target.unlink(missing_ok=True)
        raise HTTPException(400, "导出的文件是空的")
    return {"ok": True, **_info(target)}


@router.delete("/file/{name}")
def delete_file(name: str) -> dict:
    p = _path(name)
    if p.exists():
        p.unlink()
    return {"ok": True}


@router.get("/status")
def status() -> dict:
    _ensure()
    return {"dir": str(DOCX_DIR), "count": len(list(DOCX_DIR.glob("*.docx")))}


# ---------------------------------------------------------------- 公式 / 结构式贴图
#
# Word 里要「能看、能打印、导出 docx 后仍然在」，最稳的做法是把公式和结构式
# 渲成图片再插进去：图片在 docx 里是真实资源，不会因为字体缺失而变成方框。
# 图片尺寸按 CSS 像素 1:1 折算成 pt（1px = 0.75pt），贴进文档后和正文字号协调。

def _assets() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)


def _asset_name(kind: str, key: str) -> str:
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"{kind}-{h}.png"


def _asset_payload(png: bytes, name: str, css_w: float, css_h: float) -> dict:
    """同时给前端「预览用 URL」和「插文档用 data URL」。"""
    _assets()
    path = ASSET_DIR / name
    path.write_bytes(png)
    return {
        "ok": True,
        "url": f"/api/doce/asset/{name}",
        "data": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
        "width": round(css_w * 0.75, 2),
        "height": round(css_h * 0.75, 2),
        "size": path.stat().st_size,
    }


def _katex_dir() -> Path | None:
    """找到前端静态目录里的 katex（打包后在 apps/web/dist/katex）。"""
    here = Path(__file__).resolve()
    # doce.py 在 apps/api/app/routers/ 下 → parents[3] 是 apps/
    for base in (here.parents[3] / "web" / "dist", here.parents[3] / "web" / "public"):
        cand = base / "katex" / "katex.min.js"
        if cand.exists():
            return base / "katex"
    return None


def _render_formula(tex: str, display: bool, base_url: str) -> tuple[bytes, float, float]:
    """用 Chromium + KaTeX 把 LaTeX/化学式渲成透明 PNG。

    KaTeX 的 js/css 必须走 http 同源加载（file:// 会被 Chromium 拦掉），
    所以先访问本服务的 /formula-page，再对渲染好的节点截图。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise HTTPException(400, "未安装 playwright，无法渲染公式") from e

    payload = base64.urlsafe_b64encode(tex.encode("utf-8")).decode("ascii")
    url = f"{base_url.rstrip('/')}/api/doce/formula-page?b64={payload}&d={1 if display else 0}"
    with sync_playwright() as p:
        browser = None
        for kwargs in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
            try:
                browser = p.chromium.launch(**kwargs)
                break
            except Exception:
                continue
        if browser is None:
            raise HTTPException(500, "没有可用的浏览器内核，公式渲不出来")
        try:
            page = browser.new_page(device_scale_factor=3, viewport={"width": 1400, "height": 600})
            page.goto(url, wait_until="load", timeout=30000)
            try:
                page.wait_for_function("window.__fxDone === true", timeout=15000)
            except Exception:
                pass
            el = page.query_selector("#out")
            box = el.bounding_box() if el else None
            png = el.screenshot(omit_background=True) if el else b""
        finally:
            browser.close()
    if not png:
        raise HTTPException(500, "公式渲染失败，换个写法试试")
    return png, (box or {}).get("width", 0), (box or {}).get("height", 0)


@router.get("/formula-page", response_class=HTMLResponse)
def formula_page(b64: str, d: int = 0) -> HTMLResponse:
    """给 Chromium 用的公式渲染页（内部用，前端不会直接打开）。"""
    try:
        tex = base64.urlsafe_b64decode(b64.encode("ascii")).decode("utf-8")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, "公式参数不合法") from e
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<link rel='stylesheet' href='/katex/katex.min.css'>"
        "<script src='/katex/katex.min.js'></script>"
        "<script src='/katex/contrib/mhchem.min.js'></script>"
        "<style>html,body{margin:0;background:transparent}"
        "#out{display:inline-block;padding:10px 12px;font-size:30px;color:#111;line-height:1.35}"
        ".katex-display{margin:0}</style></head><body><div id='out'></div><script>"
        f"try{{katex.render({json.dumps(tex)},document.getElementById('out'),"
        f"{{throwOnError:false,displayMode:{'true' if d else 'false'}}});}}"
        "catch(e){document.getElementById('out').textContent=" + json.dumps(tex) + ";}"
        "window.__fxDone=true;</script></body></html>"
    )
    return HTMLResponse(html)


def _render_structure(svg: str) -> tuple[bytes, float, float]:
    """Ketcher 画板整块 SVG → PNG（箭头、加号、文字标注都在里面）。"""
    import pymupdf as fitz

    src = svg.strip()
    if not src or "<svg" not in src:
        raise HTTPException(400, "画板里还没有结构")
    doc = fitz.open(stream=src.encode("utf-8"), filetype="svg")
    if doc.page_count < 1:
        raise HTTPException(400, "这个结构解析不了，重画一次试试")
    page = doc[0]
    rect = page.rect
    zoom = 3.2
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=True)
    png = pix.tobytes("png")
    # fitz 的 SVG 页面单位就是 pt，乘上缩放得到像素；换算回 CSS 像素留出余量
    w = max(8.0, rect.width * 0.86)
    h = max(8.0, rect.height * 0.86)
    return png, w, h


class FormulaIn(BaseModel):
    tex: str
    display: bool = False


class StructureIn(BaseModel):
    svg: str


@router.post("/formula")
def make_formula(body: FormulaIn) -> dict:
    tex = (body.tex or "").strip()
    if not tex:
        raise HTTPException(400, "先把公式写进框里，比如 \\ce{2H2 + O2 -> 2H2O}")
    if len(tex) > 4000:
        raise HTTPException(400, "公式太长了，拆成几段插吧")
    key = ("f|%s|%s" % ("d" if body.display else "i", tex))
    name = _asset_name("formula", key)
    _assets()
    cached = ASSET_DIR / name
    if cached.exists():
        import pymupdf as fitz

        pix = fitz.Pixmap(str(cached))
        return _asset_payload(cached.read_bytes(), name, pix.width / 3.0, pix.height / 3.0)
    from ..services.exam_render import base_url

    png, w, h = _render_formula(tex, body.display, base_url())
    return _asset_payload(png, name, w, h)


@router.post("/structure")
def make_structure(body: StructureIn) -> dict:
    svg = body.svg or ""
    key = "s|" + hashlib.sha1(svg.encode("utf-8")).hexdigest()
    name = _asset_name("struct", key)
    _assets()
    cached = ASSET_DIR / name
    if cached.exists():
        import pymupdf as fitz

        pix = fitz.Pixmap(str(cached))
        return _asset_payload(cached.read_bytes(), name, pix.width / 3.2, pix.height / 3.2)
    png, w, h = _render_structure(svg)
    return _asset_payload(png, name, w, h)


@router.get("/asset/{name}")
def get_asset(name: str) -> FileResponse:
    p = ASSET_DIR / Path(str(name)).name
    if not p.exists():
        raise HTTPException(404, "图片不存在")
    return FileResponse(p, media_type="image/png")
