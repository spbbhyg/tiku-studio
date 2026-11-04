"""v2 出题接口：题组 — 小问（含化学对象与素材引用）。

约定
- 题干/答案/评分细则都是 Tiptap JSON（doc），允许空 doc 做草稿。
- 化学内容不塞进 JSON：节点形如 {"type":"chem","attrs":{"id":12}}，实体在 /api/v2/chem。
- 小问自动编号：seq 递增，label 为空时前端显示 (seq)。
"""
from __future__ import annotations

import json
import time
from typing import Any

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import LIBRARY_DIR
from ..db import get_db
from ..models import utcnow
from ..services import v2_export
from ..models_v2 import (BasketItem, ChemObject, EntityVersion, Material, PaperTemplate,
                         QuestionGroup, SubQuestion, STATUS_FLOW)

router = APIRouter(prefix="/api/v2", tags=["v2"])

def _doc_text(doc) -> str:
    """Tiptap JSON → 纯文本（用于列表预览与搜索）。"""
    if not doc:
        return ""
    parts: list[str] = []

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "text" and n.get("text"):
                parts.append(str(n["text"]))
            elif n.get("type") in ("mathInline", "chemInline"):
                attrs = n.get("attrs") or {}
                parts.append(attrs.get("latex") or ("[结构式]" if n.get("type") == "chemInline" else ""))
            for v in n.get("content") or []:
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(doc)
    return " ".join(p for p in parts if p).strip()


EMPTY_DOC = {"type": "doc", "content": [{"type": "paragraph"}]}


def doc_or_empty(v: Any) -> dict:
    return v if isinstance(v, dict) and v.get("type") == "doc" else dict(EMPTY_DOC)


def text_doc(text: str) -> dict:
    """把纯文本按空行/换行拆成段落，生成最小 Tiptap doc（批量导入用）。"""
    text = (text or "").strip()
    if not text:
        return dict(EMPTY_DOC)
    paras = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": ln}]} for ln in paras]}


# ---------- schemas ----------
class ChemIn(BaseModel):
    kind: str = "formula"                 # formula | structure | reaction
    latex: str | None = None
    smiles: str | None = None
    molfile: str | None = None
    svg: str | None = None
    renderer: str | None = None


class SubIn(BaseModel):
    stem: dict | None = None
    answer: dict | None = None
    rubric: dict | None = None
    score: float = 0
    q_type: str = "简答"
    difficulty: int = 2
    tags: list[str] = Field(default_factory=list)
    material_ids: list[int] = Field(default_factory=list)
    status: str | None = None
    label: str | None = None
    source: dict | None = None


class GroupIn(BaseModel):
    book_id: int | None = None
    title: str | None = None
    stem: dict | None = None
    source_title: str | None = None
    source_citation: str | None = None
    locator: str | None = None
    tags: list[str] = Field(default_factory=list)
    material_ids: list[int] = Field(default_factory=list)
    status: str | None = None
    meta: dict | None = None


def _group_brief(g: QuestionGroup) -> dict:
    return {
        "id": g.id, "title": g.title, "book_id": g.book_id,
        "stem": g.stem or EMPTY_DOC, "source_title": g.source_title,
        "source_citation": g.source_citation, "locator": g.locator,
        "tags": g.tags or [], "status": g.status, "version": g.version,
        "material_ids": g.material_ids or [], "meta": g.meta or {},
        "sub_count": len(g.subquestions),
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "updated_at": g.updated_at.isoformat() if g.updated_at else None,
    }


def _relabel(db: Session, group_id: int) -> None:
    """按 seq 顺序把小问 label 重排为 (1)(2)(3)…（用户手填的 label 会被覆盖）。"""
    rows = db.scalars(select(SubQuestion).where(SubQuestion.group_id == group_id)
                      .order_by(SubQuestion.seq)).all()
    for i, r in enumerate(rows, start=1):
        r.label = f"({i})"
    db.flush()


