# -*- coding: utf-8 -*-
"""去水印。

做法：很多 PDF（尤其竞赛卷、教辅）的水印是**单独一层图片对象**，铺满整页、颜色很浅。
这种不用像素修补，渲染前把这个图层从页面里删掉即可 —— 文字、线条、公式都还在原来的
图层上，删完的页面和原版一样，只是没有了底纹。

判定条件（同时满足，宁可漏删也不误删正文图）：
1. 占页面面积 ≥ 30%；
2. 图片本身平均亮度 ≥ 220（浅色）；
3. 平均饱和度 ≤ 12（灰阶，水印通常是灰的；彩色大图不删）。

对于「文字型水印」或「已经烤进扫描图的水印」，这里不处理 —— 那种要么得靠 OCR 定位，
要么得靠 inpainting，属于另一个量级的事。
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

_MIN_AREA = 0.30
_MEAN_L = 220.0
_MAX_SAT = 12.0


def _is_light_overlay(raw: bytes) -> bool:
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return False
    a = np.asarray(im, dtype=np.float32)
    if a.size == 0:
        return False
    l = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
    sat = a.max(axis=2) - a.min(axis=2)
    return float(l.mean()) >= _MEAN_L and float(sat.mean()) <= _MAX_SAT


def strip_watermarks(page, doc=None) -> int:
    """删掉本页上的浅色大图层，返回删掉的个数。"""
    doc = doc if doc is not None else page.parent
    w, h = page.rect.width, page.rect.height
    if w <= 0 or h <= 0:
        return 0
    killed = 0
    seen = set()
    for img in page.get_images(full=True):
        xref = img[0]
        if xref in seen:
            continue
        seen.add(xref)
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            continue
        big = any(((r.width * r.height) / (w * h)) >= _MIN_AREA for r in rects)
        if not big:
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
            continue
    return killed


def strip_doc(doc) -> int:
    """整份文档逐页删水印层（同一 xref 只处理一次，重复调用无害）。"""
    total = 0
    for i in range(len(doc)):
        try:
            total += strip_watermarks(doc.load_page(i), doc)
        except Exception:
            continue
    return total
