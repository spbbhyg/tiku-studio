"""tiku-studio API 入口。

两个工作面并存：
- 小学题库与组卷：资料拆解 → 对纲校验 → 自动组卷导出 PDF
- 高校文献命题：把文献/教材 PDF 放进同一块划分区，人工裁剪、手写题干与答案（含公式），
  可按筛选结果一键导出 Word / PDF / Markdown

- 开发：uvicorn app.main:app --app-dir apps/api --reload（前端另用 vite dev）
- 生产：uvicorn 直接挂载 apps/web/dist 的构建产物，单进程即可用
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import WEB_DIST, ensure_dirs
from .db import SessionLocal, init_db
from .routers import (authoring, books, imports, inbox, manual, meta, paper_canvas, papers,
                      questions, sets, settings as settings_router)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tiku-studio")

app = FastAPI(
    title="tiku-studio", version="0.2.0",
    description="题库与组卷（小学教材）+ 文献命题工作台（高校课程），共用资料库与题图服务",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(books.router)
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


@app.on_event("startup")
def startup() -> None:
    ensure_dirs()
    init_db()


if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
else:
    @app.get("/")
    def root():
        return {"app": "tiku-studio", "docs": "/docs",
                "hint": "前端未构建：cd apps/web && npm install && npm run build"}


def main() -> None:
    import uvicorn

    from .config import API_PORT

    uvicorn.run(app, host="0.0.0.0", port=API_PORT)


if __name__ == "__main__":
    main()