def _sub_brief(s: SubQuestion) -> dict:
    return {
        "id": s.id, "group_id": s.group_id, "seq": s.seq, "label": s.label,
        "stem": s.stem or EMPTY_DOC, "answer": s.answer or EMPTY_DOC,
        "rubric": s.rubric or EMPTY_DOC, "score": s.score,
        "q_type": s.q_type, "difficulty": s.difficulty, "tags": s.tags or [],
        "material_ids": s.material_ids or [], "status": s.status,
        "version": s.version, "source": s.source or {},
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def _snapshot(db: Session, entity_type: str, entity_id: int, data: dict, note: str = "") -> None:
    last = db.scalar(select(func.max(EntityVersion.version_no)).where(
        EntityVersion.entity_type == entity_type, EntityVersion.entity_id == entity_id)) or 0
    db.add(EntityVersion(entity_type=entity_type, entity_id=entity_id,
                         version_no=last + 1, note=note, snapshot=data))


def _check_status(v: str | None, default: str = "draft") -> str:
    v = (v or default).strip()
    if v not in STATUS_FLOW:
        raise HTTPException(400, f"状态只能是 {' / '.join(STATUS_FLOW)}")
    return v


# ---------- 化学对象 ----------
# ---------- 结构式渲染（RDKit 服务端） ----------
def _render_structure(src: str, width: int = 240, height: int = 170) -> tuple[str, str]:
    """把 SMILES 或 MOL(V2000/V3000) 渲染成 SVG，返回 (svg, canonical_smiles)。"""
    src = (src or "").strip()
    if not src:
        raise HTTPException(400, "结构式内容为空")
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
        from rdkit.Chem.Draw import rdMolDraw2D
    except Exception:  # pragma: no cover
        raise HTTPException(501, "服务端缺少 RDKit，无法渲染结构式")

    is_mol = "M  END" in src or src.startswith("\n") or " V2000" in src or " V3000" in src
    if is_mol:
        mol = Chem.MolFromMolBlock(src, sanitize=True, removeHs=False)
    else:
        mol = Chem.MolFromSmiles(src)
    if mol is None:
        raise HTTPException(400, "无法解析这个结构式，检查 SMILES / MOL 是否完整")

    AllChem.Compute2DCoords(mol)          # 相当于 Clean Up：重算 2D 坐标
    drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
    opts = drawer.drawOptions()
    opts.clearBackground = False
    opts.bondLineWidth = 2
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
    drawer.FinishDrawing()
    svg = drawer.GetDrawingText()
    try:
        can = Chem.MolToSmiles(mol)
    except Exception:
        can = ""
    return svg, can


class ImportSubIn(BaseModel):
    text: str = ""
    score: float = 0
    answer: str = ""


class ImportGroupIn(BaseModel):
    title: str = ""
    stem: str = ""
    source_title: str = ""
    locator: str = ""
    tags: list[str] = Field(default_factory=list)
    subs: list[ImportSubIn] = Field(default_factory=list)


class ImportIn(BaseModel):
    items: list[ImportGroupIn] = Field(default_factory=list)
    book_id: int | None = None


class ChemRenderIn(BaseModel):
    source: str
    width: int = 240
    height: int = 170


@router.post("/chem/preview")
def chem_preview(body: ChemRenderIn):
    svg, can = _render_structure(body.source, body.width, body.height)
    return {"svg": svg, "smiles": can}


@router.post("/chem")
def create_chem(body: ChemIn, db: Session = Depends(get_db)):
    if not (body.latex or body.smiles or body.molfile or body.svg):
        raise HTTPException(400, "公式要给 latex，结构式要给 smiles/molfile 或 svg")
    svg = body.svg
    renderer = body.renderer
    smiles = body.smiles
    if body.kind == "structure" and not svg:
        # 没带 svg 就用 RDKit 在服务端画（SMILES 或 MOL 都行）
        svg, can = _render_structure(body.smiles or body.molfile or "")
        renderer = "rdkit"
        smiles = smiles or can
    obj = ChemObject(kind=body.kind, latex=body.latex, smiles=smiles,
                     molfile=body.molfile, svg=svg, renderer=renderer,
                     cleaned_at=utcnow() if body.molfile else None)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return {"id": obj.id, "kind": obj.kind, "renderer": obj.renderer}


@router.post("/chem/{chem_id}/png")
def chem_to_png(chem_id: int, width: int = 900, db: Session = Depends(get_db)):
    """把结构式/公式的 SVG 转成 PNG 存进图库，返回可插入画布的绝对路径。"""
    obj = db.get(ChemObject, chem_id)
    if obj is None:
        raise HTTPException(404, "化学对象不存在")
    svg = (obj.svg or "").strip()
    if not svg:
        raise HTTPException(400, "这个化学对象没有图形，无法出图")
    if not svg.lstrip().lower().startswith("<svg") and "<svg" in svg.lower():
        i = svg.lower().index("<svg")
        svg = svg[i:]
    try:
        import pymupdf as fitz  # 11.x 推荐写法
    except Exception:  # noqa: BLE001
        import fitz  # type: ignore
    try:
        doc = fitz.open(stream=svg.encode("utf-8"), filetype="svg")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"SVG 解析失败：{exc}") from exc
    page = doc[0]
    w = float(page.rect.width) or 1.0
    zoom = max(0.5, min(8.0, width / w))
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    folder = LIBRARY_DIR / "chem"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"chem-{chem_id}-{int(time.time() * 1000)}.png"
    out = folder / name
    pix.save(str(out))
    return {"path": str(out), "width": pix.width, "height": pix.height,
            "url": f"/api/images/chem/{name}"}


