from urllib.parse import quote

import logging
import re
import threading
import time
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import Paper, PaperQuestion
from ..schemas import AiPaperBuildIn, AiPaperIn, MergePapersIn, PaperBrief, PaperGenerate, PaperOut
from ..services import ai
from ..services import exam_render
from ..services import paper_builder
from ..services import paper_doc
from ..services import pdf_render
from . import exam

router = APIRouter(prefix="/api/papers", tags=["papers"])
log = logging.getLogger(__name__)


def _export_v2(paper, part: str, title: str, header: str, footer: str):
    """paperdoc/v2 导出；任何一步不过就返回 (None, warnings, "v1") 让调用方回退旧渲染。"""
    from ..services.paperdoc import adapter
    from ..services.paperdoc import render as v2_render

    warnings: list[str] = []
    try:
        doc, warns, errors = adapter.build_and_check(
            paper, title=title, header=header, footer=footer)
    except Exception as exc:                              # noqa: BLE001
        log.warning("paperdoc 适配失败，回退旧渲染：%s", exc)
        return None, [f"适配崩溃：{exc}"], "v1"
    warnings.extend(warns)
    if errors:
        log.warning("paperdoc 校验不过，回退旧渲染：%s", errors[:5])
        warnings.append("校验：" + "；".join(errors[:5]))
        return None, warnings, "v1"
    try:
        return v2_render.export_pdf(doc, part), warnings, "v2"
    except Exception as exc:                              # noqa: BLE001
        log.warning("paperdoc 渲染失败，回退旧渲染：%s", exc)
        warnings.append(f"渲染失败：{exc}")
        return None, warnings, "v1"


def _snapshot_item(q: dict, score: float | None = None) -> dict:
    return {
        "stem": q.get("stem", ""),
        "options": q.get("options") or [],
        "answer": q.get("answer", ""),
        "analysis": q.get("analysis", ""),
        "q_type": q.get("q_type") or "综合",
        "difficulty": q.get("difficulty") or 3,
        "knowledge_points": q.get("knowledge_points") or [],
        "images": [],
        "layout": q.get("layout") or None,
        "source_book_title": q.get("source_book_title") or "AI 生成",
        "unit_name": "",
        "_score": score,
    }


def _create_paper(db: Session, *, title: str, items: list[tuple[dict, float]], config: dict,
                  subject: str = "化学", grade: int = 12, semester: str = "全",
                  duration_minutes: int = 90) -> Paper:
    clean = []
    for snap, score in items:
        s = round(float(score or 0), 1)
        if s <= 0:
            s = 10.0
        clean.append((snap, s))
    total = round(sum(s for _snap, s in clean), 1) or 100
    paper = Paper(title=title[:200], subject=subject or "化学", grade=int(grade or 12),
                  semester=semester or "全", total_score=total,
                  duration_minutes=int(duration_minutes or 90), include_extension=False,
                  config=config)
    db.add(paper)
    db.flush()
    for i, (snap, score) in enumerate(clean, 1):
        db.add(PaperQuestion(paper_id=paper.id, question_id=None, seq=i, score=score,
                             snapshot={k: v for k, v in snap.items() if k != "_score"}))
    db.flush()
    return paper


def _shift_number(text: str, offset: int) -> str:
    """把大题号整体后移：1-2 → 2-2、第1题 → 第2题（只动大题号，不动小问后缀）。"""
    if not offset or not text:
        return text

    def lead(m):
        return f"{m.group(1)}{int(m.group(2)) + offset}"

    out = re.sub(r"(?m)^(\s*)(\d+)(?=\s*[-－—.．、])", lead, text)
    out = re.sub(r"第(\d+)题", lambda m: f"第{int(m.group(1)) + offset}题", out)
    return out



@router.post("", response_model=PaperOut)
def generate(cfg: PaperGenerate, db: Session = Depends(get_db)):
    try:
        paper = paper_builder.generate_paper(db, cfg)
    except paper_builder.NotEnoughQuestions as e:
        raise HTTPException(422, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    db.refresh(paper)
    return paper


@router.post("/ai", response_model=PaperOut)
def generate_ai(body: AiPaperIn, db: Session = Depends(get_db)):
    """AI 按素材/文献直接命题，结果只进试卷，不写题库。"""
    source = (body.source or "").strip()
    doc_title = ""
    if not source and body.doc_id:
        source = ai.pdf_to_text(ai._doc_file(body.doc_id))
        doc_title = "文献"
    if not source:
        raise HTTPException(400, "请提供命题素材（题干文字或选择一份 PDF）")
    try:
        gen = ai.generate_questions(db, source, body.count, body.q_type, body.difficulty)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"AI 命题失败：{exc}") from exc
    items = [(_snapshot_item(q, q.get("score")), float(q.get("score") or 10)) for q in gen["questions"]]
    title = (body.title or "").strip() or f"AI 命题 · {doc_title or '素材'} · {len(items)} 题"
    paper = _create_paper(db, title=title, items=items,
                          config={"source": "ai", "count": len(items), "model": gen.get("model")},
                          subject=body.subject, grade=body.grade, semester=body.semester,
                          duration_minutes=body.duration_minutes)
    db.commit()
    db.refresh(paper)
    return paper


