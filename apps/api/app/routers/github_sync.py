"""公共题库的 GitHub 同步接口。

只有本机（127.0.0.1）能发布——局域网里别人打开的是你的服务，发布动作会用你本机的令牌。
拉取（一键更新）不限来源。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import github_sync as gh

router = APIRouter(prefix="/api/github", tags=["github"])

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _local_only(request: Request) -> None:
    host = (request.client.host if request.client else "") or ""
    if host not in LOCAL_HOSTS:
        raise HTTPException(403, "ERR_LOCAL_ONLY: 只有运行服务的这台电脑能发布题目（当前请求来自 " + host + "）")


def _wrap(exc: Exception) -> HTTPException:
    if isinstance(exc, gh.GHError):
        return HTTPException(exc.status, f"{exc.code}: {exc.message}")
    return HTTPException(500, f"{type(exc).__name__}: {exc}")


class ConfigIn(BaseModel):
    owner: str | None = None
    repo: str | None = None
    mirror: str | None = None


class TokenIn(BaseModel):
    token: str


class PublishIn(BaseModel):
    include_drafts: bool = False
    message: str = ""
    force: bool = False


class SkipIn(BaseModel):
    id: int
    skip: bool = True


@router.get("/status")
def status(db: Session = Depends(get_db)):
    try:
        return gh.status(db)
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


@router.post("/config")
def set_config(body: ConfigIn, db: Session = Depends(get_db)):
    try:
        return gh.save_config(db, body.owner, body.repo, body.mirror)
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


@router.post("/token")
def set_token(body: TokenIn, request: Request, db: Session = Depends(get_db)):
    _local_only(request)
    try:
        return gh.save_token(db, body.token)
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


@router.delete("/token")
def delete_token(request: Request, db: Session = Depends(get_db)):
    _local_only(request)
    return gh.clear_token(db)


@router.get("/preview")
def preview(include_drafts: bool = False, db: Session = Depends(get_db)):
    try:
        lost, images = gh.build_pack(gh.publishable(db, include_drafts))
        size = sum(len(b) for b in images.values()) + sum(len(str(x)) for x in lost)
        return {
            "count": len(lost),
            "images": len(images),
            "bytes": size,
            "titles": [(x.get("stem") or "").splitlines()[0][:60] if (x.get("stem") or "").strip() else "(无题干)"
                       for x in lost[:8]],
        }
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


@router.post("/skip")
def set_skip(body: SkipIn, db: Session = Depends(get_db)):
    """把某道题排除在公共题库之外（不影响本地使用）。"""
    try:
        return gh.set_skip(db, body.id, body.skip)
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


@router.post("/publish")
def publish(body: PublishIn, request: Request, db: Session = Depends(get_db)):
    _local_only(request)
    try:
        return gh.publish(db, body.include_drafts, body.message, body.force)
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


@router.post("/pull")
def pull(db: Session = Depends(get_db)):
    try:
        return gh.pull(db)
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