@router.get("/chem/{chem_id}")
def get_chem(chem_id: int, db: Session = Depends(get_db)):
    obj = db.get(ChemObject, chem_id)
    if obj is None:
        raise HTTPException(404, "化学对象不存在")
    return {"id": obj.id, "kind": obj.kind, "latex": obj.latex, "smiles": obj.smiles,
            "molfile": obj.molfile, "svg": obj.svg, "renderer": obj.renderer,
            "cleaned_at": obj.cleaned_at.isoformat() if obj.cleaned_at else None}



def _resequence(db: Session, rows: list[SubQuestion], ordered_ids: list[int]) -> None:
    """先挪到负区间再写回，避开 UNIQUE(group_id, seq) 的换位冲突。"""
    by_id = {s.id: s for s in rows}
    for s in rows:
        s.seq = -abs(s.seq or 1) - 1000
    db.flush()
    for i, sid in enumerate(ordered_ids, start=1):
        item = by_id.get(sid)
        if item is None:
            raise HTTPException(400, f"小问 {sid} 不属于该题组")
        item.seq = i
    db.flush()

# ---------- 题组 ----------
@router.get("/groups")
def list_groups(q: str = "", status: str = "", tag: str = "", limit: int = 50,
                db: Session = Depends(get_db)):
    stmt = select(QuestionGroup).order_by(QuestionGroup.updated_at.desc()).limit(min(limit, 200))
    if status:
        stmt = stmt.where(QuestionGroup.status == status)
    rows = db.scalars(stmt).all()
    out = [_group_brief(g) for g in rows]
    if q:
        ql = q.lower()
        out = [g for g in out if ql in json.dumps(g, ensure_ascii=False).lower()]
    if tag:
        out = [g for g in out if tag in (g["tags"] or [])]
    return {"items": out, "total": len(out)}


@router.post("/groups")
def create_group(body: GroupIn, db: Session = Depends(get_db)):
    g = QuestionGroup(
        book_id=body.book_id, title=body.title, stem=doc_or_empty(body.stem),
        source_title=body.source_title, source_citation=body.source_citation,
        locator=body.locator, tags=body.tags or [], material_ids=body.material_ids or [],
        status=_check_status(body.status), meta=body.meta or {},
    )
    db.add(g)
    db.flush()
    # 新建题组默认带一个小问，方便“新建即写正文”
    db.add(SubQuestion(group_id=g.id, seq=1, stem=doc_or_empty(None),
                       answer=doc_or_empty(None), rubric=doc_or_empty(None)))
    db.commit()
    db.refresh(g)
    return _group_detail(g)


