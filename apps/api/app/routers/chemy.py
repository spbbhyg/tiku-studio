"""本地题库接入层（`/api/chemy/*`）。

本项目的题库只有一处：`D:\\化学竞赛题库`（chemy，127.0.0.1:8080）。
这里只做四件事：
1. 探活与元数据（专题 / 分类 / 检索）——前端所有题库下拉的唯一来源；
2. 入库：把出题画布的结构化题目拼成题库的粘贴文本 → 导入草稿批次 → 补出题人 → 发布；
3. 草稿列表与题图上传；
4. 导出转发（打印页 / 打包 ZIP 由题库自己生成）。
"""
from __future__ import annotations

import base64
import html
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..services import chemy_client as chemy

router = APIRouter(prefix="/api/chemy", tags=["chemy"])

# 题库的专题 id ↔ 中文名（题库的粘贴导入只认中文名，见 chemy/importer.py TOPIC_NAMES）
TOPIC_CN = {
    "organic": "有机化学",
    "polymer": "高分子化学",
    "crystal": "晶体化学",
    "inorg-struct": "无机综合与结构推断",
    "equation": "方程式与元素化学",
    "thermo": "热力学与化学平衡",
    "electro": "电化学",
    "kinetics": "动力学",
    "analytical": "分析化学",
}

_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(value: str) -> str:
    """把画布里的 HTML 片段压成纯文本行（题库会自己做转义与 <p> 包装）。"""
    s = (value or "")
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p\s*>\s*<p[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"</(div|li|tr)\s*>", "\n", s, flags=re.I)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    lines = [ln.strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln)


class PartIn(BaseModel):
    label: str = ""
    text: str = ""
    points: float = 0.0


class ImageIn(BaseModel):
    name: str = "figure.png"
    data: str = ""          # dataURL 或裸 base64


class PublishIn(BaseModel):
    title: str = ""
    topic: str = "organic"
    year: int | None = None
    stem: str = ""
    parts: list[PartIn] = Field(default_factory=list)
    answer: str = ""
    points: float = 0.0
    author: str = ""
    paper_title: str = ""
    publish: bool = True
    images: list[ImageIn] = Field(default_factory=list)


def _build_paste_text(body: PublishIn) -> str:
    """拼成题库 importer.import_paste 认识的格式。"""
    head = ["===题目===",
            f"标题: {html_to_text(body.title) or '未命名题目'}"]
    topic_cn = TOPIC_CN.get(body.topic, body.topic)
    if topic_cn:
        head.append(f"专题: {topic_cn}")
    if body.year:
        head.append(f"年份: {body.year}")

    out = ["\n".join(head), "===题干===", html_to_text(body.stem)]

    parts = [p for p in body.parts if html_to_text(p.text)]
    if parts:
        out.append("===小问===")
        for i, p in enumerate(parts, 1):
            label = (p.label or "").strip() or f"1.{i}"
            if not re.match(r"^\d+[.．\-]\d+", label):
                label = f"1.{i}"
            out.append(f"{label} {html_to_text(p.text)}")

    answer = html_to_text(body.answer)
    out.append("===答案===")
    out.append(answer)
    return "\n".join(out) + "\n"


def _decode_image(item: ImageIn) -> tuple[str, bytes, str]:
    raw = (item.data or "").strip()
    mime = "image/png"
    if raw.startswith("data:"):
        head, _, payload = raw.partition(",")
        mime = head[5:].split(";")[0] or "image/png"
        raw = payload
    try:
        content = base64.b64decode(raw, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"题图 {item.name} 不是合法的图片数据") from exc
    if not content:
        raise HTTPException(400, f"题图 {item.name} 是空的")
    name = item.name if "." in item.name else item.name + ".png"
    return name, content, mime


# ---------- 探活与元数据 ----------

def _chemy_exe() -> str:
    """题库程序在哪：环境变量 > 安装目录自带 > 老位置。

    安装包会把题库放在 <安装目录>\chemy\chemy.exe，这样别人装完不用再找题库。
    """
    env = os.environ.get("TIKU_CHEMY_EXE")
    if env and Path(env).is_file():
        return env
    from ..config import ROOT

    cands = []
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent / "chemy" / "chemy.exe")
    cands.append(Path(ROOT) / "chemy" / "chemy.exe")
    cands.append(Path(r"D:\化学竞赛题库\chemy.exe"))
    for c in cands:
        if c.is_file():
            return str(c)
    return env or str(cands[-1])


