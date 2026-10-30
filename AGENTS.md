# tiku-studio

面向高校教师的「文献命题 → 题库 → 组卷 → 导出」工作台（FastAPI + Vue3 + SQLite）。

如果你是本地编码 Agent，被要求执行「拆解课本/教辅/试卷」任务：**先读 [agent/AGENTS.md](agent/AGENTS.md)**，
产物投递到 `data/inbox/`，用 `python scripts/import_bundle.py <file>` 校验入库。

其他约定：
- 公开仓库：任何 PDF/扫描图/题图**永不入 git**（版权）。私人数据只进 gitignored 的 `data/`。
- 改后端代码后跑：`.venv/Scripts/python -m pytest apps/api/tests -q`
- 改前端后跑：`cd apps/web && npm run build`
- 两套拆解产物 schema 在 `packages/schema/`，Agent 模式与内置 LLM 模式共用。