@router.post("/groups/import")
def import_groups(body: ImportIn, db: Session = Depends(get_db)):
    """批量导入：每条 = 一个题组（可选若干小问），纯文本自动转 Tiptap 段落。"""
    if not body.items:
        raise HTTPException(400, "没有可导入的题目")
    created = []
    for it in body.items:
        g = QuestionGroup(
            book_id=body.book_id, title=(it.title or "").strip() or "未命名题组",
            stem=text_doc(it.stem), source_title=it.source_title, locator=it.locator,
            tags=it.tags or [], status="draft",
        )
        db.add(g)
        db.flush()
        subs = it.subs or [ImportSubIn(text="")]
        for i, sub in enumerate(subs, start=1):
            db.add(SubQuestion(
                group_id=g.id, seq=i, label=f"({i})", stem=text_doc(sub.text),
                answer=text_doc(sub.answer), rubric=dict(EMPTY_DOC), score=sub.score or 0,
            ))
        _relabel(db, g.id)
        created.append({"id": g.id, "title": g.title, "subs": len(subs)})
    db.commit()
    return {"ok": True, "count": len(created), "items": created}


@router.get("/groups/{group_id}")
def get_group(group_id: int, db: Session = Depends(get_db)):
    g = db.get(QuestionGroup, group_id)
    if g is None:
        raise HTTPException(404, "题组不存在")
    return _group_detail(g)


def _group_detail(g: QuestionGroup) -> dict:
    data = _group_brief(g)
    data["subquestions"] = [_sub_brief(s) for s in g.subquestions]
    return data


@router.patch("/groups/{group_id}")
def patch_group(group_id: int, body: GroupIn, db: Session = Depends(get_db)):
    g = db.get(QuestionGroup, group_id)
    if g is None:
        raise HTTPException(404, "题组不存在")
    _snapshot(db, "group", g.id, _group_detail(g), note="编辑前")
    data = body.model_dump(exclude_unset=True)
    if "status" in data:
        g.status = _check_status(data.pop("status"), g.status)
    for k in ("book_id", "title", "source_title", "source_citation", "locator", "tags", "material_ids", "meta"):
        if k in data:
            setattr(g, k, data[k])
    if "stem" in data:
        g.stem = doc_or_empty(data["stem"])
    g.version = (g.version or 1) + 1
    db.commit()
    db.refresh(g)
    return _group_detail(g)


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, db: Session = Depends(get_db)):
    g = db.get(QuestionGroup, group_id)
    if g is None:
        raise HTTPException(404, "题组不存在")
    db.delete(g)
    db.commit()
    return {"ok": True}


# ---------- 小问 ----------
@router.post("/groups/{group_id}/subquestions")
def add_subquestion(group_id: int, body: SubIn, db: Session = Depends(get_db)):
    g = db.get(QuestionGroup, group_id)
    if g is None:
        raise HTTPException(404, "题组不存在")
    seq = (db.scalar(select(func.max(SubQuestion.seq)).where(SubQuestion.group_id == group_id)) or 0) + 1
    s = SubQuestion(
        group_id=group_id, seq=seq, label=body.label,
        stem=doc_or_empty(body.stem), answer=doc_or_empty(body.answer), rubric=doc_or_empty(body.rubric),
        score=body.score, q_type=body.q_type, difficulty=body.difficulty,
        tags=body.tags or [], material_ids=body.material_ids or [],
        status=_check_status(body.status), source=body.source or {},
    )
    db.add(s)
    db.flush()
    _relabel(db, group_id)
    db.commit()
    db.refresh(s)
    return _sub_brief(s)


@router.get("/subquestions/{sub_id}")
def get_subquestion(sub_id: int, db: Session = Depends(get_db)):
    s = db.get(SubQuestion, sub_id)
    if s is None:
        raise HTTPException(404, "小问不存在")
    data = _sub_brief(s)
    data["group"] = {"id": s.group.id, "title": s.group.title, "stem": s.group.stem}
    return data