@router.post("/ai/preview")
def preview_ai(body: AiPaperIn, db: Session = Depends(get_db)) -> dict:
    """分步出题第一步：只生成题目快照，不建卷、不进题库。"""
    source = (body.source or "").strip()
    if not source and body.doc_id:
        source = ai.pdf_to_text(ai._doc_file(body.doc_id))
    if not source:
        raise HTTPException(400, "请提供命题素材（题干文字或选择一份 PDF）")
    try:
        gen = ai.generate_questions(db, source, body.count, body.q_type, body.difficulty)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"AI 命题失败：{exc}") from exc
    items = []
    for q in gen["questions"]:
        score = float(q.get("score") or 10)
        items.append({"snapshot": _snapshot_item(q, score), "score": score})
    title = (body.title or "").strip() or f"AI 命题 · {len(items)} 题"
    return {"ok": True, "title": title, "items": items, "model": gen.get("model"),
            "usage": gen.get("usage")}


_AI_JOBS: dict[str, dict] = {}
_AI_JOBS_LOCK = threading.Lock()


def _job_cleanup() -> None:
    now = time.time()
    with _AI_JOBS_LOCK:
        for jid, job in list(_AI_JOBS.items()):
            if now - job.get("created", now) > 3600:
                _AI_JOBS.pop(jid, None)


@router.post("/ai/jobs")
def start_ai_job(body: AiPaperIn) -> dict:
    """后台生成 AI 题卷：切走页面也不会中断，回来可继续取结果。"""
    _job_cleanup()
    source = (body.source or "").strip()
    doc_id = body.doc_id
    if not source and not doc_id:
        raise HTTPException(400, "请提供命题素材（题干文字或选择一份 PDF）")
    jid = uuid.uuid4().hex[:16]
    with _AI_JOBS_LOCK:
        _AI_JOBS[jid] = {"status": "running", "created": time.time(), "items": [],
                         "title": (body.title or "").strip(), "error": "", "model": ""}

    def run() -> None:
        db = SessionLocal()
        try:
            src = source
            if not src and doc_id:
                src = ai.pdf_to_text(ai._doc_file(doc_id))
            gen = ai.generate_questions(db, src, body.count, body.q_type, body.difficulty)
            items = []
            for q in gen["questions"]:
                score = float(q.get("score") or 10)
                items.append({"snapshot": _snapshot_item(q, score), "score": score})
            title = (body.title or "").strip() or f"AI 命题 · {len(items)} 题"
            with _AI_JOBS_LOCK:
                job = _AI_JOBS.get(jid)
                if job is not None:
                    job.update({"status": "done", "items": items, "title": title,
                                "model": gen.get("model") or ""})
        except Exception as exc:  # noqa: BLE001
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            with _AI_JOBS_LOCK:
                job = _AI_JOBS.get(jid)
                if job is not None:
                    job.update({"status": "error", "error": detail or "AI 命题失败"})
        finally:
            db.close()

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "job_id": jid}


@router.get("/ai/jobs/{job_id}")
def get_ai_job(job_id: str) -> dict:
    with _AI_JOBS_LOCK:
        job = dict(_AI_JOBS.get(job_id) or {})
    if not job:
        raise HTTPException(404, "任务不存在或已过期")
    return {"job_id": job_id, "status": job["status"], "title": job.get("title") or "",
            "items": job.get("items") or [], "error": job.get("error") or "",
            "model": job.get("model") or ""}


@router.post("/ai/build", response_model=PaperOut)
def build_ai(body: AiPaperBuildIn, db: Session = Depends(get_db)):
    """分步出题最后一步：把逐题（可人工改过）的快照建成一套试卷。"""
    pairs: list[tuple[dict, float]] = []
    for raw in body.items:
        snap = raw.get("snapshot") if isinstance(raw.get("snapshot"), dict) else raw
        if not isinstance(snap, dict) or not str(snap.get("stem") or "").strip():
            continue
        pairs.append((snap, float(raw.get("score") or snap.get("score") or 10)))
    if not pairs:
        raise HTTPException(400, "没有可成卷的题目")
    title = (body.title or "").strip() or f"AI 命题 · {len(pairs)} 题"
    paper = _create_paper(db, title=title, items=pairs,
                          config={"source": "ai", "count": len(pairs)},
                          subject=body.subject, grade=body.grade, semester=body.semester,
                          duration_minutes=body.duration_minutes)
    db.commit()
    db.refresh(paper)
    return paper


