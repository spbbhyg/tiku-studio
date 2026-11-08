# tiku-studio 开发日志 / 交接说明

> 快照时间：2026-11-04 22:45（Asia/Shanghai，**已合并到 C 盘工作副本**）
> 项目目录：`C:\Users\Administrator\Documents\tiku-studio`
> 当前服务：`http://127.0.0.1:8100`（Windows，本机运行，计划任务 `tiku-studio` 登录自启）
> 合并说明见文末「八、2026-11-04 合并记录」

这份文档给「换一台电脑继续开发」用。先看 **一、换机步骤**，再看 **四、当前状态** 和 **六、已知问题**。

---

## 一、换机步骤（最短路径）

### 1. 需要带走的东西

| 内容 | 是否必须 | 说明 |
| --- | --- | --- |
| 源码包 `tiku-studio-dev-*.zip` | 必须 | 含 `apps/`、`docs/`、`packages/`、`scripts/`、启动脚本、已构建的 `apps/web/dist`，不含 `.venv`、`node_modules`、`data/` |
| 数据包 `tiku-studio-data-*.zip` | 按需 | 含 `data/tiku.db` 与 `data/library/`（题库、上传 PDF、题图）；**API Key 已从包内数据库清除**，到新机重新填 |
| 本文件 `DEVLOG.md` | 必须 | 已在源码包根目录 |

不要把整个 `.venv`、`apps/web/node_modules` 拷到另一台电脑：Windows 下这些带绝对路径/平台二进制，换机后必须重装。

### 2. 新电脑安装

1. 装 **Python 3.11+**（推荐 3.13）和 **Node.js 20+**（推荐 22）。  
   或者直接双击 `一键安装并启动.bat`，缺 Python/Node 时会尝试自动下载安装。
2. 解压源码包到任意目录（路径尽量不要带空格；带中文可以）。
3. 双击 `start.bat`。它会：
   - 找 Python，创建 `.venv`；
   - 安装 `apps/api/requirements.txt`；
   - 若 `apps/web/dist` 不存在才安装前端依赖并构建；
   - 启动 `uvicorn`，打开 `http://127.0.0.1:8100`。
4. 第一次启动后，打开「设置 → AI 模型」：
   - 有 DeepSeek Key：填 `https://api.deepseek.com` + `deepseek-chat` + 自己的 Key；
   - 没有 Key：保持 DeepSeek Harness 免费模型插件运行，点「检测免费模型插件 → 使用免费模型」。免费网关是 `http://127.0.0.1:8326/v1`，Key 从 `%USERPROFILE%\.dsh\openai-gateway\api-key` 运行时读取，不写进项目。
5. 如需旧数据：先解压数据包，把 `data/` 放回项目根目录，再启动。  
   如果 `storage.json` 里记录过自定义数据目录，要么删除 `storage.json`，要么把数据放到对应路径。

### 3. 验证是否正常

```bat
start.bat
:: 另开一个终端
curl http://127.0.0.1:8100/api/stats
```

应返回 JSON（`books / questions / papers` 等计数）。  
再打开：

- `http://127.0.0.1:8100/#/author` 出题页
- `http://127.0.0.1:8100/#/questions` 题库
- `http://127.0.0.1:8100/#/settings` 设置

---

## 二、技术栈与目录

- 后端：FastAPI + SQLAlchemy + SQLite + PyMuPDF + Pillow + RapidOCR + python-docx + matplotlib + RDKit（可选）
- 前端：Vue 3 + Vite 5 + Vue Router + KaTeX + Tiptap + Ketcher（结构式）+ 少量 React（Ketcher 容器）
- 数据库：`data/tiku.db`（SQLite，25 张表）
- 文件库：`data/library/`（`uploads/` 原始 PDF、`questions/` 题图、`page-cache/` 页面缓存）
- 构建产物：`apps/web/dist`（FastAPI 直接托管）
- 启动脚本：`start.bat`（Windows 主入口）、`start.sh`（macOS/Linux）、`run-server.cmd`（计划任务用）、`检查环境.bat`（体检）