@router.patch("/subquestions/{sub_id}")
def patch_subquestion(sub_id: int, body: SubIn, db: Session = Depends(get_db)):
    s = db.get(SubQuestion, sub_id)
    if s is None:
        raise HTTPException(404, "小问不存在")
    _snapshot(db, "subquestion", s.id, _sub_brief(s), note="编辑前")
    data = body.model_dump(exclude_unset=True)
    if "status" in data:
        s.status = _check_status(data.pop("status"), s.status)
    for k in ("label", "score", "q_type", "difficulty", "tags", "material_ids", "source"):
        if k in data:
            setattr(s, k, data[k])
    for k in ("stem", "answer", "rubric"):
        if k in data:
            setattr(s, k, doc_or_empty(data[k]))
    s.version = (s.version or 1) + 1
    db.commit()
    db.refresh(s)
    return _sub_brief(s)


@router.delete("/subquestions/{sub_id}")
def delete_subquestion(sub_id: int, db: Session = Depends(get_db)):
    s = db.get(SubQuestion, sub_id)
    if s is None:
        raise HTTPException(404, "小问不存在")
    gid = s.group_id
    db.delete(s)
    db.flush()
    rest = db.scalars(select(SubQuestion).where(SubQuestion.group_id == gid)
                      .order_by(SubQuestion.seq)).all()
    _resequence(db, rest, [x.id for x in rest])
    _relabel(db, gid)
    db.commit()
    return {"ok": True, "renumbered": len(rest)}


class ReorderIn(BaseModel):
    ids: list[int] = Field(default_factory=list)


@router.post("/groups/{group_id}/subquestions/reorder")
def reorder(group_id: int, body: ReorderIn, db: Session = Depends(get_db)):
    rows = db.scalars(select(SubQuestion).where(SubQuestion.group_id == group_id)).all()
    if set(body.ids) != {s.id for s in rows}:
        raise HTTPException(400, "提交的小问集合与该题组不一致")
    _resequence(db, rows, body.ids)
    _relabel(db, group_id)
    db.commit()
    return {"ok": True, "n": len(body.ids)}


class StatusIn(BaseModel):
    status: str
    note: str | None = None


@router.post("/subquestions/{sub_id}/status")
def set_status(sub_id: int, body: StatusIn, db: Session = Depends(get_db)):
    s = db.get(SubQuestion, sub_id)
    if s is None:
        raise HTTPException(404, "小问不存在")
    new = _check_status(body.status)
    _snapshot(db, "subquestion", s.id, _sub_brief(s), note=f"状态 {s.status} → {new}")
    s.status = new
    s.version = (s.version or 1) + 1
    db.commit()
    return {"ok": True, "id": s.id, "status": s.status, "version": s.version}


# ---------- 版本 ----------
@router.get("/subquestions/{sub_id}/versions")
def list_versions(sub_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(EntityVersion).where(
        EntityVersion.entity_type == "subquestion", EntityVersion.entity_id == sub_id
    ).order_by(EntityVersion.version_no.desc())).all()
    return {"items": [{"id": v.id, "version_no": v.version_no, "note": v.note,
                       "created_at": v.created_at.isoformat() if v.created_at else None}
                      for v in rows]}


@router.post("/subquestions/{sub_id}/versions/{version_id}/restore")
def restore(sub_id: int, version_id: int, db: Session = Depends(get_db)):
    s = db.get(SubQuestion, sub_id)
    v = db.get(EntityVersion, version_id)
    if s is None or v is None or v.entity_id != sub_id:
        raise HTTPException(404, "小问或版本不存在")
    _snapshot(db, "subquestion", s.id, _sub_brief(s), note="回滚前")
    snap = v.snapshot or {}
    for k in ("stem", "answer", "rubric"):
        if snap.get(k):
            setattr(s, k, doc_or_empty(snap[k]))
    for k in ("score", "q_type", "difficulty", "tags", "material_ids", "label"):
        if k in snap:
            setattr(s, k, snap[k])
    s.version = (s.version or 1) + 1
    db.commit()
    db.refresh(s)
    return _sub_brief(s)


