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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import WEB_DIST, ensure_dirs
from .db import SessionLocal, init_db
from .routers import (workbench, wb_extra, wb_split, ai as ai_router, authoring, books, exam, github_sync,
                      imports, inbox, manual, meta, paper_canvas, papers, questions, sets,
                      settings as settings_router, v2 as v2_router, reader,
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

app.include_router(books.router)
app.include_router(v2_router.router)
app.include_router(reader.router)
app.include_router(questions.router)
app.include_router(papers.router)
app.include_router(paper_canvas.router)
app.include_router(inbox.router)
app.include_router(meta.router)
app.include_router(manual.router)
app.include_router(authoring.router)
app.include_router(imports.router)
app.include_router(sets.router)
app.include_router(settings_router.router)
app.include_router(workbench.router)
app.include_router(wb_split.router)
app.include_router(wb_extra.router)
app.include_router(ai_router.router)
app.include_router(exam.router)
app.include_router(github_sync.router)


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
