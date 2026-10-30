# tiku-studio

**面向高校教师的「文献命题 → 题库 → 组卷 → 导出」工作台。**

把「翻 PDF 找题干 → 裁剪成题图 → 写答案与解析 → 打标签 → 攒成题库 → 组卷 → 导出 Word/PDF」这条重复劳动固化成一套本地可跑的 Web 应用：
左边放文献，右边排题目，画布上所见即所得，保存即入库。

前后端分离（FastAPI + Vue3）· 自带 SQLite · 纯本机运行 · 无云依赖 · MIT

---

## 它能做什么

| 页面 | 路由 | 用途 |
| --- | --- | --- |
| **出题** | `/#/author` | 左边 PDF 框选裁剪，右边 A4 画布自由排版：拖入题图自动对齐、调字号/加粗/对齐、`＋文本` 排版公式、答案就地编辑、元素可多选组合/拆分/复制粘贴；保存即渲染成整页题图入库 |
| **题库** | `/#/questions` | 按专题（Chemy 九大化学专题）筛选 + 关键词搜索；卡片直接显示题干（KaTeX 渲染）与题图；多选后一键「用所选组卷」或导出 Word / PDF / Markdown |
| **组卷** | `/#/compose` | ① 从题库挑题：托盘调序、按题型设分值、总分实时累加；② 按单元范围自动抽题配分 |
| **试卷** | `/#/papers` | 题图与公式正常渲染、单题换题、打印视图、Playwright 一键导出 PDF（题目卷 + 答案卷） |
| **资料库** | `/#/books` | 书册 / 单元 / 知识点骨架，题图与原件归口 |
| **标签管理** | `/#/tags` | 标签分组维护：新增、改名（题目里的旧标签同步）、删除、批量导入、搜索 |
| **QF题库** | `/#/quizforge` | 内置 [QuizForge](https://github.com/cjxxx222/quizforge) 导入：直接读它的 `quizforge.db`，题库→书册、题目→题目，题型/选项/答案/解析/页码一并带过来，重复导入只补空字段 |
| **AI拆解** | `/#/ingest` | 可选：上传 PDF 交给视觉大模型自动拆题（需自备 API Key，实验性） |

## 截图

出题画布（左裁剪 / 右排版，A4 595×842pt，所见即所得）：

![出题画布](docs/screenshots/author-canvas.png)

答案就地编辑（支持 `$...$`，实时预览）：

![答案编辑](docs/screenshots/author-answer.png)

题库（专题 chips + 题图 + 多选导出）：

![题库](docs/screenshots/questions.png)

组卷（挑题 → 托盘调序调分 → 生成）：

![组卷](docs/screenshots/compose.png)

标签管理 / 试卷 / QuizForge 导入：

![标签管理](docs/screenshots/tags.png)
![试卷](docs/screenshots/paper.png)
![QuizForge](docs/screenshots/quizforge.png)

## 三分钟跑起来

需要 **Python 3.11+** 与 **Node.js 18+**。

```bash
git clone <本仓库> && cd tiku-studio

# Windows：双击 start.bat（自动建 venv、装依赖、建前端、起服务）
# macOS / Linux：
./start.sh
```

打开 <http://localhost:8100> 即可。**默认是空库**：从「出题」做第一道题，或用 `＋ 放入 PDF` 裁导入库；
也可以在「QF题库」里导入一份现成的 QuizForge 题库。

手机 / 平板访问：同一个 WiFi 下打开 `http://<电脑IP>:8100`。

> 只想要前端静态文件、不想装 Node？把 `apps/web/dist` 预先构建好（`cd apps/web && npm install && npm run build`），后端会自动托管它。

## 目录结构

```
apps/api        FastAPI 后端
  app/routers      books / questions / papers / ingest / manual / authoring / imports / meta
  app/services     组卷引擎、PDF 渲染、画布渲染（compose_render）、导出（docx/pdf/md）、
                   LLM 拆解适配器、QuizForge 导入、对纲校验
apps/api/tests   pytest 用例与最小夹具（fixtures/ 里 1 册课本纲 + 8 道题）
apps/web        Vue3 + Vite 前端（出题/题库/组卷/试卷/资料库/标签/QF题库/AI拆解/设置）
packages/schema 拆解产物的 JSON Schema（本地 Agent 与 API 两种引擎共用）
agent/          本地 Agent 拆解提示词手册
scripts/        import_bundle.py 等脚本
docs/           教程：本地部署 / Docker / Agent 拆解 / API 拆解 / 组卷
data/           你的私有数据（gitignored）：tiku.db、题图库、上传原件
```

## 技术栈

- 后端：FastAPI · SQLAlchemy 2 · Pydantic v2 · PyMuPDF（PDF 转图 / 裁剪 / 画布渲染）· python-docx（Word）· matplotlib（公式转图）· SQLite
- 前端：Vue 3 · Vite · vue-router（hash 路由）· KaTeX（公式）
- 导出：Word（python-docx）、PDF（自绘排版器；试卷另可用 Playwright 打印）、Markdown
- 测试：pytest（`apps/api/tests`）

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/stats` | 首页统计 |
| GET/POST | `/api/questions` | 题库检索（`q` / `book_id` / `q_type` / `difficulty` / `tag` / 分页）/ 新增 |
| PUT/DELETE | `/api/questions/{id}` | 改 / 删 |
| GET/POST | `/api/papers` | 试卷列表 / 组卷（`question_ids` 手动选题，或 `textbook_id` + 单元范围自动） |
| GET | `/api/papers/{id}/view` · `/export` | 打印视图 / PDF 导出 |
| GET/POST | `/api/authoring/compose` | 出题画布：保存版面（渲染成整页 PNG 入库）/ 读取回填 |
| GET/POST/PUT/DELETE | `/api/authoring/tags` | 标签库维护 |
| GET | `/api/authoring/export` | 题库导出 `fmt=docx\|pdf\|md`，可按筛选或 `ids` |
| POST | `/api/manual/upload` · `/crop` | 资料页图服务：上传 PDF、按归一化矩形裁剪 |
| GET/POST | `/api/import/quizforge` | QuizForge 库探测 / 导入（也可 `POST /upload` 传 .db） |

完整的交互式文档在 <http://localhost:8100/docs>。

## 数据与隐私

- 所有 PDF、题图、数据库都在本地 `data/`（gitignored），不上传任何服务器。
- 仓库**不含**任何受版权保护的教材/文献扫描件。
- 仅在配置了 LLM API Key 时，「AI拆解」才会把你的 PDF 页面图片发给你选定的模型服务商。

## 参与开发

欢迎 Issue / PR，哪怕是文档措辞的修正。

```bash
# 1) 后端：建 venv、装依赖
python -m venv .venv
.venv/Scripts/activate            # macOS/Linux: source .venv/bin/activate
pip install -r apps/api/requirements.txt

# 2) 前端：装依赖并构建（后端会托管 apps/web/dist）
cd apps/web && npm install && npm run build && cd ../..

# 3) 起服务（热更新开发模式）
python -m uvicorn app.main:app --app-dir apps/api --reload --port 8100
# 前端另开一个终端：cd apps/web && npm run dev

# 4) 测试（独立临时库，绝不碰 data/tiku.db）
python -m pytest apps/api/tests -q
```

约定：

- 提交前请确保 `pytest` 全绿；新增功能最好带一个最小用例（夹具放 `apps/api/tests/fixtures/`）。
- 数据库结构改动请写在 `apps/api/app/db.py` 的 `_migrate()` 里做幂等迁移，不要要求用户删库。
- 前端不引入新的 UI 框架；样式类统一在 `apps/web/src/style.css` 与组件 scoped 样式里。
- Issue 请写清：系统版本、`python --version`、复现步骤、预期与实际结果；能附截图更好。
- PR 请一个主题一个 PR，并在描述里关联对应 Issue。

安全：**不要把任何 API Key、`.db`、PDF 原件提交进仓库**（`.gitignore` 已覆盖，但提 PR 前请再 `git status` 检查一次）。

## Roadmap

- [x] 题库检索 / 对纲校验 / 自动组卷 / PDF 导出
- [x] 本地 Agent 与 LLM 两种 PDF 拆解
- [x] 题库题图化、自由挑题组卷（参考 [Chemy](https://brucejllpku.github.io/Chemy-)）
- [x] 文献出题并入题库、标签独立管理、QuizForge 题库导入
- [x] 大题画布：裁剪拖入、自动对齐、公式排版、组合/拆分/复制粘贴
- [ ] 试卷整卷视图（按「届次 / 试卷」聚合题库，像 Chemy 那样整卷勾选）
- [ ] 出题画布的纯文本版面（保留 markdown 源，后期可纯文本改）

## 更新记录

- **v0.7（2026-10-30）** 开源整理：移除示例题库 `seed/`（默认空库启动）、测试改用最小夹具、
  LICENSE 版权人写明、新增 `CONTRIBUTING`/本 README 的参与开发章节
- **v0.6（2026-10-30）** 大题画布（裁剪拖入 / 自动对齐 / 公式 / 组合拆分 / 复制粘贴 / 答案就地编辑）、
  题库与组卷按专题重做、标签独立成页、QuizForge 内置导入
- **v0.5** 文献出题并入题库、题库题图化、自由挑题组卷、框选坐标修正

## License

MIT。本项目基于 [cy-0754/tiku-studio](https://github.com/cy-0754/tiku-studio) 二次开发（原项目为小学题库与组卷系统），
在其之上重构了题库/组卷形态、加入了文献命题与画布出题、导出与 QuizForge 导入等能力。原始版权声明见 [LICENSE](LICENSE)。