# ---------- 素材 ----------
class MaterialIn(BaseModel):
    kind: str = "image"
    path: str
    width: int | None = None
    height: int | None = None
    caption: str | None = None
    source: str | None = None
    tags: list[str] = Field(default_factory=list)


@router.get("/materials")
def list_materials(q: str = "", limit: int = 100, db: Session = Depends(get_db)):
    rows = db.scalars(select(Material).order_by(Material.id.desc()).limit(min(limit, 300))).all()
    items = [{"id": m.id, "kind": m.kind, "path": m.path, "width": m.width, "height": m.height,
              "caption": m.caption, "tags": m.tags or []} for m in rows]
    if q:
        items = [m for m in items if q.lower() in json.dumps(m, ensure_ascii=False).lower()]
    return {"items": items}


@router.post("/materials")
def create_material(body: MaterialIn, db: Session = Depends(get_db)):
    m = Material(kind=body.kind, path=body.path, width=body.width, height=body.height,
                 caption=body.caption, source=body.source, tags=body.tags or [])
    db.add(m)
    db.commit()
    db.refresh(m)
    return {"id": m.id, "path": m.path}


# ---------- 模板 ----------
class TemplateIn(BaseModel):
    name: str
    spec: dict = Field(default_factory=dict)
    is_default: bool = False


@router.get("/templates")
def list_templates(db: Session = Depends(get_db)):
    rows = db.scalars(select(PaperTemplate).order_by(PaperTemplate.id)).all()
    return {"items": [{"id": t.id, "name": t.name, "spec": t.spec, "is_default": bool(t.is_default)}
                      for t in rows]}


@router.post("/templates")
def create_template(body: TemplateIn, db: Session = Depends(get_db)):
    """同名模板视为更新（前端「存为模板」重名时覆盖）。"""
    t = db.scalar(select(PaperTemplate).where(PaperTemplate.name == body.name))
    if t is None:
        t = PaperTemplate(name=body.name, spec=body.spec, is_default=1 if body.is_default else 0)
        db.add(t)
        updated = False
    else:
        t.spec = body.spec
        if body.is_default:
            t.is_default = 1
        updated = True
    db.commit()
    db.refresh(t)
    return {"id": t.id, "name": t.name, "updated": updated}


# ---------- 小问检索（组卷篮用） ----------
@router.get("/subquestions")
def list_subquestions(q: str | None = None, group_id: int | None = None,
                      status: str | None = None, limit: int = 200,
                      db: Session = Depends(get_db)):
    stmt = select(SubQuestion).order_by(SubQuestion.group_id, SubQuestion.seq).limit(max(1, min(limit, 500)))
    if group_id:
        stmt = stmt.where(SubQuestion.group_id == group_id)
    if status:
        stmt = stmt.where(SubQuestion.status == status)
    rows = db.scalars(stmt).all()
    groups = {g.id: g for g in db.scalars(select(QuestionGroup)).all()}
    out = []
    for r in rows:
        g = groups.get(r.group_id)
        brief = _sub_brief(r)
        text = _doc_text(brief.get("stem"))
        if q and q.strip():
            needle = q.strip().lower()
            hay = (text + " " + (g.title if g else "") + " " + " ".join(brief.get("tags") or [])).lower()
            if needle not in hay:
                continue
        out.append({
            "id": r.id, "group_id": r.group_id,
            "group_title": (g.title if g else ""),
            "seq": r.seq, "label": r.label or f"({r.seq})",
            "score": r.score or 0, "q_type": r.q_type, "status": r.status,
            "tags": brief.get("tags") or [],
            "text": text[:120],
            "has_chem": bool(brief.get("has_chem")),
        })
    return {"items": out, "count": len(out)}