```
apps/
  api/
    app/
      main.py              FastAPI 入口、静态托管、生命周期
      config.py            路径/端口/数据目录解析（TIKU_* 环境变量）
      db.py                引擎、Session、_migrate()（所有迁移必须幂等）
      models.py            Book/Question/Paper/Setting/... 25 张表对应模型
      routers/             authoring, questions, papers, manual, imports, ai, workbench, wb_extra, v2, reader...
      services/            ai.py（LLM/OCR/翻译/出题/拆题/归类）、compose_render.py（画布转 PNG）、
                           pdf_render.py（打印视图）、paper_builder.py、authoring_export.py、web_search.py...
    tests/                 pytest，最小夹具
  web/
    src/views/             Dashboard, Authoring, Questions, Papers, PaperView, SmartPaper, Reader, Settings...
    src/components/        CanvasPane.vue（A4 画布核心）、FormulaPad.vue...
    src/textflow.js        PDF/OCR 文本「连句成行」工具
    public/ketcher/        Ketcher 静态资源（约 30MB）
    dist/                  已构建产物（源码包内含，换机可先不装 Node）
```

---

## 三、开发命令

### 后端测试

```bat
.venv\Scripts\python.exe -m pytest apps/api/tests -q
```

当前结果：**20 passed, 4 warnings**（警告是 FastAPI `on_event` 与 Starlette TestClient 的弃用提示，不影响）。

### 前端构建

这台机器 npm 不在 PATH，用项目自带的 Node + pnpm：

```bat
cd apps/web
C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe ^
  node_modules\vite\bin\vite.js build
```

新电脑若装了 Node，也可以：

```bat
cd apps/web
npm install
npm run build
```

> `apps/web/node_modules` 里的 esbuild 安装脚本被 pnpm 忽略过一次（`ERR_PNPM_IGNORED_BUILDS`），不影响构建；如果换机后 `vite` 报找不到 esbuild，执行 `npm rebuild esbuild` 或重新 `npm install`。

### 启动 / 重启

```bat
start.bat
:: 或后台（日志写 data/server.log）
.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir apps/api --host 0.0.0.0 --port 8100
```

端口被占用时先查：

```bat
netstat -ano | findstr :8100
taskkill /PID <pid> /F
```

### 改数据库结构

所有迁移写在 `apps/api/app/db.py::_migrate()`，必须幂等（先判断表/列是否存在），不要手工改生产库。

---

## 四、当前状态（2026-11-04）

### 数据

- 书：3 本（自编合集 / AI 文献出题 / 化学基础自测演示）
- 题目：12 道（6 道已确认 + 6 道拆题草稿；含 6 道演示数据，来源标签「演示数据（可删）」「批量拆题测试」）
- 试卷：3 套（其中 #4 为试题篮生成的试卷，带快照 `_version/align/font_size/color`）
- 分类维度：内置「专题」9 项，可在「设置 → 分类与标签」自行增删改名
- 错题集：2 本（易错·热力学 / 电化学易错）
- 设置表里保存了 AI 配置（`settings.deepseek`）。**源码包不含数据库；数据包里的 Key 已清空。**

### 这一轮已经完成的功能

- **出题页**
  - PDF 框选裁剪、固定图片、图片转文字（OCR）、橡皮擦涂白、图内小区域框选提取；
  - 画布字级字号/颜色、对齐/统一/连句、A－/A＋ 缩放、左右平移；
  - PDF 文字精确选区（不再多带相邻行）、自动连句、按顺序插入；
  - 答案画布「AI 生成答案」自动复核，答案 + 解析 + 参考文献一起填入，可人工改；
  - AI 逐页精读（扫描页自动 OCR）——**当前版本已按用户要求从面板移除，后端接口仍保留**；
  - 分步出题：1–11 题、右上角「出下一个题」、完成后自动生成试卷；后台任务，切页不丢；
  - 保存时 AI 自动归类题型/难度/化学专题。
- **题库**
  - 多 PDF 导入、拖拽、中文文件名、按题号拆题、题目/答案卷配对、同一大题小问合并；
  - 批量拆题支持「文字拆题」与「按题号裁图（扫描件）」；
  - 选中一键删除；页内组卷托盘。
- **试卷**
  - 题目卷/答案卷/合卷三种可编辑打印视图（标题、题干、答案、解析可直接改并保存）；
  - 试卷融合，大题号顺延（1-2 → 2-2，小问号不变）。
- **精读 / 文献解读**
  - 精读页支持批注（笔/橡皮/颜色/粗细/清除）；
  - 文献解读后台生成、结果可勾选导入题库、可选专题。
