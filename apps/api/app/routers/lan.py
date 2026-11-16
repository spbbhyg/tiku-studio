"""局域网共备：让同一网络里的其他老师打开同一个地址，一起备课/共创题目。

软件本身就是个本机服务（监听 0.0.0.0），所以只要把地址发出去，同事在浏览器里
打开就是同一份数据——能看到你出的草稿、能一起勾题组卷。这里做三件事：

1. 告诉他"该发哪个地址"（本机所有局域网 IP，可按需给出带访问码的链接）；
2. 可选的访问码：设了以后，非本机访问要先带一次访问码（之后用 cookie 记住），
   免得同一间办公室里别人乱点；
3. 一键复制链接。

不设访问码也能用（纯局域网教室内网），设了更稳妥。改完立即生效，不用重启。
"""
from __future__ import annotations

import json
import socket
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from fastapi import Depends

from ..db import get_db
from ..models_wb import AppSetting

router = APIRouter(prefix="/api/lan", tags=["lan"])

KEY = "lan"          # app_settings 里的键，值形如 {"code": "6537"}
PORT = 8100


def _read(db: Session) -> dict:
    row = db.get(AppSetting, KEY)
    if not row or not row.value:
        return {}
    try:
        data = json.loads(row.value)
    except Exception:                     # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _write(db: Session, data: dict) -> None:
    row = db.get(AppSetting, KEY)
    value = json.dumps(data, ensure_ascii=False)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=KEY, value=value))
    db.commit()


def current_code() -> str:
    """给中间件用：读当前访问码（读不到就当没设）。"""
    try:
        from ..db import SessionLocal

        with SessionLocal() as db:
            return str(_read(db).get("code") or "")
    except Exception:                     # noqa: BLE001
        return ""


def lan_ips() -> list[str]:
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127.") or ip.startswith("169.254.") or ip in ips:
                continue
            ips.append(ip)
    except Exception:                     # noqa: BLE001
        pass
    if not ips:
        # 兜底：连一下外网看默认出口地址（不真正发数据）
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("223.5.5.5", 53))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                ips.append(ip)
        except Exception:                 # noqa: BLE001
            pass
    return ips


@router.get("/info")
def info(db: Session = Depends(get_db)):
    data = _read(db)
    code = str(data.get("code") or "")
    ips = lan_ips()
    urls = [(f"http://{ip}:{PORT}/" + (f"?c={code}" if code else "")) for ip in ips]
    return {
        "port": PORT,
        "hosts": ips,
        "urls": urls,
        "code_set": bool(code),
        "code": code if code else "",
        "on": bool(ips) and bool(data.get("on", True)),
    }


class CodeIn(BaseModel):
    code: str = ""


@router.post("/code")
def set_code(body: CodeIn, db: Session = Depends(get_db)):
    code = (body.code or "").strip()
    if code and not code.isalnum():
        raise HTTPException(400, "访问码只用字母和数字，免得同事输不进去")
    if len(code) > 24:
        raise HTTPException(400, "访问码太长了，6～8 位就够")
    data = _read(db)
    data["code"] = code
    data["on"] = True
    _write(db, data)
    return {"ok": True, "code_set": bool(code)}


@router.api_route("/open", methods=["GET", "POST"])
def open_once(request: Request, response: Response, db: Session = Depends(get_db)):
    """非本机访问时先访一次 /api/lan/open?c=xxxx 拿 cookie（前端也会自动带）。"""
    code = str(_read(db).get("code") or "")
    got = request.query_params.get("c") or ""
    if code and got != code:
        raise HTTPException(403, "访问码不对")
    if code:
        response.set_cookie("lan_code", code, max_age=7 * 24 * 3600, samesite="lax")
    return {"ok": True}
