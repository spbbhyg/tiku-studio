"""给 workbench 增加「截图拆题」接口：按题号截成图片（自动去白边），同时存下 PDF 的文字层。

图给题库显示用，文字层（layout，kind=text）给导出排版用；题目图与答案图按题号配对。
"""
import io
import os
import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import LIBRARY_DIR
from ..db import get_db
from ..models import Question
from ..services import split_images as si
from ..services import topic_classify as tc
from ..services.bundle_import import _question_hash

router = APIRouter(prefix="/api/wb", tags=["workbench-split"])

WEBP = ("image/")


def _save_tmp(f: UploadFile) -> str:
    suffix = ".pdf"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(f.file.read())
    tmp.close()
    return tmp.name


@router.post("/split-images")
async def split_images(
    paper: UploadFile = File(...),
    answer: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    """把题目卷按「第 N 题」切成图片；给了答案卷就同法切答案图并配对。"""
    qpath = _save_tmp(paper)
    apath = _save_tmp(answer) if answer is not None else None
    try:
        job = si.make_job_dir(LIBRARY_DIR)
        qs = si.cut(qpath, job, "q")
        if not qs:
            raise HTTPException(400, "这份 PDF 里没找到「第 N 题」标题，可能是扫描件或排版不同")
        ans = {x["no"]: x for x in si.cut(apath, job, "a")} if apath else {}
        items = []
        for it in qs:
            a = ans.get(it["no"]) or {}
            topic = tc.classify(it.get("text") or "")
            items.append({
                "no": it["no"],
                "title": it["title"],
                "topic": topic,
                "paras": it.get("paras") or [],
                "blocks": it.get("blocks") or [],
                "q": [f"split/{job.name}/{f}" for f in it["files"]],
                "a": [f"split/{job.name}/{f}" for f in a.get("files", [])],
            })
        return {"ok": True, "count": len(items), "matched": sum(1 for i in items if i["a"]), "items": items}
    finally:
        for p in (qpath, apath):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


class SplitImportIn(dict):
    pass


@router.post("/split-import")
async def split_import(payload: dict, db: Session = Depends(get_db)):
    """把截图拆题结果入库：只存图片（题干图 + 答案图），不存文字。"""
    book_id = int(payload.get("book_id") or 0)
    items = payload.get("items") or []
    source = (payload.get("source_title") or "").strip() or None
    if not book_id or not items:
        raise HTTPException(400, "缺书册或题目")
    created = 0
    for it in items:
        q = it.get("q") or []
        a = it.get("a") or []
        blocks_in = [b for b in (it.get("blocks") or []) if isinstance(b, dict)]
        if not q and not blocks_in:
            continue
        title = f"第 {it.get('no')} 题 {it.get('title') or ''}".strip()
        stem = title
        ch = _question_hash(book_id, {"stem": stem, "q_type": "简答", "options": None,
                                      "answer": "", "knowledge_points": None, "source_page": None})
        exists = db.query(Question.id).filter(Question.book_id == book_id, Question.content_hash == ch).first()
        if exists:
            continue
        topic = (it.get("topic") or "").strip()
        # 图给题库显示；文字层进 layout（kind=text），导出时按样卷排版。
        # blocks 来自拆题（段落和图块交错）；老数据没有 blocks 就退回 paras + 整题图。
        blocks = [b for b in blocks_in
                  if (b.get("text") or b.get("path") or b.get("t") == "table")]
        if not blocks:
            blocks = [{"t": "p", "text": t} for t in (it.get("paras") or []) if str(t).strip()]
            blocks += [{"t": "img", "path": p} for p in q]
        layout = {
            "kind": "text", "version": 1,
            "meta": {"title": (it.get("title") or "").strip(), "percent": None},
            "stem": blocks, "subs": [],
            "answer": "", "answer_images": [{"path": p} for p in a],
        }
        row = Question(
            book_id=book_id, content_hash=ch, stem=stem, options=None,
            answer="", analysis=None, q_type="简答", difficulty=3,
            knowledge_points=[topic] if topic else [], images=[{"path": p} for p in q],
            layout=layout,
            answer_layout={"images": a} if a else None,
            source_title=source, status="draft", scope_status="pending",
        )
        db.add(row)
        created += 1
    db.commit()
    return {"ok": True, "created": created}


@router.post("/recolor")
def recolor(payload: dict):
    """把一张图里的黑色线条染成指定颜色（保留白色背景），返回新图路径。"""
    import time as _t
    from PIL import Image

    rel = (payload.get("path") or "").strip().replace("\\", "/")
    color = (payload.get("color") or "#ef4444").strip()
    if not rel:
        raise HTTPException(400, "缺图片路径")
    src = (LIBRARY_DIR / rel).resolve()
    try:
        src.relative_to(LIBRARY_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "路径不在图库内")
    if not src.exists():
        raise HTTPException(404, "图片不存在")

    rgb = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    im = Image.open(src).convert("RGB")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if r < 120 and g < 120 and b < 120:      # 近黑线条
                px[x, y] = rgb
    out_dir = src.parent
    name = f"{src.stem}_{color.lstrip('#')}_{int(_t.time()) % 100000}{src.suffix}"
    dst = out_dir / name
    im.save(dst, "PNG", optimize=True)
    return {"ok": True, "path": dst.relative_to(LIBRARY_DIR).as_posix()}


@router.post("/stitch")
def stitch(payload: dict):
    """把多张图拼成一张（竖拼 / 横拼），返回新图相对路径，供画布里的「拼接」用。"""
    import time as _t
    from PIL import Image

    paths = [str(p or "").strip().replace("\\", "/") for p in (payload.get("paths") or [])]
    paths = [p for p in paths if p]
    if len(paths) < 2:
        raise HTTPException(400, "至少要两张图才能拼接")
    direction = (payload.get("direction") or "v").lower()
    if direction not in ("v", "h"):
        direction = "v"
    try:
        gap = max(0, min(200, int(payload.get("gap") if payload.get("gap") is not None else 12)))
    except (TypeError, ValueError):
        gap = 12

    imgs = []
    for rel in paths:
        src = (LIBRARY_DIR / rel).resolve()
        try:
            src.relative_to(LIBRARY_DIR.resolve())
        except ValueError:
            raise HTTPException(400, "路径不在图库内")
        if not src.exists():
            raise HTTPException(404, f"图片不存在：{rel}")
        imgs.append(Image.open(src).convert("RGB"))

    if direction == "v":
        w = max(im.width for im in imgs)
        scaled, total_h = [], gap * (len(imgs) - 1)
        for im in imgs:
            if im.width != w:
                im = im.resize((w, max(1, round(im.height * w / im.width))), Image.LANCZOS)
            scaled.append(im)
            total_h += im.height
        out = Image.new("RGB", (w, total_h), "white")
        y = 0
        for im in scaled:
            out.paste(im, (0, y))
            y += im.height + gap
    else:
        h = max(im.height for im in imgs)
        scaled, total_w = [], gap * (len(imgs) - 1)
        for im in imgs:
            if im.height != h:
                im = im.resize((max(1, round(im.width * h / im.height)), h), Image.LANCZOS)
            scaled.append(im)
            total_w += im.width
        out = Image.new("RGB", (total_w, h), "white")
        x = 0
        for im in scaled:
            out.paste(im, (x, 0))
            x += im.width + gap

    out_dir = LIBRARY_DIR / "stitch" / _t.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"stitch_{len(paths)}{direction}_{int(_t.time() * 1000) % 1000000}.png"
    out.save(dst, "PNG", optimize=True)
    return {"ok": True, "path": dst.relative_to(LIBRARY_DIR).as_posix(),
            "width": out.width, "height": out.height, "count": len(paths)}
