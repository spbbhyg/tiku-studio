"""拆解产物入口：Agent 投递的 bundle 文件 / 网页上传的 PDF（LLM 拆解）。"""
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..config import INBOX_DIR
from ..db import get_db
from ..models import Question
from ..services import bundle_import, ingest_llm

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.post("/import")
def import_bundle_api(bundle: dict, db: Session = Depends(get_db)):
    """导入一个 bundle JSON（Agent 产物或 LLM 拆解产物）。校验失败返回 400。"""
    try:
        result = bundle_import.import_bundle(db, bundle)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return result


@router.post("/import-file")
async def import_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """上传 bundle .json 文件导入（对应本地 Agent 拆解的投递方式之一）。"""
    raw = await file.read()
    import json
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise HTTPException(400, f"不是合法 JSON：{e}")
    try:
        result = bundle_import.import_bundle(db, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return result


@router.post("/parse")
async def parse_pdf(
    file: UploadFile = File(...),
    subject: str = Form(...), grade: int = Form(...), semester: str = Form("上册"),
    publisher: str = Form("未知"), edition_year: int = Form(2024),
    role: str = Form("workbook"), title: str = Form(""),
    db: Session = Depends(get_db),
):
    """实验性：上传 PDF，用配置的视觉模型拆解为 bundle（不直接入库）。"""
    data = await file.read()
    meta = {
        "role": role, "subject": subject, "grade": grade, "semester": semester,
        "publisher": publisher, "edition_year": edition_year,
        "title": title or Path(file.filename or "未命名").stem,
        "source_file": file.filename,
    }
    try:
        bundle = ingest_llm.parse_pdf(db, data, meta)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(422, f"模型返回的 bundle 未通过校验：{e}")
    except Exception as e:  # 网络/接口错误原样透出
        raise HTTPException(502, f"调用 LLM 失败：{e}")
    return JSONResponse(bundle)


@router.get("/inbox")
def list_inbox():
    """列出 data/inbox 下的待导入 bundle 文件（Agent 拆解的默认投递目录）。"""
    if not INBOX_DIR.exists():
        return []
    return [{"file": p.name, "size": p.stat().st_size} for p in sorted(INBOX_DIR.glob("*.json"))]


@router.post("/inbox/{name}/import")
def import_inbox(name: str, db: Session = Depends(get_db)):
    safe = Path(name)
    if safe.name != name or "/" in name or "\\" in name:
        raise HTTPException(400, "非法文件名")
    path = INBOX_DIR / name
    if not path.exists():
        raise HTTPException(404, "inbox 中没有该文件")
    from ..services.bundle_import import import_bundle_file
    try:
        result = import_bundle_file(db, path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return result


@router.delete("/inbox/{name}")
def remove_inbox(name: str):
    path = INBOX_DIR / name
    if path.exists() and path.suffix == ".json":
        path.unlink()
    return {"ok": True}