- **首页 AI 助手「小卷」**
  - 流式输出、站内问答 + 通用问答、可选联网搜索（Bing RSS）。
- **模型接入**
  - 付费 DeepSeek Key 与本机 DSH 免费模型网关（`127.0.0.1:8326`）可切换；
  - 免费模型是推理模型，出题会先「思考」，所以单次 1–2 分钟属正常；token 预算已调大。

### 最近的 bug 修复（按时间从近到远）

1. 文献解读出题报「AI 没有返回可用的题目」：免费模型推理 token 占满 `max_tokens` → 正文为空。已提高预算、增加 JSON 修复与重试、失败时清除「继续等待」提示。
2. 拆题把 1-1/1-2 拆成多道：已按大题号合并；答案卷按题号切段并配对；图片拆题答案图附在题后。
3. 画布排版与题库渲染不一致：`compose_render` 把坐标 0 当成 48，已修；支持 `justify`；「自动对齐」默认关闭。
4. 小问只插入 `(1)` 不插入内容：`insertSub` 已带上输入文字。
5. Ketcher「取结构出图」一直灰：`ketcher:ready` 错过 + MOL 首行被 strip 导致 RDKit 解析失败；已改为 ping 轮询 + `rstrip`。
6. 固定图片无法选中/改文字：锁定图 `pointer-events` 放行 + 右上角 🔒；文字是像素，需「图片转文字」。
7. 画布左右空白过多：缩放上限 1.15 → 2.6，去掉 14px 内边距。
8. 批量拆题 `Failed to fetch`：已加 3 秒重试与提示（长任务仍同步，超时先看 `data/server.log`）。

---

## 五、API 速查

启动后打开 <http://127.0.0.1:8100/docs> 可看完整 Swagger。常用：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/stats` | 首页统计、存活检查 |
| POST | `/api/manual/upload` | 上传 PDF/图片 |
| GET | `/api/manual/docs` | 文档列表 |
| POST | `/api/manual/crop` | 按归一化选区裁剪 |
| POST | `/api/manual/image-region` | 图内小区域裁剪/OCR/擦除源图 |
| POST | `/api/manual/trim` | 裁白边 |
| POST | `/api/ai/translate` | 翻译 |
| POST | `/api/ai/answer` | 生成答案（答案/解析/参考文献） |
| POST | `/api/ai/summarize-page` | 单页精读（支持 OCR） |
| POST | `/api/ai/classify` | 题型/专题归类 |
| POST | `/api/ai/smart-questions/jobs` + GET | 文献出题后台任务 |
| POST | `/api/papers/ai/jobs` + GET | 出题后台任务（试卷） |
| POST | `/api/papers/ai/preview` / `build` | 分步出题预览/成卷 |
| POST | `/api/papers/{id}/merge` | 试卷融合 |
| POST | `/api/import/papers` | 多 PDF 题库导入 |
| POST | `/api/wb/extract` | 文字拆题 |
| POST | `/api/wb/extract-images` | 按题号裁图拆题 |
| PATCH | `/api/papers/{id}/items` | 保存打印视图里的编辑 |

---

## 六、已知问题 / 下一步

1. **PDF 直接导出返回 501**：依赖 Playwright 浏览器内核，本机未安装。当前用「题目卷 / 答案卷 / 合卷」打印视图，浏览器「打印 → 另存为 PDF」即可。  
   要恢复直接下载：`.venv\Scripts\python.exe -m playwright install chromium`。
2. **免费模型依赖 DeepSeek Harness 运行**：DSH 没开时免费通道不可用；可在设置里切回自己的 DeepSeek Key。
3. **免费模型慢且会自我纠正**：答案质量需人工复核，UI 定位就是「AI 草稿 + 人工修改」。
4. **旧拆题数据**：之前已导入的 6/7 道题若答案为空，是旧版本拆题产物，重新用新版本拆一次即可；旧数据无法自动补答案。
5. **README 有过时描述**：还提到「图片转文字」按钮和 AI 逐页精读面板，当前 UI 已移除；以后统一以本日志和代码为准。
6. **仓库不是 git 仓库**：当前目录没有 `.git`，无法 `git pull`。如果要继续用 GitHub 更新，先 `git init` 并在新机 clone 远端后把改动搬过去，避免直接覆盖 `data/`。
7. **隐私**：`data/`、上传的 PDF/扫描图、`storage.json`、设置里的 API Key 不要提交到公开仓库；`.venv` 与 `node_modules` 不要打包。
8. **数据包里的 Key 已清除**：换机后必须重新填 Key 或启用免费模型，否则 AI 功能报「还没有配置 DeepSeek API Key」。