@router.get("/status")
def status():
    """前端所有题库入口先调它：题库没起来时给"先启动题库"的引导，而不是一堆报错。"""
    try:
        h = chemy.health()
    except chemy.ChemyError as exc:
        return {"ok": False, "base": chemy.base_url(), "error": str(exc)}
    info = {"ok": bool(h.get("ok")), "base": chemy.base_url(),
            "version": h.get("version"), "stats": h.get("stats") or {}}
    try:
        info["topics"] = chemy.topics()
    except chemy.ChemyError:
        info["topics"] = []
    return info


@router.get("/topics")
def topics():
    try:
        return chemy.topics()
    except chemy.ChemyError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/start")
def start_bank():
    """题库没起来时，替老师把它拉起来（只启动进程，不改它任何文件）。"""
    try:
        if chemy.health().get("ok"):
            return {"ok": True, "already": True, "base": chemy.base_url()}
    except chemy.ChemyError:
        pass

    import subprocess

    exe = Path(_chemy_exe())
    if not exe.is_file():
        raise HTTPException(404, f"找不到题库程序：{exe}，请手动启动本地题库")

    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
    except OSError as exc:
        raise HTTPException(500, f"启动题库失败：{exc}") from exc
    return {"ok": True, "already": False, "exe": str(exe)}


@router.get("/tree")
def tree(topic: str = ""):
    """专题浏览：题库的 /api/search 要求必须带关键词，空的就查不到东西。"""
    try:
        if not topic:
            return chemy.all_questions()
        return chemy.tree(topic)
    except chemy.ChemyError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/taxonomy")
def taxonomy():
    try:
        return chemy.taxonomy()
    except chemy.ChemyError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/search")
def search(q: str = "", topic: str = "", page: int = 1,
           page_size: int = Query(20, alias="pageSize"), scope: str = ""):
    try:
        return chemy.search(q=q, topic=topic, page=page, page_size=page_size, scope=scope)
    except chemy.ChemyError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/questions/{qid}")
def get_question(qid: str):
    try:
        return chemy.question(qid)
    except chemy.ChemyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/drafts")
def get_drafts():
    try:
        return chemy.drafts()
    except chemy.ChemyError as exc:
        raise HTTPException(503, str(exc)) from exc


# ---------- 入库 ----------

@router.post("/publish")
def publish_question(body: PublishIn):
    """把一道题直接送进本地题库：粘贴导入 → 补出题人 → 发布（可选）。"""
    author = (body.author or "").strip()
    if not author:
        raise HTTPException(400, "请先填写出题人：入库的题目必须能追到是谁出的")

    text = _build_paste_text(body)
    paper_title = (body.paper_title or "").strip() or f"神奋出题 · {author}"

    try:
        res = chemy.import_paste(text, topic=body.topic, paper_title=paper_title)
        batch = res.get("batch")
        if not batch:
            raise HTTPException(502, f"题库没有建出批次：{res.get('warning') or res}")
        if not res.get("count"):
            raise HTTPException(502, "题库没有导入任何题目，请检查题目内容是否为空")

        # 导入的是 draft 批次；趁还是 draft 先把出题人写进标签，再发布
        qids = [d["id"] for d in chemy.drafts() if d.get("paper_id") == f"import-{batch}"]
        tags = [f"出题人：{author}"]
        for qid in qids:
            chemy.patch_question(qid, tags=tags)

        for item in body.images:
            name, content, mime = _decode_image(item)
            for qid in qids:
                chemy.upload_figure(qid, name, content, mime)

        published = 0
        if body.publish:
            published = int(chemy.publish(batch).get("published") or 0)
    except chemy.ChemyError as exc:
        raise HTTPException(502, str(exc)) from exc

    return {
        "ok": True,
        "batch": batch,
        "qids": qids,
        "count": res.get("count", 0),
        "published": published,
        "author": author,
        "urls": [f"{chemy.base_url()}/admin?q={q}" for q in qids],
    }


# ---------- 导出转发（题库自己产打印页 / 打包 ZIP） ----------

@router.get("/export")
def export(ids: str, mode: str = "print", answers: int = 0,
           title: str = "化学竞赛练习卷"):
    """打印页 / 打包 ZIP 一律由题库生成，前端只需跳转。"""
    wanted = [i.strip() for i in (ids or "").split(",") if i.strip()]
    if not wanted:
        raise HTTPException(400, "没有选题")
    if mode == "bundle":
        target = f"{chemy.base_url()}/api/export/bundle?ids={quote(','.join(wanted))}"
    else:
        target = (f"{chemy.base_url()}/print/collection?ids={quote(','.join(wanted))}"
                  f"&answers={1 if answers else 0}&title={quote(title)}")
    return RedirectResponse(target, status_code=307)