# ---------- 组卷篮 ----------
class BasketIn(BaseModel):
    items: list[dict] = Field(default_factory=list)   # [{subquestion_id, score?}]


@router.put("/papers/{paper_id}/basket")
def put_basket(paper_id: int, body: BasketIn, db: Session = Depends(get_db)):
    db.query(BasketItem).filter(BasketItem.paper_id == paper_id).delete()
    total = 0.0
    for i, raw in enumerate(body.items, start=1):
        sid = int(raw.get("subquestion_id"))
        s = db.get(SubQuestion, sid)
        if s is None:
            raise HTTPException(400, f"小问 {sid} 不存在")
        score = float(raw.get("score", s.score or 0))
        total += score
        db.add(BasketItem(paper_id=paper_id, subquestion_id=sid, seq=i,
                          number=str(raw.get("number") or f"{i}"),
                          score=score, snapshot=_sub_brief(s)))
    db.commit()
    return {"ok": True, "count": len(body.items), "total_score": total}


@router.get("/papers/{paper_id}/basket")
def get_basket(paper_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(BasketItem).where(BasketItem.paper_id == paper_id)
                      .order_by(BasketItem.seq)).all()
    return {"items": [{"id": b.id, "subquestion_id": b.subquestion_id, "seq": b.seq,
                       "number": b.number, "score": b.score, "snapshot": b.snapshot}
                      for b in rows],
            "total_score": sum(b.score for b in rows)}

# ---------- 导出与检查 ----------
@router.get("/papers/{paper_id}/check")
def check_paper(paper_id: int, db: Session = Depends(get_db)):
    return v2_export.check_basket(db, paper_id)


@router.get("/papers/{paper_id}/export")
def export_paper(paper_id: int, fmt: str = "html", title: str = "试卷",
                 answers: int = 1, scores: int = 1, template_id: int = 0,
                 db: Session = Depends(get_db)):
    tpl_spec = None
    if template_id:
        t = db.get(PaperTemplate, template_id)
        if t is not None:
            tpl_spec = t.spec or {}
            title = (tpl_spec.get("title") or title)
    opts = None if tpl_spec is not None else {"answers": bool(answers), "scores": bool(scores)}
    html = v2_export.basket_html(db, paper_id, title, opts, spec=tpl_spec)
    if fmt == "html":
        return Response(html, media_type="text/html; charset=utf-8")
    if fmt == "pdf":
        data = v2_export.html_to_pdf(html)
        name = (title or "试卷") + ".pdf"
        return Response(data, media_type="application/pdf",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
    raise HTTPException(400, "fmt 只支持 html / pdf")

@router.post("/materials/upload")
def upload_material(file: UploadFile = File(...), caption: str = "", db: Session = Depends(get_db)):
    """上传图片素材到 data/library/materials/，登记 Material 并返回。"""
    import hashlib
    import uuid as _uuid
    from datetime import datetime as _dt

    raw = file.file.read()
    if not raw:
        raise HTTPException(400, "空文件")
    ext = (file.filename or "img.png").rsplit(".", 1)[-1].lower()
    if ext not in ("png", "jpg", "jpeg", "gif", "webp", "svg"):
        raise HTTPException(400, "只支持 png/jpg/gif/webp/svg")
    sub = _dt.now().strftime("%Y-%m")
    folder = LIBRARY_DIR / "materials" / sub
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{_uuid.uuid4().hex[:8]}.{ext}"
    (folder / name).write_bytes(raw)
    rel = f"materials/{sub}/{name}"

    width = height = None
    if ext != "svg":
        try:
            from PIL import Image

            with Image.open(folder / name) as im:
                width, height = im.size
        except Exception:
            pass
    m = Material(kind="image", path=rel, width=width, height=height,
                 caption=caption or (file.filename or ""), source="upload",
                 sha1=hashlib.sha1(raw).hexdigest())
    db.add(m)
    db.commit()
    db.refresh(m)
    return {"id": m.id, "path": rel, "width": width, "height": height, "caption": m.caption}
