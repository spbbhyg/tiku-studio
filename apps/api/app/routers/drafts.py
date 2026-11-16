"""出题草稿：出到一半关掉页面也不丢。

只落 JSON 文件（data/drafts/<id>.json），不碰题库、不进 SQLite：
草稿本来就短命，坏了删掉即可，不需要迁移。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import DATA_DIR

router = APIRouter(prefix="/api/drafts", tags=["drafts"])

DATA = DATA_DIR / "drafts"
_ID_OK = re.compile(r"^[A-Za-z0-9_-]{6,40}$")


class DraftIn(BaseModel):
    id: str = ""
    title: str = ""
    mode: str = "canvas"          # canvas | text | paper
    payload: dict[str, Any] = Field(default_factory=dict)
    thumb: str = ""               # 可选：dataURL 缩略图


def _path(draft_id: str) -> Path:
    if not _ID_OK.match(draft_id or ""):
        raise HTTPException(400, "草稿 id 不合法")
    return DATA / f"{draft_id}.json"


def _load(draft_id: str) -> dict:
    p = _path(draft_id)
    if not p.is_file():
        raise HTTPException(404, "草稿不存在或已被删掉")
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"草稿文件坏了：{exc}") from exc


@router.get("")
def list_drafts(limit: int = 100):
    DATA.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in DATA.glob("*.json"):
        try:
            d = json.loads(p.read_text("utf-8"))
        except Exception:  # noqa: BLE001 — 坏文件跳过，不拖累列表
            continue
        rows.append({
            "id": d.get("id") or p.stem,
            "title": d.get("title") or "未命名草稿",
            "mode": d.get("mode") or "canvas",
            "blocks": d.get("blocks") or 0,
            "updated_at": d.get("updated_at") or "",
            "created_at": d.get("created_at") or "",
            "thumb": d.get("thumb") or "",
        })
    rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return {"drafts": rows[: max(1, min(limit, 500))], "total": len(rows)}


@router.get("/{draft_id}")
def get_draft(draft_id: str):
    return _load(draft_id)


@router.post("")
def save_draft(body: DraftIn):
    DATA.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    draft_id = (body.id or "").strip() or uuid.uuid4().hex[:12]
    p = _path(draft_id)
    old = {}
    if p.is_file():
        try:
            old = json.loads(p.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            old = {}
    blocks = 0
    for key in ("q_layout", "a_layout"):
        lay = body.payload.get(key) or {}
        blocks += len(lay.get("blocks") or []) if isinstance(lay, dict) else 0
    data = {
        "id": draft_id,
        "title": (body.title or old.get("title") or "").strip() or f"未命名草稿 {draft_id[:4]}",
        "mode": body.mode or old.get("mode") or "canvas",
        "payload": body.payload if body.payload else (old.get("payload") or {}),
        "thumb": body.thumb if body.thumb else (old.get("thumb") or ""),
        "blocks": blocks,
        "created_at": old.get("created_at") or now,
        "updated_at": now,
    }
    p.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    return {"ok": True, "id": draft_id, "updated_at": now, "blocks": blocks}


@router.delete("/{draft_id}")
def delete_draft(draft_id: str):
    p = _path(draft_id)
    if p.is_file():
        p.unlink()
    return {"ok": True}
