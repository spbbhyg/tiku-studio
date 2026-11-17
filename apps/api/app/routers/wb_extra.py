"""错题集扩展接口：备注、批量移出、导出（HTML/CSV）；通用键值设置（助手人格等）。

与 workbench.py 同前缀 /api/wb，单独成文件避免动大文件。
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from PIL import Image, ImageOps
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
import numpy as np

from ..db import get_db
from ..models import Book, Question
from ..models_wb import AppSetting, WrongBook, WrongItem
from ..config import DATA_DIR, LIBRARY_DIR

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


# ---------------------------------------------------------------- 图片：上色 / 拼接
def _lib_path(rel: str) -> Path:
    """把前端传来的 library 相对路径安全地解析成绝对路径。"""
    raw = str(rel or "").strip().lstrip("/")
    if raw.startswith("api/images/"):
        raw = raw[len("api/images/"):]
    path = (LIBRARY_DIR / raw).resolve()
    if not str(path).startswith(str(LIBRARY_DIR.resolve())):
        raise HTTPException(400, "路径非法")
    if not path.is_file():
        raise HTTPException(404, "图片不存在")
    return path


def _save_png(im: Image.Image) -> dict:
    now = datetime.now()
    rel_dir = Path("questions") / now.strftime("%Y-%m")
    abs_dir = LIBRARY_DIR / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    name = f"{now.strftime('%H%M%S')}-{uuid.uuid4().hex[:8]}.png"
    target = abs_dir / name
    im.save(target, format="PNG")
    return {"path": target.relative_to(LIBRARY_DIR).as_posix()}


def _parse_color(value: str) -> tuple[int, int, int]:
    s = str(value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in s):
        raise HTTPException(400, "颜色格式应为 #rrggbb")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


class RecolorIn(BaseModel):
    path: str
    color: str = "#d21f1f"


@router.post("/recolor")
def recolor_image(body: RecolorIn):
    """把近黑的线条/结构式染成目标色，其余彩色内容保持原样。

    做法：按亮度做 alpha，白底变透明、黑线保留，RGB 全部换成目标色；
    饱和度高的像素（彩色照片、彩色标记）不参与染色。
    """
    src = _lib_path(body.path)
    try:
        im = ImageOps.exif_transpose(Image.open(src)).convert("RGBA")
    except Exception as e:                                   # noqa: BLE001
        raise HTTPException(400, f"无法读取图片：{e}")
    if im.width * im.height > 40_000_000:
        raise HTTPException(400, "图片太大，无法染色")
    a = np.asarray(im, dtype=np.float32)
    rgb, alpha = a[:, :, :3], a[:, :, 3] / 255.0
    lum = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    neutral = sat <= 40
    ink = np.clip((255.0 - lum) / 255.0, 0.0, 1.0)
    target = np.array(_parse_color(body.color), dtype=np.float32)
    out = a.copy()
    out[:, :, 0] = np.where(neutral, target[0], rgb[:, :, 0])
    out[:, :, 1] = np.where(neutral, target[1], rgb[:, :, 1])
    out[:, :, 2] = np.where(neutral, target[2], rgb[:, :, 2])
    out[:, :, 3] = np.where(neutral, alpha * ink * 255.0, alpha * 255.0)
    result = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")
    saved = _save_png(result)
    return {"ok": True, **saved, "width": result.width, "height": result.height,
            "color": body.color}


class StitchIn(BaseModel):
    paths: list[str]
    direction: str = "v"          # h=横排，v=竖排
    gap: int = 10
    rotates: list[int] = []       # 每张图顺时针旋转角度（0/90/180/270）


@router.post("/stitch")
def stitch_images(body: StitchIn):
    """把多张图按横排/竖排拼成一张 PNG（等比缩放、统一留白间隔）。"""
    paths = [p for p in (body.paths or []) if str(p).strip()]
    if len(paths) < 2:
        raise HTTPException(400, "至少选择 2 张图片")
    if len(paths) > 30:
        raise HTTPException(400, "一次最多拼接 30 张图片")
    gap = max(0, min(80, int(body.gap or 0)))
    direction = "h" if str(body.direction).lower().startswith("h") else "v"
    rots = list(body.rotates or [])

    frames: list[Image.Image] = []
    for i, p in enumerate(paths):
        try:
            im = ImageOps.exif_transpose(Image.open(_lib_path(p)))
        except HTTPException:
            raise
        except Exception as e:                               # noqa: BLE001
            raise HTTPException(400, f"第 {i + 1} 张图无法读取：{e}")
        deg = int(rots[i]) % 360 if i < len(rots) else 0
        if deg:
            im = im.rotate(-deg, expand=True)
        frames.append(im.convert("RGBA"))
    if not frames:
        raise HTTPException(400, "没有可拼接的图片")

    def flatten(im: Image.Image) -> Image.Image:
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.split()[3])
        return bg

    frames = [flatten(f) for f in frames]
    try:
        if direction == "h":
            height = max(f.height for f in frames)
            scaled = [f.resize((max(1, round(f.width * height / f.height)), height),
                               Image.LANCZOS) for f in frames]
            width = sum(f.width for f in scaled) + gap * (len(scaled) - 1)
        else:
            width = max(f.width for f in frames)
            scaled = [f.resize((width, max(1, round(f.height * width / f.width))),
                               Image.LANCZOS) for f in frames]
            height = sum(f.height for f in scaled) + gap * (len(scaled) - 1)
        if max(width, height) > 12000:
            k = 12000 / max(width, height)
            width, height = max(1, round(width * k)), max(1, round(height * k))
            scaled = [f.resize((max(1, round(f.width * k)), max(1, round(f.height * k))),
                               Image.LANCZOS) for f in scaled]
        canvas = Image.new("RGB", (width, height), "white")
        if direction == "h":
            x = 0
            for f in scaled:
                canvas.paste(f, (x, 0))
                x += f.width + gap
        else:
            y = 0
            for f in scaled:
                canvas.paste(f, (0, y))
                y += f.height + gap
    except Exception as e:                                   # noqa: BLE001
        raise HTTPException(400, f"拼接失败：{e}")

    saved = _save_png(canvas)
    return {"ok": True, **saved, "width": canvas.width, "height": canvas.height,
            "direction": direction, "count": len(frames)}