@router.post("/merge", response_model=PaperOut)
def merge(body: MergePapersIn, db: Session = Depends(get_db)):
    """把多套试卷合成一套，并自动顺延大题号（小问号不变）。"""
    papers = []
    for pid in body.paper_ids:
        p = db.get(Paper, pid)
        if p is None:
            raise HTTPException(404, f"试卷 #{pid} 不存在")
        papers.append(p)
    items: list[tuple[dict, float]] = []
    offset = 0
    for p in papers:
        for it in p.items:
            snap = dict(it.snapshot or {})
            if offset:
                for key in ("stem", "answer", "analysis"):
                    snap[key] = _shift_number(snap.get(key) or "", offset)
            items.append((snap, it.score))
        offset += max(1, len(p.items))
    title = (body.title or "").strip() or " + ".join(p.title for p in papers) + "（融合卷）"
    first = papers[0]
    paper = _create_paper(db, title=title, items=items,
                          config={"source": "merged", "from": [p.id for p in papers]},
                          subject=first.subject, grade=first.grade, semester=first.semester,
                          duration_minutes=max(p.duration_minutes or 90 for p in papers))
    db.commit()
    db.refresh(paper)
    return paper


@router.get("", response_model=list[PaperBrief])
def list_papers(include_drafts: int = 0, db: Session = Depends(get_db)):
    papers = db.scalars(select(Paper).order_by(Paper.id.desc())).all()
    out = []
    for p in papers:
        n = db.scalar(select(func.count()).select_from(PaperQuestion).where(PaperQuestion.paper_id == p.id))
        cfg = p.config or {}
        kind = str(cfg.get("kind") or "")
        status = exam.paper_status(p) if kind == "manual" else ""
        # 人工卷没编辑完 = 草稿，留在「出整卷」里，不进试卷列表
        if kind == "manual" and status == "draft" and not include_drafts:
            continue
        out.append(PaperBrief(
            id=p.id, title=p.title, subject=p.subject, grade=p.grade, semester=p.semester,
            total_score=p.total_score, duration_minutes=p.duration_minutes,
            question_count=n, created_at=p.created_at,
            kind=kind, source=str(cfg.get("source") or ""), status=status))
    return out


@router.get("/{paper_id}", response_model=PaperOut)
def get_paper(paper_id: int, db: Session = Depends(get_db)):
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    return paper


@router.patch("/{paper_id}", response_model=PaperOut)
def rename_paper(paper_id: int, body: dict, db: Session = Depends(get_db)):
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    if body.get("title"):
        paper.title = body["title"]
    db.commit()
    db.refresh(paper)
    return paper


@router.delete("/{paper_id}")
def delete_paper(paper_id: int, db: Session = Depends(get_db)):
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    db.delete(paper)
    db.commit()
    return {"ok": True}


