"""存储清理：找出没人引用的图片、旧上传、旧导出。

设计原则：**只报告、不误删**——只有「确实没被任何地方引用」的文件才进孤儿列表；
扫描与删除都走同一份逻辑，保证「扫到什么，删的就是什么」。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import DATA_DIR

LIBRARY = DATA_DIR / "library"
UPLOADS = DATA_DIR / "uploads"
EXPORTS = DATA_DIR / "exports"
DOCX = DATA_DIR / "docx"
LOGS = DATA_DIR / "logs"

OLD_DAYS = 7

# 这些后缀才算“可清理的图片/导出物”，别把用户文档当垃圾
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}
EXP_EXT = {".pdf", ".docx", ".zip", ".md", ".html"}


def _iter_files(root: Path, exts: set[str]) -> list[Path]:
    if not root.exists():
        return []
    out: list[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            out.append(p)
    return out


def _rel(p: Path) -> str:
    try:
        return p.relative_to(DATA_DIR).as_posix()
    except ValueError:
        return p.as_posix()


def _referenced(db: Session) -> set[str]:
    """把数据库里出现过的“文件路径/文件名”都收集起来，用来判断谁还在用。"""
    refs: set[str] = set()
    try:
        from ..models_wb import Paper, Question  # 延迟导入，避免循环
    except Exception:  # pragma: no cover
        Paper = Question = None  # type: ignore

    rows: list[str] = []
    try:
        from sqlalchemy import text

        for sql in (
            "SELECT stem_html FROM questions",
            "SELECT text_html FROM parts",
            "SELECT answer FROM questions",
            "SELECT snapshot FROM paper_questions",
            "SELECT content FROM reader_notes",
        ):
            try:
                for (val,) in db.execute(text(sql)).all():
                    if val:
                        rows.append(str(val))
            except Exception:
                continue
    except Exception:  # pragma: no cover
        pass

    # docx 里的图片引用（/api/images/<path> 或 <img src="...">）
    for p in _iter_files(DOCX, {".docx"}):
        try:
            import zipfile

            with zipfile.ZipFile(p) as z:
                for name in z.namelist():
                    if name.endswith(".xml"):
                        rows.append(z.read(name).decode("utf-8", "ignore"))
        except Exception:
            continue

    for txt in rows:
        for m in re.finditer(r"(?:/api/images/)?(library/[A-Za-z0-9_./\-\u4e00-\u9fff]+)", txt):
            refs.add(m.group(1))
        for m in re.finditer(r'"path"\s*:\s*"([^"]+)"', txt):
            refs.add(m.group(1).lstrip("/"))
    return refs


def scan(db: Session) -> dict:
    """返回 {stats:{orphan_images, old_uploads, old_exports, total_bytes}, 三个路径列表}"""
    refs = _referenced(db)
    orphans: list[Path] = []
    for p in _iter_files(LIBRARY, IMG_EXT):
        rel = _rel(p)
        # 只要有任何地方提到“文件名”或“相对路径”，就认为还在用
        if rel in refs or p.name in refs:
            continue
        if any(r.endswith(p.name) or p.name in r for r in refs):
            continue
        orphans.append(p)

    cutoff = time.time() - OLD_DAYS * 86400
    old_ups = [p for p in _iter_files(UPLOADS, IMG_EXT | EXP_EXT) if p.stat().st_mtime < cutoff]
    old_exp = [p for p in _iter_files(EXPORTS, EXP_EXT) if p.stat().st_mtime < cutoff]

    total = sum(p.stat().st_size for p in orphans + old_ups + old_exp if p.exists())
    return {
        "stats": {
            "orphan_images": len(orphans),
            "old_uploads": len(old_ups),
            "old_exports": len(old_exp),
            "total_bytes": total,
        },
        "orphan_images": orphans,
        "old_uploads": old_ups,
        "old_exports": old_exp,
    }


def run(db: Session) -> dict:
    r = scan(db)
    removed = 0
    freed = 0
    for p in r["orphan_images"] + r["old_uploads"] + r["old_exports"]:
        try:
            size = p.stat().st_size
            p.unlink()
            removed += 1
            freed += size
        except OSError:
            pass
    return {"removed": removed, "freed_bytes": freed}
