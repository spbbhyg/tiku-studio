"""错题集扩展接口：备注、批量移出、导出（HTML/CSV）；通用键值设置（助手人格等）。

与 workbench.py 同前缀 /api/wb，单独成文件避免动大文件。
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Book, Question
from ..models_wb import AppSetting, WrongBook, WrongItem
from ..config import DATA_DIR

router = APIRouter(prefix="/api/wb", tags=["workbench"])

IMAGES_DIR = Path(DATA_DIR) / "library"


class NoteIn(BaseModel):
    note: str = ""


class ItemsIn(BaseModel):
    question_ids: list[int] = []


class SettingIn(BaseModel):
    value: Any = None


def _img_src(ref) -> str:
    """题图引用可能是文件名、相对路径或 {name/path} 字典，统一成 /api/images/xxx。"""
    if isinstance(ref, dict):
        keep = ref.get("path")
        if keep:
            return f"/api/images/{str(keep).lstrip('/')}"
        ref = ref.get("name") or ref.get("url") or ref.get("file") or ""
    ref = str(ref or "").strip()
    if not ref:
        return ""
    if ref.startswith("http") or ref.startswith("/api/images/"):
        return ref
    name = Path(ref).name
    return f"/api/images/{name}"


# ------------------------------------------------------------------ 备注
@router.patch("/wrongbooks/{bid}/items/{qid}")
def set_wrong_note(bid: int, qid: int, body: NoteIn, db: Session = Depends(get_db)):
    it = db.scalar(select(WrongItem).where(WrongItem.book_id == bid, WrongItem.question_id == qid))
    if not it:
        raise HTTPException(404, "这道题不在该错题集里")
    it.note = (body.note or "").strip() or None
    it.last_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "note": it.note or ""}


# ------------------------------------------------------------------ 批量移出
@router.post("/wrongbooks/{bid}/items/delete")
def delete_wrong_items(bid: int, body: ItemsIn, db: Session = Depends(get_db)):
    ids = [i for i in (body.question_ids or []) if i]
    if not ids:
        return {"ok": True, "deleted": 0}
    rows = db.scalars(select(WrongItem).where(WrongItem.book_id == bid,
                                             WrongItem.question_id.in_(ids))).all()
    for it in rows:
        db.delete(it)
    db.commit()
    return {"ok": True, "deleted": len(rows)}


# ------------------------------------------------------------------ 导出
@router.get("/wrongbooks/{bid}/export")
def export_wrongbook(bid: int, fmt: str = "html", db: Session = Depends(get_db)):
    book = db.get(WrongBook, bid)
    if not book:
        raise HTTPException(404, "错题集不存在")
    rows = db.execute(
        select(WrongItem, Question)
        .join(Question, Question.id == WrongItem.question_id)
        .where(WrongItem.book_id == bid)
        .order_by(WrongItem.last_at.desc())
    ).all()

    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["序号", "题型", "难度", "题干", "选项", "答案", "解析", "错次", "备注", "来源", "题图"])
        for idx, (it, q) in enumerate(rows, 1):
            w.writerow([idx, q.q_type, q.difficulty, q.stem or "", " / ".join(q.options or []),
                        q.answer or "", q.analysis or "", it.wrong_count, it.note or "",
                        q.source_title or "", " ".join(_img_src(x) for x in (q.images or []))])
        body = buf.getvalue().encode("utf-8-sig")
        return Response(content=body, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="wrongbook-{bid}.csv"'})

    blocks: list[str] = []
    for idx, (it, q) in enumerate(rows, 1):
        figs = "".join(
            f'<div class="fig"><img src="{_img_src(x)}" alt="题图"></div>' for x in (q.images or []))
        opts = "".join(f'<div class="opt">{o}</div>' for o in (q.options or []))
        note = f'<div class="note"><b>我的备注：</b>{it.note}</div>' if it.note else ""
        ana = f'<div class="ana"><b>解析：</b>{q.analysis}</div>' if q.analysis else ""
        blocks.append(
            f'<section class="q"><h3>{idx}. <span class="t">{q.q_type or ""} · 错 {it.wrong_count} 次</span></h3>'
            f'<div class="stem">{q.stem or ""}</div>{figs}{opts}'
            f'<div class="ans"><b>答案：</b>{q.answer or "—"}</div>{ana}{note}</section>')

    html = f'''<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>错题集 · {book.name}</title>
<style>
 @page {{ size: A4; margin: 16mm 14mm; }}
 body {{ font-family: "KaiTi","楷体","SimSun",serif; font-size: 13pt; line-height: 1.9; color:#111; }}
 h1 {{ text-align:center; font-size: 18pt; margin: 0 0 6px; }}
 .meta {{ text-align:center; color:#666; font-size: 10.5pt; margin-bottom: 12px; }}
 .q {{ page-break-inside: avoid; border-bottom: 1px dashed #ccc; padding: 10px 0; }}
 .q h3 {{ font-size: 12.5pt; margin: 0 0 4px; }}
 .q h3 .t {{ color:#888; font-weight: normal; font-size: 10.5pt; }}
 .fig img {{ max-width: 60%; max-height: 260px; display:block; margin: 6px 0; }}
 .opt {{ margin-left: 1.6em; }}
 .ans {{ color:#065f46; font-size: 11pt; }}
 .ana {{ color:#374151; font-size: 10.5pt; }}
 .note {{ color:#92400e; font-size: 11pt; background:#fffbeb; padding: 4px 8px; border-radius: 6px; }}
</style></head><body>
<h1>错题集 · {book.name}</h1>
<div class="meta">共 {len(rows)} 题 · 导出于 {datetime.now().strftime("%Y-%m-%d %H:%M")}</div>
{"".join(blocks) if blocks else "<p>（这本错题集还是空的）</p>"}
</body></html>'''
    return Response(content=html.encode("utf-8"), media_type="text/html; charset=utf-8")


# ------------------------------------------------------------------ 通用设置
@router.get("/settings/{key}")
def get_setting(key: str, db: Session = Depends(get_db)):
    row = db.get(AppSetting, key)
    if not row or row.value is None:
        return {"key": key, "value": None}
    try:
        return {"key": key, "value": json.loads(row.value)}
    except (TypeError, ValueError):
        return {"key": key, "value": row.value}


@router.put("/settings/{key}")
def put_setting(key: str, body: SettingIn, db: Session = Depends(get_db)):
    row = db.get(AppSetting, key)
    val = json.dumps(body.value, ensure_ascii=False)
    if row:
        row.value = val
    else:
        db.add(AppSetting(key=key, value=val))
    db.commit()
    return {"ok": True, "key": key, "value": body.value}