@router.post("/{paper_id}/replace", response_model=PaperOut)
def replace_question(paper_id: int, body: dict, db: Session = Depends(get_db)):
    pq_id = body.get("paper_question_id")
    if not pq_id:
        raise HTTPException(400, "缺少 paper_question_id")
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    try:
        new_item = paper_builder.replace_question(db, paper, pq_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    if new_item is None:
        raise HTTPException(422, "该题型已没有可替换的新题，请缩小范围或先补题库")
    db.refresh(paper)
    return paper


@router.patch("/{paper_id}/items", response_model=PaperOut)
def update_paper_items(paper_id: int, body: dict, db: Session = Depends(get_db)):
    """打印视图里直接改标题/题干/答案后保存。"""
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    title = str(body.get("title") or "").strip()
    if title:
        paper.title = title[:200]
    by_id = {it.id: it for it in paper.items}
    for raw in body.get("items") or []:
        try:
            pq = by_id.get(int(raw.get("id")))
        except (TypeError, ValueError):
            continue
        if pq is None:
            continue
        snap = dict(pq.snapshot or {})
        for key in ("stem", "answer", "analysis"):
            if key in raw and isinstance(raw[key], str):
                snap[key] = raw[key]
        pq.snapshot = snap
    db.commit()
    db.refresh(paper)
    return paper


@router.get("/{paper_id}/view", response_class=HTMLResponse)
def view_paper(paper_id: int, part: str = "all", db: Session = Depends(get_db)):
    """网页打印视图：可在页面上直接改题干/答案，再打印或另存 PDF。"""
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    if part not in ("all", "questions", "answers"):
        part = "all"
    if (paper.config or {}).get("kind") == "manual":
        doc = exam.doc_from_paper(paper)
        return HTMLResponse(exam_render.build_doc_html(doc, "questions" if part == "all" else part))
    return HTMLResponse(pdf_render.render_paper_html(db, paper, part))


@router.get("/{paper_id}/export.pdf")
def export_paper_pdf(paper_id: int, part: str = "questions", title: str = "",
                     header: str = "", footer: str = "", save: int = 0,
                     render: str = "", request: Request = None,
                     db: Session = Depends(get_db)):
    """统一导出：任何来源的卷子（人工 / 题库选题 / 画布 / 单题模式）都走这一条。

    title / header / footer 是导出时临时覆盖的标题与页眉页脚；save=1 时顺手记进
    paper.config.exam.export，下次打开还是上次填的。

    默认走新的 paperdoc/v2 渲染器；校验或渲染不过自动回退旧渲染，并在
    X-Paperdoc-Mode 里写清用的是哪套。render=v1 可强制回旧渲染（并存期回滚用）。
    """
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    if part not in ("questions", "answers"):
        part = "questions"

    if save:
        cfg = dict(paper.config or {})
        exam_cfg = dict(cfg.get("exam") or {})
        exam_cfg["export"] = {"title": (title or "").strip(),
                              "header": (header or "").strip(),
                              "footer": (footer or "").strip()}
        cfg["exam"] = exam_cfg
        paper.config = cfg
        db.commit()

    base = str(request.base_url).rstrip("/") if request is not None else None
    pdf = None
    warnings: list[str] = []
    mode = "v1"
    choice = str(render or "").strip().lower()
    if choice not in ("v1", "legacy", "old"):
        pdf, warnings, mode = _export_v2(paper, part, title, header, footer)
    if pdf is None:
        doc = paper_doc.build_doc(paper, title=title, header=header, footer=footer)
        try:
            pdf = exam_render.export_pdf(doc, part, base)
        except RuntimeError as e:
            raise HTTPException(501, str(e))

    # 人工整卷导出过就算「出完了」，和出整卷页面的行为保持一致
    if (paper.config or {}).get("kind") == "manual":
        try:
            exam._set_status(db, paper, "done")
        except Exception:                                  # noqa: BLE001
            pass

    label = "题目卷" if part == "questions" else "答案卷"
    ttl = (title or "").strip() or str(paper.title or "") or "试卷"
    filename = f"{ttl}_{label}.pdf"
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
               "X-Paperdoc-Mode": mode}
    if warnings:
        headers["X-Paperdoc-Warnings"] = quote("；".join(warnings)[:600])
    return Response(pdf, media_type="application/pdf", headers=headers)


@router.post("/{paper_id}/export.pdf")
def export_paper_pdf_post(paper_id: int, payload: dict = Body(default={}),
                          request: Request = None, db: Session = Depends(get_db)):
    """POST 版导出：标题/页眉页脚放 JSON body。

    走查询串传中文在这台机器上会被解码成乱码（PDF 里标题变方块），
    统一改成 JSON body，前端只用这一条。
    """
    p = payload or {}
    return export_paper_pdf(
        paper_id,
        part=str(p.get("part") or "questions"),
        title=str(p.get("title") or ""),
        header=str(p.get("header") or ""),
        footer=str(p.get("footer") or ""),
        save=int(p.get("save") or 0),
        render=str(p.get("render") or ""),
        request=request,
        db=db,
    )


@router.get("/{paper_id}/export")
def export_paper(paper_id: int, part: str = "all", request: Request = None,
                 db: Session = Depends(get_db)):
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    if part not in ("all", "questions", "answers"):
        part = "all"
    try:
        if (paper.config or {}).get("kind") == "manual":
            doc = exam.doc_from_paper(paper)
            base = str(request.base_url).rstrip("/") if request is not None else None
            pdf = exam_render.export_pdf(doc, part, base)
        else:
            pdf = pdf_render.export_pdf(db, paper, part)
    except RuntimeError as e:
        raise HTTPException(501, str(e))
    suffix = {"questions": "_题目卷", "answers": "_答案卷"}.get(part, "")
    filename = paper.title.replace(" ", "_") + suffix + ".pdf"
    return Response(
        pdf, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})
