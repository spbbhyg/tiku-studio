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
