"""样卷对标：把自己出的卷子和 D:\\初赛、D:\\决赛 里的真题比一比。

只做「结构层面」的对比（题量、小问数、每卷页数），不评价题目好坏——
那是命题人自己的事。真题的统计靠扫 PDF 文本估出来，误差会有，但看趋势够用。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from statistics import median

import pymupdf
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/bench", tags=["bench"])

ROOTS = {
    "初赛": Path(r"D:\初赛"),
    "决赛": Path(r"D:\决赛"),
}
_CACHE: dict[str, list[dict]] = {}
_CACHE_DIR = Path(__file__).resolve().parents[3] / "data"
_MAX_FILES = 60          # 每个库最多扫 60 份，够看中位数了，扫全部太慢


def _stats(pdf: Path) -> dict:
    """扫一份真题 PDF，估出题数、小问数、页数。"""
    try:
        doc = pymupdf.open(pdf)
    except Exception:
        return {"pages": 0, "questions": 0, "subs": 0}
    pages = doc.page_count
    text = ""
    for i in range(min(pages, 40)):
        try:
            text += doc[i].get_text()
        except Exception:
            pass
    doc.close()
    q = len(re.findall(r"第\s*\d+\s*[题、.]", text))
    if q == 0:  # 有的卷子写「1.」「2.」开头
        q = len(re.findall(r"(?m)^\s*(\d{1,2})\s*[.．、]\s*\S", text))
    subs = len(set(re.findall(r"\b(\d{1,2})\s*[-–—]\s*(\d{1,2})\b", text)))
    return {"pages": pages, "questions": q, "subs": subs}


def _cache_file(kind: str) -> Path:
    return _CACHE_DIR / f"bench-{kind}.json"


def _scan(kind: str, force: bool = False) -> list[dict]:
    if kind in _CACHE and not force:
        return _CACHE[kind]
    root = ROOTS.get(kind)
    if not root or not root.is_dir():
        raise HTTPException(404, f"没找到参考卷目录：{root}")
    cf = _cache_file(kind)
    if not force and cf.is_file():
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
            if data.get("items"):
                _CACHE[kind] = data["items"]
                return _CACHE[kind]
        except Exception:
            pass
    out: list[dict] = []
    files = [p for p in sorted(root.rglob("*.pdf")) if "答案" not in p.name]
    for p in files[:_MAX_FILES]:
        st = _stats(p)
        if st["questions"] <= 0:
            continue
        out.append({
            "name": p.name,
            "path": str(p),
            "dir": p.parent.name,
            "pages": st["pages"],
            "questions": st["questions"],
            "subs": st["subs"],
            "per_q": round(st["pages"] / max(1, st["questions"]), 2),
        })
    _CACHE[kind] = out
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M"), "items": out},
                                 ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return out


@router.get("/refs")
def refs(kind: str = "初赛", force: bool = False) -> dict:
    items = _scan(kind, force=force)
    if not items:
        raise HTTPException(404, f"{kind} 里没扫到可用的试题 PDF")
    return {
        "kind": kind,
        "count": len(items),
        "items": items,
        "median": {
            "pages": median([i["pages"] for i in items]),
            "questions": median([i["questions"] for i in items]),
            "subs": median([i["subs"] for i in items]),
            "per_q": round(median([i["per_q"] for i in items]), 2),
        },
    }


class Mine(BaseModel):
    title: str = "我的卷子"
    questions: int = 0
    subs: int = 0
    score: float = 0.0
    pages: float = 0.0
    kind: str = "初赛"


@router.post("/compare")
def compare(body: Mine) -> dict:
    items = _scan(body.kind)
    if not items:
        raise HTTPException(404, "参考卷库里没有可比的真题")
    med_q = median([i["questions"] for i in items])
    med_s = median([i["subs"] for i in items])
    per_q = median([i["per_q"] for i in items])

    rows = [
        {"k": "题数", "mine": body.questions, "ref": med_q, "unit": "道"},
        {"k": "小问数", "mine": body.subs, "ref": med_s, "unit": "个"},
        {"k": "每题占页", "mine": round(body.pages / max(1, body.questions), 2) if body.pages else 0,
         "ref": round(per_q, 2), "unit": "页/题"},
    ]
    if body.score:
        rows.append({"k": "总分", "mine": body.score, "ref": 0, "unit": "分"})

    advice: list[str] = []
    if body.questions and med_q:
        ratio = body.questions / med_q
        if ratio < 0.6:
            advice.append(f"题量偏少：真题中位数 {med_q:.0f} 道，你这套 {body.questions} 道。当作考前专练没问题，当整卷偏薄。")
        elif ratio > 1.6:
            advice.append(f"题量偏多：真题中位数 {med_q:.0f} 道，你这套 {body.questions} 道，2 小时考试可能做不完。")
    if body.subs and med_s:
        per = body.subs / max(1, body.questions) if body.questions else 0
        ref_per = med_s / max(1, med_q)
        if per < ref_per * 0.6:
            advice.append(f"每题小问偏少：真题平均 {ref_per:.1f} 问/题，你这套 {per:.1f} 问/题，可再拆细一步。")
        elif per > ref_per * 1.8:
            advice.append(f"每题小问偏多：真题平均 {ref_per:.1f} 问/题，你这套 {per:.1f} 问/题，注意别把一道题拖成三道。")
    if body.pages and body.questions:
        mine_pq = body.pages / body.questions
        if mine_pq > per_q * 1.6:
            advice.append("每题占页明显比真题多：可能是图太大或行距太松，先看组卷预览里有没有半页空白。")
    if not advice:
        advice.append("题量、小问密度都在真题区间内，可以直接拿去印。")
    return {"ok": True, "rows": rows, "advice": advice, "ref_count": len(items), "kind": body.kind}