---

## 七、交接清单

- [x] 源码、文档、启动脚本、已构建前端
- [x] 开发日志（本文件）
- [x] 后端测试 20 passed
- [x] 数据包（可选，Key 已清空）
- [ ] 新电脑上重填 AI Key / 启用免费模型
- [ ] 新电脑上跑一次 `start.bat` 与 `pytest`

---

## 八、2026-11-04 合并记录（两个工作副本合一）

### 背景

这一天并行存在两份代码：

| 副本 | 位置 | 内容 |
| --- | --- | --- |
| A（本机 C 盘） | `C:\Users\Administrator\Documents\tiku-studio` | WPS 式出题界面（顶部工具栏 + 右侧内嵌面板 + 画布让位）、题目/答案浏览器式标签页、小问工具、Ketcher 内嵌手绘、取结构出图直插画布、AI 额度面板、试题篮、错题集、可配分类 |
| B（另一台机器） | `D:\tiku-studio-源码-20261031\tiku-studio` | 在 A 的基础上继续做了：AI 任务化（jobs 轮询）、OCR 图片转文字、橡皮擦涂白、图内小区域提取、按题号裁图拆题 + 题干/答案卷配对、小问合并、试卷融合、答案卷 AI 复核、精读批注、文献解读后台任务 |

B 的源码包（`tiku-studio-dev-2026-11-04.zip`）**已包含 A 的全部文件**，因此合并方式确定为：以 B 的版本覆盖 A 的分歧文件，保留 A 的数据与构建环境。

### 实际动作

1. 合并前备份：`dsh-work\backup-src-20261104-2233`（api/app + web/src + README），数据库另存 `data/tiku.db.bak-*`。
2. 解压 B 的包到 `dsh-work\devlog-20261104`，逐文件比对（`comm` + `cmp`），确认两边**文件集合一致**，仅 23 个文件内容不同。
3. 用 B 的版本覆盖以下文件（共 23 个）：
   - 后端：`routers/ai.py`、`routers/manual.py`、`routers/papers.py`、`routers/questions.py`、`routers/v2.py`、`routers/workbench.py`、`services/ai.py`、`services/compose_render.py`、`apps/api/requirements.txt`、`apps/api/tests/test_paper_flow.py`
   - 前端：`App.vue`、`components/CanvasPane.vue`、`components/FormulaPad.vue`、`views/Authoring.vue`、`views/ComposeCanvas.vue`、`views/Extract.vue`、`views/Reader.vue`、`views/Settings.vue`、`views/SmartPaper.vue`、`apps/web/pnpm-lock.yaml`
   - 文档：`README.md`、`docs/本地部署.md`、`使用说明.txt`
4. `DEVLOG.md` 放入项目根目录（本文件）。
5. 重新构建前端（`apps/web`，`index-*.js` 约 1.02 MB），重启服务。

### 合并后验证（2026-11-04 22:45）

| 项 | 结果 |
| --- | --- |
| 后端语法 | `apps/api/app/**/*.py` 全部 `ast.parse` 通过 |
| 单元测试 | `pytest apps/api/tests -q` → **20 passed, 4 warnings** |
| 接口总数 | `/openapi.json` → **132** 条（合并前 105） |
| 新增接口抽查 | `/api/ai/doc-brief/jobs`、`/api/ai/smart-questions/jobs`、`/api/ai/ocr-image`、`/api/manual/extract-text`、`/api/papers/ai/jobs`、`/api/papers/merge`、`/api/wb/extract`、`/api/wb/extract-images` 全部存在 |
| 页面冒烟 | 9 页（首页/题库/试卷/设置/出题/精读/智能出题/QF题库/错题集）Playwright 全 OK，控制台 **0 报错** |
| 出题页 | 顶部工具：✂ 文献与裁剪 / ∑ 公式 / ⌬ 结构式 / ⑂ 小问 / AI 生成答案 / ＋ 新建一题 / 保存到题库 |
| 试卷页 | 「融合」入口在 |
| 题库 | 「批量拆题」弹层支持题干卷+答案卷配对、同一大题 1-1/1-2 合并 |
| PDF 导出 | `GET /api/papers/4/export` → **200 / 208 KB application/pdf**（本机 Python Playwright + chromium 已装，见下） |

