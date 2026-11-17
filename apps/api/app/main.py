"""神奋 API 入口。

两个工作面并存：
- 小学题库与组卷：资料拆解 → 对纲校验 → 自动组卷导出 PDF
- 高校文献命题：把文献/教材 PDF 放进同一块划分区，人工裁剪、手写题干与答案（含公式），
  可按筛选结果一键导出 Word / PDF / Markdown

- 开发：uvicorn app.main:app --app-dir apps/api --reload（前端另用 vite dev）
- 生产：uvicorn 直接挂载 apps/web/dist 的构建产物，单进程即可用
"""
import logging
import os
import threading

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import WEB_DIST, ensure_dirs
from .db import SessionLocal, init_db
from .routers import (workbench, wb_extra, lan, update as update_router, ai as ai_router, authoring, books, exam, github_sync,
                      manual, meta, paper_canvas, papers, questions, sets,
                      settings as settings_router, v2 as v2_router, reader,
                      chemy as chemy_router, convert as convert_router, drafts as drafts_router, compose as compose_router,
                      doce as doce_router,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("神奋")

logger = logging.getLogger("tiku")

app = FastAPI(
    title="神奋", version="0.2.0",
    description="题库与组卷（小学教材）+ 文献命题工作台（高校课程），共用资料库与题图服务",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def lan_guard(request: Request, call_next):
    """局域网访问码：设了码以后，非本机请求要带一次 ?c=码（之后用 cookie 记住）。"""
    try:
        code = lan.current_code()
    except Exception:                     # noqa: BLE001
        code = ""
    if code:
        host = request.client.host if request.client else "127.0.0.1"
        if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            got = request.query_params.get("c") or request.cookies.get("lan_code") or ""
            if got != code:
                return JSONResponse({"detail": "这个地址需要访问码：问一下发链接的老师"}, status_code=403)
            resp = await call_next(request)
            if got and not request.cookies.get("lan_code"):
                resp.set_cookie("lan_code", code, max_age=7 * 24 * 3600, samesite="lax")
            return resp
    return await call_next(request)

app.include_router(books.router)
app.include_router(v2_router.router)
app.include_router(reader.router)
app.include_router(questions.router)
app.include_router(papers.router)
app.include_router(paper_canvas.router)
app.include_router(meta.router)
app.include_router(manual.router)
app.include_router(authoring.router)
app.include_router(sets.router)
app.include_router(settings_router.router)
app.include_router(workbench.router)
app.include_router(wb_extra.router)
app.include_router(ai_router.router)
app.include_router(exam.router)
app.include_router(github_sync.router)
# 本地题库（D:\化学竞赛题库）接入层：只走 HTTP，读写都通过题库自己的接口
app.include_router(chemy_router.router)
app.include_router(drafts_router.router)
app.include_router(compose_router.router)
app.include_router(convert_router.router)
app.include_router(doce_router.router)
app.include_router(lan.router)
app.include_router(update_router.router)


@app.on_event("startup")
def startup() -> None:
    ensure_dirs()
    init_db()
    # 内置免费模型服务（不依赖 DSH）：后台拉起，失败不影响其它功能
    if os.environ.get("TIKU_NO_OFM") not in ("1", "true", "yes"):
        def _boot_ofm() -> None:
            try:
                from .services import ofm as ofm_svc
                st = ofm_svc.ensure_running()
                # 首次启动/没配过 AI：直接把内置服务设为默认模型，做到开箱即用
                if st.get("ok") and not os.environ.get("TIKU_NO_AUTO_AI"):
                    from .db import SessionLocal
                    from .services import ai as ai_svc
                    db = SessionLocal()
                    try:
                        cfg = ai_svc.public_config(db)
                        full = ai_svc._full_config(db)          # noqa: SLF001 内部读取，取原始 key
                        same_host = (full.get("base_url") or "").rstrip("/") == str(st.get("base_url", "")).rstrip("/")
                        key_diff = (full.get("api_key") or "") != (st.get("key") or "")
                        if not cfg.get("configured") or (same_host and key_diff):
                            ai_svc.save_config(db, base_url=st["base_url"],
                                               model=ofm_svc.pick_model(st.get("models", [])),
                                               api_key=st.get("key", ""))
                    finally:
                        db.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("内置免费模型启动失败：%s", exc)
        threading.Thread(target=_boot_ofm, daemon=True, name="ofm-boot").start()

    # 安装版自带题库：装好后不用手动开题库，这里在后台确认一次，没起来就拉起来
    def _boot_chemy() -> None:
        try:
            from .services import chemy_client
            try:
                if chemy_client.health():
                    return
            except Exception:  # noqa: BLE001 没起来就是「要拉起」，不必区分原因
                pass
            exe = chemy_router._chemy_exe()          # noqa: SLF001 与「启动题库」按钮同一套路径
            if not os.path.exists(exe):
                logger.info("没有随包的题库程序，跳过自动启动：%s", exe)
                return
            import subprocess
            flags = 0
            for name, val in (("DETACHED_PROCESS", 0x00000008), ("CREATE_NEW_PROCESS_GROUP", 0x00000200)):
                flags |= getattr(subprocess, name, val)
            subprocess.Popen([exe], cwd=os.path.dirname(exe), creationflags=flags,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("已拉起随包题库：%s", exe)
        except Exception as exc:  # noqa: BLE001
            logger.warning("自动启动题库失败（不影响出题）：%s", exc)

    threading.Thread(target=_boot_chemy, daemon=True, name="chemy-boot").start()


if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
else:
    @app.get("/")
    def root():
        return {"app": "神奋", "docs": "/docs",
                "hint": "前端未构建：cd apps/web && npm install && npm run build"}


def main() -> None:
    import uvicorn

    from .config import API_PORT

    uvicorn.run(app, host="0.0.0.0", port=API_PORT)


if __name__ == "__main__":
    main()