### 对原文「已知问题」的修订

1. **PDF 直接导出 501 —— 本机不复现**。本机已装 Python `playwright`，且 `%LOCALAPPDATA%\ms-playwright\chromium-1248` 存在，实测导出 200、208 KB。换机若报 501，再执行：
   ```
   .venv\Scripts\python.exe -m playwright install chromium
   ```
2. 免费模型依赖 DSH 运行、慢且会自我纠正 —— 仍然成立。
3. 旧拆题数据答案为空 —— 仍然成立（现有 6 道拆题草稿就是旧产物，需要时重拆）。
4. 仓库不是 git 仓库 —— 仍然成立，且**三次本地改动仍未推送 GitHub**（需要带 `repo` + `workflow` 权限的 token，桌面 `发布到GitHub.cmd`）。
5. 隐私：`data/`、上传 PDF、`storage.json`、API Key 不入库。

### 下一步建议（按优先级）

1. 把「AI 任务化（jobs）」接到前端：智能出题、文献解读、分步出题现在都是后台任务，前端应轮询 + 显示进度（接口已就绪）。
2. 出题页 OCR 相关按钮（图片转文字 / 橡皮擦 / 图内小区域）目前只在选中图片后出现，可在工具条上给一个常驻入口。
3. 拆题草稿的答案补全：跑一次「按题号裁图 + 答案卷配对」重拆旧数据。
4. 把 v2 题组（一题多小问）与出题页的小问工具打通：`question_groups/sub_questions` 已有表和接口，前端可复用。
5. 推送 GitHub（token 到位后）。

---

## 九、2026-11-05 更新（本轮）

1. **题库改成 Chemy 三栏**：`apps/web/src/views/Bank.vue`，左「专题计数 / 我的收藏 / 组卷历史 / 题目讨论 / 消息」、中「题目目录（来源分组树 + 题号跳转 + 来源/题型筛选）」、右「题干 + 查看参考答案 / 讨论与提问 折叠 + 信息卡（标签、第 N 题（X 分）、来源、选入练习卷/收藏/下载本题 PDF）」，底部浮动「已选 N 题 · 生成练习卷」。旧版保留在 `/#/questions/classic`。
2. **精读标注版 PDF 落盘**：`POST /api/reader/export-file` 写到 `data/exports/{原文名}-标注版-时间戳.pdf`，接口返回路径，页面在导出按钮下显示完整路径 + 「打开所在文件夹 / 预览」。
3. **出题页 PDF 裁剪重做**：左侧常驻栏删除，改为右侧「📄 PDF」面板（导入/列表/缩略图/✂ 放大并裁剪）；点裁剪弹出 94vw×92vh 大窗（翻页、清晰度、缩放、框选、裁剪到题目/答案），裁完自动关闭并落图到画布。
4. **导出排版按样卷校准（第一版）**：`services/v2_export.py` 与 `services/pdf_render.py` 的 CSS 统一为 A4 + 页边距 22/32/20mm、正文 SimSun/Times 10.5pt、行距 1.45、标题 12.5pt 黑体、题型头 Arial Bold 10.5pt、分值紧跟题干。尚未实现样卷的「第 N 题 …（X 分，占 Y%）」与 1.1 / 1.1.1 三级编号。

验证：`pytest` 20 passed；9 个页面 Playwright 全 OK、无 5xx、控制台 0 报错。

### 2026-11-05 晚补充（插入公式抖动等 4 个修复）

1. **插入公式整页抖**：`insertFormula()` 原来在插入后 `drawer.value = ''` 关闭右侧面板，画布宽度 992→1424 触发重新缩放 → 视觉抖动。改为不自动关闭，仅提示「已插入公式到题目/答案画布」。
2. **题库勾选入口丢失**：删「选入练习卷」后没有选题方式，底部「生成练习卷」条永远出不来。目录每题加复选框 + 目录头「全选 / 取消全选 / 清空」。
3. **试卷详情「题目+答案合卷」不记导出日志**：原本是 `<a target=_blank>` 直开，改成 `openView('all')`，与另两卷一致写入 `export_logs`。
4. **普通试卷没有 PDF 下载**：新增「下载 PDF」按钮（`GET /api/papers/{id}/export`），并记录导出版本指纹。
