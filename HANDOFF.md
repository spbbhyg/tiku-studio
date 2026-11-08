# tiku-studio 交接书（第九版）

> 更新于 **2026-11-08 01:40（Asia/Shanghai）**。第八版新增「公共题库首次发布排错、增量发布、不发布开关、题库顶部工具条」；第七版新增「公共题库（GitHub 同步）」；前面几版（画布撤销重做/拼接/自动分类/裁剪、人工整卷、单题模式、整卷导入）均保留有效。
> 项目根：`C:/Users/Administrator/Documents/tiku-studio`

---

## 0. 一句话现状

题库、出题画布（含撤销/重做、图文拼接）、单题模式、拆题入库、整卷导入、人工整卷、组卷导出、精读、智能出题、错题集、设置，以及 **公共题库（GitHub 同步）** 全部可用；
`pytest` **20 passed**，接口 **173 条**，服务在 `http://127.0.0.1:8100` 常驻（计划任务 `tiku-studio` 开机自启）。

---

## 1. 启动 / 重启 / 排障

### 正常启动

```bat
cd /d C:\Users\Administrator\Documents\tiku-studio
start.bat
```

后台等价命令（由 `run-hidden.vbs` + 计划任务调用）：

```
uvicorn app.main:app --app-dir apps/api --host 0.0.0.0 --port 8100 >> data/server.log 2>&1
```

### 重启（改完后端 Python 后必须做）

```bash
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*uvicorn*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
sleep 3
schtasks //Run //TN "tiku-studio"
sleep 16
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8100/api/stats   # 必须是 200
```

### 改完前端必须重新构建

```bash
cd apps/web && "/c/Program Files/nodejs/npm.cmd" run build
```
构建产物在 `apps/web/dist/`，服务直接读它；**不 build 就看不到改动**。

### 看日志（GBK/UTF-8 混杂，别用 sed/grep 直接看中文）

```python
import io
print(io.open(r"C:/Users/Administrator/Documents/tiku-studio/data/server.log", "rb").read().decode("utf-8", errors="replace")[-4000:])
```

### 关机 / 取消关机

```powershell
shutdown.exe /s /t 120     # 关机（bash 里传中文注释会失败，用 pwsh）
shutdown.exe /a            # 取消
```

---

## 2. 目录与关键文件

### 后端 `apps/api/app/`

| 文件 | 作用 |
| --- | --- |
| `main.py` | 注册全部路由。**新增 router 要改两处**：`from .routers import (...)` 和 `app.include_router(...)` |
| `models.py` / `models_v2.py` / `models_wb.py` | 旧题库 / v2 题组小问 / 工作台（分类、错题集、导出记录） |
| `routers/` | `ai` `authoring` `books` `exam`（**人工整卷：草稿/预览/导出**）`imports` `inbox` `manual` `meta` `paper_canvas` `papers` `questions` `reader` `sets` `settings` `v2` `wb_extra` `wb_split` `workbench` |
| `tools/pdf_import/` | **独立命令行**（`cli.py`）：PDF → 结构化题面 JSON。PyMuPDF 是 AGPL，主程序只通过 subprocess 调它，JSON 文件交换 |
| `services/` | `ai`（命题/答案/翻译）`exam_render`（**整卷 HTML + Playwright 出 PDF**）`authoring_export` `bundle_import` `compose_render` `key_sentences` `ofm`（内置免费模型）`paper_builder` `paper_parse` `pdf_render` `qimage` `scope` `site_guide` `split_images`（**按题号切图+去白边**）`imgclean`（**去水印：删 PDF 里的浅色大图层**）`topic_classify`（**关键词判专题**）`v2_export` `versions` `web_search` |

### 前端 `apps/web/src/`

- `views/`：`Dashboard` `Bank`（**题库三栏，主入口**）`Questions`（旧版）`Authoring`（**出题画布**）`Papers` `PaperView` `Reader`（精读）`SmartPaper`（智能出题）`WrongBook`（**错题集，独立页**）`Settings` `TagsAdmin` `Extract` `Compose` `ComposeCanvas`、`ExamHome`（**出整卷工作台/草稿列表**）`PaperMaker`（**整卷编辑器**）`Ingest`（**整卷导入·人工校对**）、`v2/Editor`（题组 v2）
- `components/`：`CanvasPane`（画布核心）`QuestionSheet`（**结构化题面渲染，card/a4 两壳**）`CropDialog`（**从文献裁图弹窗，整卷/单题共用**）`ChemDock`（**悬浮公式/结构式面板，整卷/单题共用**）`TextMode`（**单题模式编辑器**）`FormulaPad` `ChemPanel`（结构式：SMILES→RDKit / 手绘 Ketcher）`RichField` `Icon`（图标库）`AiQuota` `CategoryAdmin` `WrongBooks` `QuestionCard`
- 路由（hash 模式）：`/` `/author` `/questions` `/questions/classic` `/compose` `/compose/canvas` `/exam`（出整卷）`/ingest`（整卷导入）`/papers` `/papers/new` `/papers/:id/edit` `/papers/:id` `/v2/editor/:id?` `/wrong`（错题集，自动落在第一本）`/wrong/:id` `/reader` `/smart` `/settings`

### 数据 `data/`

- `tiku.db`：SQLite 主库
- `library/`：题图（`library/questions/`、`library/split/<stamp>/`、`library/stitch/<stamp>/` 拼接产物）
- `samples/`：验收截图（`wb*.png`）
- `uploads/`：上传的 PDF
- `exports/`：精读标注版 PDF
- `server.log`：服务日志

---

## 3. 已实现的功能

| 模块 | 入口 | 说明 |
| --- | --- | --- |
| 题库 | `/#/questions` | 三栏（专题/目录/阅读），可折叠；勾选后底部条出现「清空 / 删除所选 / 生成练习卷」；右上角绿色 **拆题**、灰蓝 **自动分类** |
| 拆题 | 题库右上角 | 选题目卷 + 答案卷 → 按「第 N 题」**纯截图**（不识别文字），自动去白边，自动判专题，可全部入库（题干图 + 答案图） |
| 出题 | `/#/author` | WPS 式：顶部工具栏（PDF/裁剪/公式/结构式/小问/AI 答案/新建/撤销/重做/保存…），左右画布标签页，右侧 dock（PDF/公式/结构式/小问），图片可染色；**撤销/重做**（Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y）；选中 ≥2 块可**拼接**（多图拼一张 / 多段文字合并） |
| 组卷 | 题库勾选 → 生成练习卷 | 支持排序、按教材自动组卷、导出 PDF/HTML |
| **出整卷** | `/#/exam` | 侧边栏独立入口（在「试卷」之前）：草稿列表（卷名/大题/小问/总分/建卷时间）+ 「+ 新建整卷」+ 每行「继续编辑 / 完成出卷 / 删除」。**没出完就是草稿**，只有草稿进这里；出完自动进试卷列表 |
| **人工整卷** | `/#/exam` → 新建 / 继续编辑（编辑器地址 `/#/papers/:id/edit`） | 文字流排版（不是画布）：大题 → 小问（最深四级）→ 题干/图/表/公式/分值/答案/给分点；题号·小问号·图号·分值占比全自动；左侧大纲树 + 右侧 A4 实时预览（题目卷/答案卷切换、100%/150%）；`✂ 从文献裁图` 300dpi + 去白边 + 记出处可重裁；`✨ AI 出草稿` 改完才入卷；导出「题目卷 / 答案卷」两份文字可搜索的 A4 PDF。顶栏有 `草稿/已出完` 徽标 + `完成出卷` 按钮 |
| **单题模式** | `/#/author?mode=text`（顶栏「画布 ｜ 单题」） | 不画画布，直接录一道大题的**结构化题面**：标题/分值/占比 + 题干（`$…$`、`\ce{}`、`_{}`）+ 图/表 + 递归小问（题干/答案/给分点）；右侧 A4 实时预览；存进题库 `questions.layout`（`kind:"text"`），题库卡片用结构化渲染，文字可搜可复制 |
| **整卷导入** | `/#/ingest` | 选题目卷 PDF（+ 可选参考答案）→ 独立进程解析成 9 道大题的结构化题面 → 表格里逐题预览/勾选/**并入上一题** → 批量入库。6 页卷约 6 秒 |
| 智能出题 | `/#/smart` | 选 PDF → 后台任务出题（3 题 60~90 秒），可导入题库 |
| 精读 | `/#/reader` | 章节/重点句、逐句译文与批注、导出标注版 PDF（写 `data/exports/`） |
| 错题集 | 侧边栏「错题集」`/#/wrong` | 多本错题集、备注、导出 HTML/CSV；进页面自动打开第一本 |
| 题组 v2 | `/#/v2/editor` | 一题多小问 + 富文本公式（Tiptap），组卷篮、模板、版本 |
| **公共题库** | `/#/settings` → 常规「公共题库（GitHub 同步）」；题库右上角青色「更新题库」 | 仓库 [`spbbhyg/tiku-public`](https://github.com/spbbhyg/tiku-public)：所有者发布题包，别人点一下增量拉取。令牌走 DPAPI 存本机，**只有运行服务的这台电脑能发布**（本机校验），拉取不限 |
| 设置 | `/#/settings` | 常规（数据位置/存储清理/公共题库）、AI 模型（内置免费模型 + 预设）、快捷键、分类、标签、AI 助手 |

---

## 4. 数据模型要点

- `Question.content_hash` **NOT NULL**，新建题必须 `_question_hash(...)`（`services/bundle_import.py`，sha256 前 32 位）。
- `Question.images` 是 JSON 列表，元素 `{"path": "library/questions/..."}`；`answer_layout` 结构 `{"images": [...]}`。
- `QuestionOut`（`apps/api/app/schemas.py`）要显式列出字段，漏了前端就收不到（`answer_layout` 曾因此丢）。
- 图路径统一存**相对 `data/library` 的正斜杠路径**（`split/2026.../q01_1.png`）；后端返回 Windows 反斜杠会导致二次请求 404（染色 bug 根因）。
- 试卷导出：`GET /api/papers/{id}/export?part=questions|answers`，渲染在 `services/pdf_render.py`；纯图题**不再输出**「简答」分组标题、`(N分)`、`第 N 题` 文字前缀。

---

## 5. 最近的改动（2026-11-06 ~ 11-08）

1. **拆题改纯截图**：`services/split_images.py` + `routers/wb_split.py`，题目卷/答案卷都切图、PIL 去白边、`topic_classify` 自动判专题。实测 9 题 → 9 张题目图 + 9 张答案图。
2. **题库入口**：拆题按钮移到题库右上角；删除所选（前端只发 id 数组）；答案图在「查看参考答案」里直接显示。
3. **试卷渲染修复**：单题 PDF 不再空白（图片进 PDF，宽度 100%）；答案卷有内容（`answer_layout.images`）；去掉「简答/分数/第 N 题」前缀（`pdf_render.py` 的 `_NAME_PREFIX`、`all_img` 判断）。
4. **AI 答案修复**：`generate_answer` 的 `max_tokens` 3000→**16000**，空答案自动压篇幅重试；实测 10 秒返回，且成功后会**自动放进答案画布**。
5. **染色二次失败修复**：`/api/wb/recolor` 返回 `as_posix()` 路径，入参也做 `\`→`/` 归一化。
6. **出题加速**：`generate_questions` 改 `ThreadPoolExecutor(3)` 逐题并发生成，3 题 60~90 秒。
7. **批量自动分类**：`POST /api/wb/questions/reclassify`（后端已通，实测 classified 7 / skipped 2 / 约 162 秒），**UI 按钮还没接**。
8. **技能**：装了 6 个 DSH 技能到 `C:/Users/Administrator/.dsh/skills`（`pdf`、`webapp-testing`、`frontend-design`、`systematic-debugging`、`verification-before-completion`、`writing-plans`），描述与「中文速览」已中文化。

### 2026-11-07 下午（第三版新增，本节四项全部验收通过）

9. **出题画布撤销/重做**：`Authoring.vue` 内建双画布历史栈（题目/答案各一条，JSON 快照、去重、上限 80 步、400ms 防抖合并连续拖拽）。入口：工具栏 ⟲/⟳ 两个按钮（`disabled` 绑定 `canUndo/canRedo`）、`Ctrl+Z`、`Ctrl+Shift+Z`、`Ctrl+Y`。切题/清空/载入会 `resetHist`，不会把上一题的历史带过来。实测：插 3 段文字逐步撤销 3→2→1→0、重做 1→2、拖动 111px 后 `Ctrl+Z` 回位。
10. **图片/文字拼接**：后端 `POST /api/wb/stitch`（`routers/wb_split.py` 末尾，PIL；竖拼统一最大宽、横拼统一最大高，白底，gap 0~200 默认 12，路径必须落在 `data/library` 内，产物 `library/stitch/<时间戳>/stitch_N{v|h}_{ms}.png`）；前端 `CanvasPane.vue` 的 `stitchSel()` + 工具栏拼接按钮（「组合」左边，「拼接所选」）：选 ≥2 张图自动判横/竖拼、拼完删原块、按选区 bbox 放新块、**新图超过页面剩余高度会等比缩**；选 ≥2 段文字按阅读顺序合并成一段并保留第一段样式。实测竖拼 1074×1747、横拼 5698×1409，整套操作可 `Ctrl+Z` 回退。
11. **自动分类按钮 + 两个真 bug**：题库右上角 `.cls-fab`（拆题下方）→ `POST /api/wb/questions/reclassify`，绿色提示「已分类 N 题，跳过 M 题（S 秒）」。修了 ① `reclassify_questions` 只看题干长度决定是否 OCR，旧 9 题 stem 是「第 N 题 xxx」标题（≥12 字），关键词判不出 → 永远 skipped；改成判不出且 `images` 非空就先 OCR 首图再判。② 题库专题栏/筛选/chip 只读 `tags`（空），分类结果写在 `knowledge_points` → 分类后计数全是 0；统一改用 `kpOf(x)`。③ 顺手修了文字块 Shift+点不能多选（`onBlkDown` 的 `.tt` 分支无视 `shiftKey`）。实测 9 题：有机化学×3、晶体化学×2、无机综合与结构推断×2、电化学×1、动力学×1，耗时约 130 秒（OCR 慢）。
12. **原图裁剪验收 + 滚轮修复**：裁剪大窗里真正能滚的是内层 `.viewer`（`scrollHeight` 2187 vs `clientHeight` 753），旧 `onCropWheel` 却在改 `.cropbody.scrollTop`（恒 0）且 `preventDefault` 掉了原生滚动 → **滚轮完全不能滚，只能拖滚动条**。已改为滚 `viewerEl`（`deltaMode=1` 乘 16、`deltaMode=2` 乘可视高），滚一格 100px；`Ctrl+滚轮` 缩放显示（100%→110%），`Shift+滚轮` 横滚。端到端实测：拖框 → 裁剪到题目 → 画布出现 345×275 图块（`/api/images/questions/...`）→ `Ctrl+Z` 撤销干净。
13. **冒烟脚本修正**：`check_all_final.js` 的「功能轨」还在查 `.wrail .rb`（左栏已改顶部工具栏，`wrail` 只剩 scoped 死 CSS），改成 `.wtools .wt` 后 10 个按钮；9 页仍 `ERRORS []`。

### 2026-11-07 傍晚（第四版新增：人工整卷）

14. **人工整卷（文献改编卷）**：全新一条文字流排版链路，不复用 `ComposeCanvas.vue` 的绝对定位画布。
   - 后端：`services/exam_render.py`（新建，约 700 行）出 HTML + `routers/exam.py`（新建，约 240 行）出接口，接口总数 **149 → 158**。
     模板默认值 `DEFAULT_TEMPLATE`：A4、边距 90/80/72.5/82、正文 SimSun/Times 10.45、上下标 6.85、行距 15.6pt（CSS 里按 `20.8px` 下发，见坑 16）、题号 `第{n} 题{title}（{score} 分，占{percent}%）`、图最大宽 415 / 图注 6.8 / `autoNumber`、表 0.5pt、答案红 `#FF0000`、分值蓝 `#0000FF`。
     接口：`POST/GET/PUT /api/exam/papers[/{id}]`、`POST /api/exam/papers/{id}/duplicate`、`POST /api/exam/preview`、`POST /api/exam/check`、`GET /api/exam/html/{token}`（预览缓存 30 分钟）、`GET /api/exam/papers/{id}/view?part=`、`GET /api/exam/papers/{id}/export?part=questions|answers|all`、`POST /api/exam/draft/accept`。
     数据仍走 `papers` + `paper_questions`（`question_id=NULL`，`paper.config={"kind":"manual",...}`，题目树存 `paper_questions.snapshot`），所以试卷列表 / PaperView / 导出入口全部白拿；`papers.py` 的 `view_paper`/`export_paper` 遇 `config.kind=="manual"` 自动转 `exam_render`。
   - 前端：`views/PaperMaker.vue`（新建，约 1100 行）+ 路由 `/papers/new`、`/papers/:id/edit`；`Papers.vue` 加「新建人工整卷」入口 + 「人工整卷」标记；`PaperView.vue` 加 manual 分支（继续编辑 / 两道 PDF / 预览）；`Reader.vue` 的「用这段出题」改成写 `sessionStorage['tiku:exam-seed']` 后直接开新卷（正文+出处预填）。
   - KaTeX：`apps/web/public/katex`（从 `node_modules/katex/dist` 拷，删 ttf/woff 只留 woff2，2.2 MB），导出页走 `/katex/...` 加载 `katex.min.js` + `contrib/mhchem.min.js` + `contrib/auto-render.min.js`；Playwright 改为 `page.goto(http://127.0.0.1:8100/api/exam/html/{token})` 而不是 `set_content`，等 `window.__examReady === true` 再 `page.pdf()`。
   - 验收（8 条全跑过，截图 `data/samples/exam-0*.png`）：单题对齐边距 90/79.02/73.94、字号 10.45/6.85、行距 15.75（样卷 15.6，差 0.15）、大题头逐字一致；裁图 300dpi + 去白边 + 图号 1/2/3 + 重裁回原页选区还在；AI 选段 → 2 条草稿 → 改一处 → 采用入卷 → 出处角标 + 参考文献成对；整卷 9 题 52 小问 156 分 → 题目卷 **6 页**、答案卷 **12 页**；`pdftotext` 能搜到题干/答案/`P4S3`/`Hg2Cl2`/`→`；`/api/stats` questions 仍 9；pytest 20 passed、`check_all_final.js` 9 页 `ERRORS []`；录一道带 3 张图的题脚本化 **33 秒 / 29 次操作**。

### 2026-11-07 晚（第五版新增：出整卷独立界面 + 草稿）

15. **「出整卷」独立成页 + 草稿机制**（用户要求：功能单独一个界面，没编辑完算草稿，试卷页单独一个按钮跳草稿列表，可留多份）。
   - 状态字段：`paper.config.exam.status = "draft" | "done"`。`exam.paper_status(paper)` 统一判定；**老数据没有 status 时**用启发式「有题目算 done、空卷算 draft」，避免历史卷全变草稿。
   - 接口（`routers/exam.py`，接口 158 → **161**）：`GET /api/exam/drafts`（草稿列表 `{drafts:[{id,title,questions,subs,total_score,duration,created_at,status}],count}`）、`POST /api/exam/papers/{id}/finish`（草稿→正式）、`POST /api/exam/papers/{id}/reopen`（正式→草稿）；`create_paper` 建卷即 `draft`；`doc_from_paper` 返回里带 `status`；`apply_doc` 会把 `doc.status` 写回（前端导出/完成后同步）。
   - **导出即完成**：`GET /api/exam/papers/{id}/export` 成功后自动置 `done`（老师导出 PDF 就等于交付了，不用再点一次）；想继续改就到试卷页点「改回草稿」。
   - 试卷列表过滤（`routers/papers.py: list_papers`）：`kind=="manual" and status=="draft"` 默认**不返回**（`?include_drafts=1` 可取全量），`PaperBrief` 加 `status` 字段（`schemas.py`）。
   - 前端：新建 `views/ExamHome.vue`（出整卷工作台：草稿表 + 新建 + 继续编辑/完成出卷/删除），路由 `/#/exam`，侧边栏 `navs` 加「出整卷」（放在「试卷」之前）；`Papers.vue` 顶部改成「出整卷 / 查看草稿（N）」两个按钮 + 人工卷行内「改回草稿」；`PaperMaker.vue` 顶栏加 `草稿/已出完` 徽标 + `完成出卷` 按钮（`finishPaper()`），`emptyDoc()` 带 `status:'draft'`，`fixDoc()` 遇到空卷自动摆一个大题（新卷不再空白），返回链接改成 `← 出整卷`。
   - 验收（`pw/check_exam.js` / `check_exam2.js` / `check_exam3.js`，截图 `data/samples/exam-08~11-*.png`）：侧边栏有「出整卷」且在「试卷」之前；新建 → 编辑器徽标「草稿」；回工作台草稿表出现该卷（1 大题 8 小问 14 分）；试卷列表**不含**草稿但按钮显示「查看草稿（1）」；点它跳到 `/#/exam`；继续编辑 → 完成出卷 → 自动回 `/#/papers` 且该卷带「人工整卷」、草稿数归 0；试卷页「改回草稿」→ 回列表消失、草稿数回 1；全程 `ERRORS []`。

### 2026-11-07 深夜（第六版新增：题库结构化 + 出题双模式）

16. **单题模式 + 结构化题面 + 整卷导入**（两张提示词：出题页加「单题模式」、题库题面结构化、PDF 解析隔离成独立进程）。
   - 数据契约：`questions.layout = {v:1, kind:"text", meta:{title,number,score,percent,numbering}, stem:[{t:"p"|"img"|"table",…}], subs:[{no,text,score,answer,points,imgs,subs}], source:{…}}`。冗余字段同步写：`q.stem`（标题 + 题干段落，标题**必须**进 stem，否则题库搜不到题名）、`q.answer`（所有小问答案 + 给分点按序）、`q.images`（`[{path,caption}]` 全量图）、`content_hash = sha1(book|stem|answer)[:32]`（**不能**用画布那套版面哈希，否则同题干不同答案会被覆盖）。
   - 渲染唯一入口 `components/QuestionSheet.vue`：`variant="card"`（题库卡片，14px）与 `variant="a4"`（595.3×842pt，边距 72.5/80/82/90，正文 10.45pt、行距 15.6pt、图注 6.8pt 居中、表 0.5pt 全框、答案 A4 `#FF0000`/卡内 `#b91c1c`、给分点 A4 `#0000FF`）。行内公式走 `mathx.js`：`$…$` 交给 KaTeX，`\ce{…}` 直接翻成 `<sub>` 纯文本（PDF 文字层可搜）。
   - 接口（接口 161 → **164**）：`POST /api/authoring/compose-text`（单题入库）、`POST /api/authoring/pdf-parse`（multipart：题目卷 + 可选答案卷 → 结构化题面）、`POST /api/authoring/pdf-import`（批量入库，逐题走 compose-text 那套冗余字段与自动分类）；`GET /api/authoring/compose/{id}` 多返回 `layout_kind`；`QuestionOut` 加 `layout`。
   - PDF 解析隔离：`tools/pdf_import/cli.py`（独立目录，只 import pymupdf），后端用 `subprocess.run` 调它、JSON 文件交换，**主程序不 import AGPL 库**。规则：页眉页脚（≥60% 页面重复）、大题 `^第 N 题…（M 分，占 P%）`（含中文数字）、小问 `^\d+-\d+(?:-\d+)*`、跨页续接、图区按 `get_image_info` 抽 300dpi PNG 并跳过面积 >35% 的水印、答案卷按「共 N 分」切块 + 红/蓝字分流（颜色不可靠时按「短句 + 含 N 分」判给分说明）。
   - 前端：`Authoring.vue` 顶栏 `.modesw` 分段「画布 ｜ 单题」（`?mode=text` + `localStorage['tiku-author-mode']`，切换有未保存改动会 confirm；`onKeys` 首行 `if (editMode === 'text') return`，画布快捷键不外泄）；`components/TextMode.vue`（题干/小问/图/表/公式 + A4 实时预览 + 撤销）；`views/Ingest.vue`（`/#/ingest` 校对页：上传 → 解析 → 表格逐题预览/勾选/并入上一题 → 批量入库）；`Bank.vue` 卡片按 `layout.kind` 双分支。
   - 验收（`pw/check_text.js`、`pw/shot_text.js`、`pw/check_ingest.js`、`exam/mk_text1.py`、`exam/test_pdfimp.py`）：
     - A：`items 9 / subs 52 / score 156 / percent [8,9,13,9,10,18,8,12,13] / images 15 / page_images_p5 6（原始 7）/ doc_id 8610e61592a8`；6 页卷解析 **6.2~7.1 秒**。
     - C：题库卡片文字可选、图行内、`1-1` 加粗、答案可折叠、`1-4-1` 嵌套缩进（截图 `data/samples/text-02-bank.png`）。
     - D：模式条存在、A4 预览、刷新保持、互斥提示卡（画布模式打开 text 题时提示「这是单题模式录的题」）、`ERRORS []`。
     - E：`pytest 20 passed`、`check_all_final.js` 9 页 `ERRORS []` + 功能轨 10、`check_undo.js` / `check_stitch.js` / `check_crop_accept2.js` 全绿（画布行为没改）。
     - 整卷导入：`/#/ingest` 上传两份 PDF → 统计条「页数 6 大题 9 小问 52 分值合计 156 图 15 张 用时 6.4 秒」→ 预览含题名与答案 → 入库勾选题 →「1 道题已入库」→ `ERRORS []`（截图 `data/samples/ingest-01-review.png`、`ingest-02-bank.png`）。

17. **自动去水印（2026-11-07 22:00）**：`apps/api/app/services/imgclean.py` —— 竞赛卷/教辅的水印往往是 PDF 里**单独一层浅色大图**，渲染前 `page.delete_image(xref)` 删掉即可，文字与公式在别的图层上，删完版面不变。
    - 判定：面积 ≥30% 页 + 图片平均亮度 ≥220 + 平均饱和度 ≤12（宁可漏删不误删正文大图）；`strip_watermarks(page, doc)` / `strip_doc(doc)`。
    - 接线三处：`routers/manual.py` 的页码图 `page_image()`（缓存名加 `-wm` 后缀，旧缓存自动失效）与 `crop_page()`（`CropIn.clean` 默认 True）；`tools/pdf_import/cli.py` 的 `build_docs(..., clean=True)`（`--keep-watermark` 可关，工具目录内联同一份逻辑，不 import 主程序）。
    - 实测：样卷第 1 页水印 xref 8（area 0.368 / meanL 249 / sat 0）删除后整页干净，裁出的 `p1-1.png`(445×649)、`a1-1.png`(162×165) 无底纹；对比图 `data/samples/wm-01-before-after.png`。
    - 重新导入 9 道结构化题（id 10~18）：`check_bank_fix.js 10` → 小问分值 0 / sub 63 / sup 6 / 乱码 false / 答案图 3 / PDF 41.6KB+495KB / `ERRORS []`；`pytest 20 passed`；`check_all_final.js` 9 页 `ERRORS []`；`check_recrop.js 23` 全绿。

### 2026-11-08 凌晨（第七版新增：公共题库 GitHub 同步）

18. **公共题库（GitHub 同步）**：公开仓库 `github.com/spbbhyg/tiku-public`，只有所有者能写，别人只读、点一下「更新题库」就能拉。
   - 后端 `services/github_sync.py`：题包 = `packs/<UTC 时间>-<seq>.jsonl`（一行一题）+ `images/<sha1><ext>` + `manifest.json{schema,owner,seq,counts,packs[]}`。发布走 **Git Data API 一次提交**（blob→tree→commit→PATCH ref），不 clone、不碰工作区；单次 ≤45MB。拉取按 `seq` 增量，`(book_id, content_hash)` 去重，图片落 `library/public/<sha1><ext>`，远端题进自动建的 `公共题库（GitHub）· owner/repo`（`status=confirmed`）。
   - 令牌来源：环境变量 `TIKU_GITHUB_TOKEN` → `data/.secrets/github.json`（DPAPI 加密）→ `gh auth token`。**这台机器 `gh` 与 GCM 都是坏的**（系统 `credential.helper=helper-selector` 会拉起不可见的 GCM 窗口卡 60 秒），所以 `detect_token` 里已经删掉 `git credential fill`。
   - 接口（164 → **173**）：`/api/github/status|config|token|preview|publish|pull`；`publish` 和令牌写入只放行本机（`_local_only`：`127.0.0.1/::1/localhost`，否则 403 `ERR_LOCAL_ONLY`），`pull` 不限。
   - 前端：设置 → 常规 →「公共题库（GitHub 同步）」卡片（仓库 owner/repo、下载源 jsDelivr/raw/ghproxy、令牌框、连草稿一起发布、立即发布/一键更新/刷新状态 + 状态 chips）；题库顶部工具条（`.btop`，固定不遮正文）里的「拆题 / 自动分类 / 更新题库」三颗按钮。
   - 验证：`test_gh_sync.py`（本地假 GitHub API）发布两包、二次 pull `up_to_date`、空库导入 3 题跳 2 题；线上 `status` 1.3 秒、`pull` `up_to_date`、`publish` 无令牌时 `ERR_NO_TOKEN`；UI `check_gh_ui.js` 全绿，截图 `data/samples/wb28-github-settings.png`、`wb29-bank-update-btn.png`、`wb30-bank-update-result.png`。
   - **首次发布已完成**：`b9f101f7 publish: 9 questions (1)`，仓库 `manifest.seq=1`、9 题 25 张图，`packs/20261107-151437-1.jsonl`。
   - 增量发布：状态里存 `published`（content_hash → seq）与 `published_images`（sha1+ext），已发过的题和已传过的图不会重发；再次点「立即发布」直接返回 `up_to_date`（实测 0.8 秒）。想强制重发传 `{"force": true}`。
   - 不想发布的题：题库阅读区有开关「公共库：会发布 / 已排除」（`POST /api/github/skip {id, skip}`，存在 `skip_ids`），被排除的题不进 `publishable()`，也不进 `preview`；本地照常使用。**GitHub 上手动删题没用**——下次发布只会补新题，但已发出去的包不会撤回（要撤回得改 manifest + 删包，目前没做）。

### 2026-11-08 01:30（第八版新增：发布排错 / 增量 / 排除开关 / 工具条）

19. **首次发布排错**：用户点发布报 `ERR_NETWORK: 连不上 GitHub：The read operation timed out`（502）。逐段测速：`/user` 0.9s、`manifest` 0.5s、单张 200KB blob 2.0s，**`POST /git/trees` 实测 63.0s**，被旧 `_http(timeout=20)` 判死（GitHub 其实已收到大部分内容）。修法：`_http` 加 `retries=2`（只重试网络层）+ 默认 60s；blob 120s、tree 240s、commit/ref 120s。重试后提交成功：`b9f101f7 publish: 9 questions (1)`。**一次全量发布要 1~3 分钟，属正常。**
20. **增量发布**：状态里存 `published`（content_hash → seq）和 `published_images`（sha1+ext），只发新题、只传新图；全发过时 `publish` 直接返回 `up_to_date`（实测 0.8 秒）。`PublishIn.force=true` 可强制重发。已用 `backfill_state.py` 把 9 题 25 图回填为已发布。
21. **不发布开关**：`skip_ids` 存 Setting，`publishable()` 过滤，`POST /api/github/skip {id, skip}`；`status` 返回 `skip_ids` 与已发布计数；`preview` 同步生效（排除第 18 题后 9 题 25 图 → 8 题 16 图）。前端在题库阅读区 `.rd-act` 第一颗按钮，琥珀色 `#b45309`。
22. **题库顶部工具条**：`拆题 / 自动分类 / 更新题库` 原来是三个 `position:absolute` 浮按钮（`top:10/58/106px`），会压住正文。现在改成 `.bank` 网格第一行 `.btop`（`grid-column: 1 / -1`，`justify-content:flex-end`，`padding:7px 12px`，底边细线），按钮改为静态流式排布、图标 17px；提示条 `.cls-msg` 也移进条内（`position:static`，`margin-right:auto`），不再浮在正文上。实测：条 y=25 h=50、正文从 y=75 开始、滚动正文条不动、提示出现时条高不变。截图 `data/samples/wb33-bank-topbar.png`、`wb34-bank-topbar-msg.png`。

### 2026-11-08 02:10（第九版新增：导出统一 + 试题篮）

23. **导出只有一个入口**：`POST /api/papers/{id}/export.pdf`，body `{part:'questions'|'answers', title, header, footer, save:1}`，内部 `paper_doc.build_doc()` → `exam_render.export_pdf()`。GET 版仍保留（`?part=&title=&header=&footer=`）。返回文件名 `{标题}_{题目卷|答案卷}.pdf`，`save=1` 时把 title/header/footer 记进 `paper.config.exam.export`，下次打开自动回填。
24. **一份渲染器吃三种卷**：`services/paper_doc.py` 的 `item_from_snapshot()` 按形状分派——`kind=text` 的结构化题走 `item_from_doc()`、扁平快照走 `item_from_flat()`、画布题（stem 为空、只有整页图）给 `width=FULL_W(424)` 并把模板 `figure.maxWidth/maxHeight` 抬到 424/690。单题导出（`/api/exam/question/{id}/export`）也改用它，画布题不再 400。
25. **前端统一组件** `components/PaperExport.vue`：一个「导出 PDF」按钮 + 对话框（试卷标题 / 页眉 / 页脚 + 导出题目卷、导出答案卷）。`PaperView.vue`、`PaperMaker.vue` 都用它；`Papers.vue` 行内三个导出按钮删掉，改成一个「打开 / 导出」；`Questions.vue`（旧列表）的导出面板整个删除。**以后加导出功能只改这一个组件。**
26. **试题篮**（`Bank.vue`）：题目目录每一行 `draggable`，拖到右下角「试题篮」或点行尾「＋」；篮子面板可改每题分值、移出、清空，点「组卷并打开试卷」→ `POST /api/wb/papers`（`BasketPaperIn`，`config.source=basket`）→ 跳 `/#/papers/{id}`。原来的底部「生成练习卷」条已被它替代。
27. **排版两处修正**：`.figrow` 的 `flex-wrap` 从 `nowrap` 改 `wrap`（多图不再挤成一排缩略图）、`img max-height:240pt !important`（大图不再整块被推到下一页）。注意 `export_pdf` 里 `print_background=True`、`prefer_css_page_size=True`，边距 90/80/72.5/82 由 template 控制。
28. **一个假 bug，别再查**：用 curl 带中文 `title` 导出，PDF 标题会变成 `೦೦೦Ա೦೦೦`。原因是 Git Bash/MSYS 的 curl 把命令行中文按 **GBK** 发出去。产品没问题——用 `--data-binary @body.json`（UTF-8 文件）或从网页导出都正常。

29. **导出排版按样卷对齐（第九版补）**：①`_figures()` 原来把 N 张图平分一行，7 张会缩成 52pt 缩略图；改为**每行两张**（每张 203.5pt）。②小题若只有一块内容（无子问），不再显示「1-1」编号；答案卷改显示蓝字「本题共 N 分」。③`.sub` 的 `break-inside` 从 `avoid` 改 `auto`，答案卷不再出现整页空白（原来第一题整块被推到第 2 页）。④扁平题的首行短标题（≤30 字）提到大题标题上，显示成「第 1 题 van Leusen 试剂（5 分，占 33%）」。
30. **为什么有的题「导出只有图片」**：这 9 道是「拆题」导入的，题干其实是**按页切的图片**（`images` 里 `p5-1.png`… + 答案图），文字只有一行标题。渲染器只能照着数据排——要图文混排得用「单题模式」重录，或让拆题保留文字层。
18. **图归到具体小问（2026-11-08 11:50）**：以前图片只按「大题」落到题首，同一页里属于 7-2 的那张图会跑到 7-1 前面，看着像「把答案裁进了题目」。
    - `tools/pdf_import/cli.py`：`build_docs` 里先按阅读顺序给每页每一行记下（当时属于哪个大题/小问），图片按「图上方最近的一行」归属；挂在 `sub.imgs` 而不是 `it.imgs`。
    - 同时修了两个连带 bug：`clean_sub()` 原来把 `imgs` 写死成 `[]`（图挂到小问就被丢掉）；`compose_text` 的「题干是空的」校验放开——题干只剩标题、图都在小问里的题不再被拒。
    - 实测第 7 题（id 17）：7-1 带 A–D 结构图、7-2 带大小关系图，位置与卷面一致；`check_all_final.js` 9 页 `ERRORS []`、`pytest 20 passed`。
    - 截图 `data/samples/fix-02-sub-images.png`。


19. **删掉 QF题库 / 资料库（2026-11-08 11:55）**：QF 那套是另一个工具的库导入，实际没用过，整块清掉。
    - 前端：删 `views/QuizForge.vue`、`views/Books.vue`、路由 `/quizforge` `/books`、侧边栏「QF题库」与它的题库子导航（`qfBanks`）；旧版题库页 `/#/questions/classic` 里那块 QuizForge 导入弹窗也一并剥掉，只留整卷 PDF 导入。
    - 后端：`routers/imports.py` 只保留 `/api/import/paper`、`/api/import/papers`；删 `services/quizforge_import.py`；`services/site_guide.py` 去掉 QF 那条。
    - 错题集升为侧边栏独立入口（`/#/wrong`），进页面自动落到第一本（没有就停在列表）。
    - 校验：`/openapi.json` 173 条（无 quizforge）、`pytest 20 passed`、`check_all_final.js` 10 页 `ERRORS []`、`/#/quizforge` 与 `/#/books` 都回落到首页。

20. **单题模式补上「从文献裁图」+ 修右侧空白（2026-11-08 12:20）**：出题页原来只能「＋图」上传本机图片，没法直接从库里文献裁；而且单题模式被 `.writer` 的两列网格挤在左半边，右边空一大块。
    - 抽公共组件 `components/CropDialog.vue`：选文献 / 翻页 / 拖框（Shift 等比）/ 方向键微调 / Esc / Ctrl+Z / 300dpi / 去白边，`emit('done', {path, caption, width, source:{doc_id,page,kind,rect,dpi}})`；把 `source` 传进来就能定回原页、还原选区（重裁）。
    - `PaperMaker.vue` 改用这个组件：`cropState` 从 9 个字段瘦成 `{open, target}`，删掉约 200 行重复逻辑和 `cropKey` 全局监听；`onCropDone` 负责「重裁替换」或「追加到当前节点」。
    - `TextMode.vue` 同样接上：题干工具条和小问工具条各加「✂ 文献裁图」，裁完直接 push 到 `doc.stem`（img block）或 `sub.imgs`，右侧 A4 预览立刻刷新。
    - 版面：`Authoring.vue` 加 `.writer.wps > .wbody.tmode { grid-column: 1 / -1; ... }` 让它占满整行；`TextMode.vue` 的 `.tm` 补 `flex: 1 1 auto; width: 100%; min-width: 0`（flex 子项不加就按内容收缩，这就是右边空白的真因），`.tmright` 改 `flex: 0 0 520px`。
    - 实测：`.tm` 1093 → **1344px**（左栏 824 + 预览 520，1600 视口无空白）；`check_tmcrop.js` 对话框 1 / 选区 1 / 裁完缩略图 1 / `ERRORS []`；`check_recrop.js 23`（整卷重裁还原选区）全绿；`check_pm.js 36` 52 小问、`ERRORS []`；`check_all_final.js` 10 页 `ERRORS []`；`pytest 20 passed`。
    - 截图：`data/samples/text-03-layout.png`、`text-04-crop.png`。

21. **悬浮的「公式 / 结构式」面板（2026-11-08 12:45）**：单题模式和整卷编辑器右下角各挂一个浮层，写公式、画结构式都能直接插到光标处。
    - 新组件 `components/ChemDock.vue`：两个圆钮「ƒx 公式」「⌬ 结构式」，点开是 360px 浮卡。
      - 公式：LaTeX 输入 + 18 个常用符号（上下标/分式/根号/Δ/→/⇌/↑↓/°/αβγ/Σ∫±×）+ 实时 KaTeX 预览，插入时输出 `$…$`。
      - 结构式：填 SMILES（回车预览）→ `POST /api/v2/chem/preview` 拿 SVG 预览 → 插入时 `POST /api/v2/chem` 建对象、`POST /api/v2/chem/{id}/png` 出图，取 `url` 去掉 `/api/images/` 前缀得到 `chem/xxx.png` 相对路径。
    - 插入目标：两页各自维护 `lastField`（`@focusin` 记录最后聚焦的 textarea），插入时按 `selectionStart/End` 拼接并 `dispatchEvent(new Event('input'))` 让 v-model 同步，光标停在插入内容之后；没聚焦就落到当前题干。**坑**：`@focusin` 必须跳过弹层自己的输入框（`t.closest('.dock')`），否则公式会插进浮层自己的 textarea（第一版就踩了）。
    - 图默认宽度：结构式给 160pt（原来按正文 415pt 会占掉半页）。
    - 实测：单题模式 `$K_{sp}=rac{c^2}{1-lpha}$` 完整落进题干、苯环 SMILES 预览 svg 1 张、裁/插图缩略图 1 张；整卷 paper 36 同样插入成功；`check_all_final.js` 10 页 `ERRORS []`、`check_pm.js 36` 52 小问、`pytest 20 passed`。
    - **手绘（Ketcher）**：浮层第三个页签，`src="/ketcher/index.html"` 的 iframe + postMessage（`ketcher:ping/set/get`），**第一次点才挂载**（30MB 包）；「把 SMILES 载入画板」= `ketcher:set`，「取结构出图并插入」= `ketcher:get` → `POST /api/v2/chem {molfile, svg, renderer:'ketcher'}` → png → 插入。卡片在手绘时加宽到 820px，画板高 430px。
    - 实测（`check_draw.js`）：3 个圆钮｜iframe 1｜卡片 820px｜画板 ready｜载入「已载入画板，可继续增删原子」｜出图「已插入」｜缩略图 1｜`ERRORS []`。
    - 截图：`data/samples/dock-01-formula.png`、`dock-02-struct.png`、`dock-03-paper.png`、`dock-04-draw.png`。

22. **图片尺寸按样卷校准（2026-11-08 12:35）**：之前所有插图一律按 415pt（版心宽）排版，一张苯环占掉半页；现在改成「按原稿真实宽度」。
    - 参照：第 40 届 Chemy 模拟试题合集（220 页）实测 155 张正文级结构图，宽度 **P25=86 / P50=130 / P75=180 / P90=239 pt**，45% 窄于 120pt。据此定结构式插入的三档：**小 80 / 中 110（默认）/ 大 160**，选择记在 `localStorage['tiku:chem-w']`。
    - 解析侧：`tools/pdf_import/cli.py` 的 `save_picture()` 保存后**按白边裁剪**（灰度阈值 246 + 6px 边距），再用裁完的像素宽换算 `w_pt = px × 72 / dpi` 写进图块 `w`；题干图 `w = clamp(40, w_pt, 415)`，答案整块图下限 60。这样题库卡片、A4 预览、导出 PDF 三处都按原稿比例显示。
    - 渲染侧：`QuestionSheet.vue` 四处 `<img>` 都吃 `im.w`（缺省 300）；`exam_render._figure()` 无显式宽度时按 `_natural_size()` 兜底（上限 200pt/260pt，多图并排再等分）。
    - 实测：重解析后题干图宽 `[409, 202, 326, 46, 212, 151, 178]`、答案图 `[40, 92, 356, 255, 191, 46]`，全部等于原稿 bbox；卡片里反应式图约 250–320px（原来撑满 640px）；悬停档位切换实测 110pt → A4 里 79pt（0.72 缩放）、160pt → 115pt。
    - 截图：`data/samples/fix-01-bank-card.png`（第 1 题）、`fix-03-q15.png`（第 6 题，原来那张撑满全屏的反应式图现在正常了）。

---

## 6. 已知问题 / 待办

| 优先级 | 事项 | 位置线索 |
| --- | --- | --- |
| ✅ 已完成 | ② 撤销重做、⑤ 图文拼接、⑦ 自动分类按钮、③ 原图裁剪/滚轮验收 | 见第 5 节 9~12，截图 `data/samples/wb2[0-7]-*.png` |
| 中 | 公共题库尚未发布过（本机没有令牌） | 设置 → 常规 → 粘贴 PAT → 「立即发布」；仓库 `manifest.seq` 现在是 0 |
| 中 | 独立版 zip 落后 | 桌面最新是 `tiku-studio-独立版-20261105-1343.zip`，跑 `更新并打包独立版.cmd`（约 4 分钟） |
| 低 | GitHub 未推送 | 需带 `repo` + `workflow` 权限的新 token，用桌面「发布到GitHub.cmd」 |
| 中 | 试题篮里的题「标题」是空的，导出后每题只显示「第 N 题（X 分，占Y%）」 | 篮子快照没有 title；要么组卷时把题干首行截成标题，要么让用户在篮子里改 |
| 低 | 题库 9 题全是「简答 + 草稿」 | 自动分类已完成，题型/状态还需人工确认 |
| 低 | 自动分类结果会随 OCR 抖动 | 同一张图两次跑可能给出不同专题（实测 分析化学→动力学、高分子化学→晶体化学）。现已改成「已有专题就跳过」，要重判传 `{"force": true}`；根因是 OCR 的是**整页图**，会把邻近题目的文字也读进去 |
| 低 | `Authoring.vue` 里 `.wrail` 是死 CSS（约 20 行） | 左栏早已改成顶部 `.wtools` 工具栏，可清理 |
| 中 | 人工整卷没有「整卷复制/模板另存」按钮 | 后端 `POST /api/exam/papers/{id}/duplicate` 已通，前端还没接；`doc.template` 可在编辑器「模板」抽屉里手改 |
| 中 | 人工整卷的分值只能手填，没有「按小问均分」 | `PaperMaker.vue` 的 `leafSum()`；大批量导入时先用脚本按 `divmod` 均分，再人工微调 |
| 中 | 空题干 / 0 分题 / 题号断档只在导出前提示，不阻止编辑 | `exam_render.check_issues()`；顶栏「⚠ N 条提示」可点开，导出时 confirm 可「仍然导出」 |
| 低 | `$K_{sp}$` 这类真公式（非 `\ce{}`）进 PDF 后文字层有零宽空格 | KaTeX 的 MathML+HTML 双份输出，`output:'html'` 无效；纯化学式已走 `_ce_text()` 直出 HTML，不受影响 |
| 低 | 文献自动解析入卷（`mk_full.py`）会把 `X₁` 读成 `X1`、把图注连同图一起裁进去 | 脚本层问题，不是产品链路；手录或 `X_{1}` 写法都正常 |
| 低 | 草稿没有「自动清理」：建了不用的空草稿会一直留在「出整卷」 | `ExamHome.vue` 每行有「删除」；要批量清就 `DELETE /api/papers/{id}` |
| 低 | 编辑器里没有「改回草稿」按钮（只有「完成出卷」） | 反向操作在试卷列表行内「改回草稿」（`POST /api/exam/papers/{id}/reopen`） |
| 中 | PDF 解析只抽**位图**，纯矢量图形抽不到 | 样卷第 4/5 题的立体结构是矢量绘制，`get_image_info()` 返回空；`tools/pdf_import/cli.py: page_pictures()`。要覆盖就得对页面做区域渲染（按 drawing 聚类），成本更高 |
| 中 | PDF 解析**没有表格结构化** | 表格会被当成普通文字行落进题干；`cli.py` 里还没有表格检测。手录时 `＋表` 可用 |
| 低 | 单题模式插图只能上传本机图片 | `TextMode.vue: pickImage()`；文献裁图在「出整卷」里（`PaperMaker.vue` 的 `✂ 从文献裁图`，300dpi + 去白边 + 记出处可重裁），两处图都落 `data/library/questions/` |
| 低 | 草稿状态存在 `paper.config.exam.status`，SQL 里没法直接筛 | 量小（几十份）时是全表扫后 Python 过滤；真要筛再给 Paper 加列 |
| 低 | 公式排版区（如样卷 6-2-2 的平衡常数表）按字符流拼出来仍然别扭 | PDF 里这块是 Symbol 字体分栏排版，文字顺序本身就是散的；`cli.py` 只能清掉 PUA 乱码（`clean_text`）。这类区域建议整块截图后当图插 |
| 低 | 答案图按「y 坐标最近的『共 N 分』块」归属，偶尔会挂到相邻小问 | `cli.py: color_answers()`；错了就在单题模式里挪图 |
| 低 | 单题导出已统一走 `/api/exam/question/{id}/export`（画布题也支持）；`/api/v2/papers/{id}/export`、`/api/papers/{id}/export-canvas`、`/api/authoring/export` 三个老接口还在后端，前端已不再调用 | 无引用后可删；`export-canvas` 只有画布卷需要 |
| — | 讨论/提问只存在本地库（`question_notes` 表），没有多人同步 | 单人自用够；要多端就换成带用户的口碑表 |
| 中 | 用 Symbol/MT Extra 字体排的数学符号会变成乱码字母（`e9-2= e2 -0F R`） | PDF 里没嵌 Unicode 映射，pymupdf 只能给出字形码；已清掉 PUA 字符，但这类 ASCII 假字符要按字体名做映射表才能还原。目前只能用人工改答案 |
| 低 | 去水印只认「PDF 里的浅色大图层」 | `services/imgclean.py`：面积 ≥30%、平均亮度 ≥220、饱和度 ≤12 才删。水印若是**文字型**或已经烤进扫描图，不会自动去掉；那类需要 OCR 定位或 inpainting，属于另一个量级 |
| 低 | 有些 PDF 的水印和正文在同一个图层里 | 删图层会把正文一起删 → 这种只能退回像素法（阈值压浅灰），本版没做，遇到就手裁时避开 |

---

## 7. 踩过的坑（务必记住）

1. **heredoc 写 Python 补丁会把 `\n` 变成真换行**，直接写坏文件。→ 补丁一律先 `write` 成 `.py` 文件再执行。
2. 字符串里出现 Windows 路径 `C:\Users\...`，`\U` 会被当 unicode 转义 → `SyntaxError: truncated \UXXXXXXXX escape`。→ 用正斜杠或 raw string。
3. 锚点守卫要写 `if "def _repair_json(" not in s`，写函数名片段会被函数体里的调用误判，导致补丁静默不生效。
4. 图标化后按钮无文字，Playwright 要 `.wtools .wt[title^="AI 拆题"]`。
5. Ketcher 产物要同时同步 `apps/web/public/ketcher/` **和** `apps/web/dist/ketcher/`，只改前者会发旧文件。
6. `main.py` 加 router 要改两处，否则 `NameError: name 'wb_split' is not defined` → 服务 000。
7. `app.routes` 只有十几条是**延迟注册**，判断接口在不在要看 `/openapi.json`。
8. A4 = 595×842 pt，正文左边距 `MARGIN=48`；图片按原图像素换算 1:1 才不失真。
9. 面板 `v-if="drawer"` 会与 PDF dock 同时命中，导致两个面板并排、画布只剩 500px。
10. 改 `.vue` 的 scoped 覆盖样式必须写在 `<style scoped>` **末尾**，否则被前面的 `position:fixed` 规则盖掉。
11. `max_tokens` 给小了会被推理型模型吃光，返回空内容（命题/答案都要 ≥12000，复杂任务 16000）。
12. **滚轮要滚对元素**：裁剪弹窗里 `.cropbody` 是 flex 容器（`scrollTop` 恒 0），真正的滚动容器是内层 `.viewer`；在父级 `@wheel.prevent` 后手动滚错元素 = 滚轮彻底失效。
13. **题库专题在 `knowledge_points`，不在 `tags`**：`tags` 还混着「第 3 题 / 5 分」这类来源标签，别再拿它算专题。
14. **`reclassify` 现在有幂等性**：已有 `knowledge_points` 的题默认跳过（防覆盖手改、防两次跑结果不一致），要重判传 `{"force": true}`。
15. **`check_all_final.js` 的「功能轨」早就不是 `.wrail .rb`**：左栏改顶部工具栏后选择器应为 `.wtools .wt`，看到「功能轨 0」先怀疑脚本过期而不是 UI 坏了。
16. **行距别直接写 `line-height: 15.6pt`**：Chrome 会把行盒取整成 21px（=15.75pt），要精确 15.6 必须写 `line-height: 20.8px`（=15.6×96/72）；标题又要按 pt 给，所以 `exam_render._css()` 同时给了 `LH`（pt，给标题）和 `LHPX`（px，给正文）两个占位符。
17. **`_inline()` 的 `$...$` 别全丢给 KaTeX**：KaTeX 的 MathML+HTML 双份输出会让 PDF 文字层出现「`X` 换行 `4 X` 换行 `3`」和零宽空格，`pdftotext` 搜不到 `Hg2Cl2`。现在 `\ce{}` 与整段只有一个 `\ce{}` 的 `$...$` 都先用 `_ce_text()` 直出 `<sub>` HTML，只有真数学式才走 KaTeX。
18. **重启用 `restart_api.ps1` 且必须从 pwsh 直接跑**：从 bash 里 `powershell -File` 起不来新进程（输出还被吞），表现为「改了后端没生效、两次导出 PDF 字节数一模一样」。脚本已改成先按 `name='python.exe'` + `CommandLine -like '*uvicorn*'` 全杀、等 3 秒、再 `schtasks /Run /TN "tiku-studio"`、轮询 `/api/stats`。
19. **CSS 里 `str.format` 会撞花括号**：`exam_render._CSS` 一开始用 `tpl.format(...)` 直接 `KeyError: ' size'`，改成 `__MT__`/`__LH__` 这类占位符 + 逐 token `replace` 才对。
20. **`raw.githubusercontent.com` 在这台机器 TLS 握手能卡 30 秒以上**，`urlopen(timeout=…)` 管不住 → 远端请求一律套守护线程 `join` 超时（`_fetch_with_deadline`），默认走 jsDelivr 镜像。
21. `git credential fill` / GCM 在这台机器上走不通：窗口 `vis=False`，最后 `fatal: could not read Username … terminal prompts disabled`。别再试浏览器登录，要么 PAT，要么 SSH（`~/.ssh/id_tiku` 已配好）。
22. `restart_api.ps1` 里的 `Stop-Process` 杀不掉 uvicorn（报 `left: 2`）→ 用 `taskkill /F /T /PID`。
23. 状态接口在网络上不能一直转圈：连不上就用 `Setting` 里缓存的 `remote_seq/remote_counts/remote_at` 兜底，并标「（上次）」。
24. **jsDelivr / raw 对 `@main` 有 CDN 缓存**：刚发布完读 `manifest.json` 可能还是旧 seq（实测读到 0）。`fetch_manifest` 现在本机有令牌时直接走 GitHub API（`/contents/manifest.json`），没令牌时给 URL 加 `?t=<时间戳>`。
25. `POST /git/trees` 从这台机器要 **60 秒以上**，每个 blob 1~3 秒：一次全量发布会话 1~3 分钟，别用 20 秒超时判死（现在 blob 120s / tree 240s）。前端按钮要能等；状态查询才用短超时。

---

## 8. 常用验证脚本

| 脚本 | 用途 |
| --- | --- |
| `apps/api/tests`（pytest） | 后端基线，必须 **20 passed** |
| `C:/Users/Administrator/Documents/dsh-work/pw/check_all_final.js` | 9 个页面冒烟，期望 `ERRORS []` |
| `.../pw/check_3new.js` | 拆题 / 题库删除 / 翻译按钮 |
| `.../pw/check_reader*.js` | 精读页（选文献、译文、导出） |
| `.../pw/check_cropmodal.js` | 裁剪弹窗（左栏移除、面板宽度、弹窗尺寸） |
| `.../pw/check_undo.js` | 撤销/重做（按钮状态、Ctrl+Z/Y、拖动撤销） |
| `.../pw/check_stitch.js` | 拼接（3 段文字合并、2 张图竖拼、撤销） |
| `.../pw/check_topic_ui.js` | 专题计数/筛选（分类后应 有机化学 3、点进去剩 3 条） |
| `.../pw/check_classify_idem.js` | 自动分类幂等（第二次点击应「已分类 0 题，跳过 9 题」） |
| `.../pw/check_crop_accept2.js` | 裁剪滚轮（一格 100px、Ctrl 缩放）+ 裁剪入库 + 撤销 |
| `.../test_stitch.py` | `/api/wb/stitch` 后端直测（尺寸、400/404、路径穿越） |
| `.../pw/check_pm.js <paper_id>` | 人工整卷：列表入口 / 新建 / 编辑器 / 预览 / 答案卷 |
| `.../pw/check_crop.js` | 裁图全流程（拖框 → 300dpi → 去白边 → 插入） |
| `.../pw/check_recrop.js <paper_id>` | 重裁：出处还原、选区尺寸、方向键 1px / Shift 10px、Esc |
| `.../pw/check_ai.js <paper_id>` | AI 出草稿 → 改一处 → 采用入卷 → 预览含参考文献 |
| `.../pw/check_timing.js` | 限时体验：空卷 → 1 题 3 小问 → 裁 3 图 → 导出两道卷（计时+数操作） |
| `.../exam/measure.py <pdf>` | 量版式：页尺寸、四边距、字号分布、行距、大题头文本 |
| `.../exam/cmp.py <a> <b> <out> <pa> <pb>` | 把两个 PDF 指定页并排渲染成一张对比图 |
| `.../exam/mk_exam1.py <qdoc> <adoc>` | 单题链路：裁 3 图 → 建卷 → 导出 → 抓图注（回归用） |
| `.../exam/mk_full.py` | 整卷链路：从样卷题目/答案 PDF 解析 9 题 52 小问 + 15 张图 → 导出两道卷 |
| `.../pw/check_exam.js` | 出整卷：侧边栏入口 / 新建 / 草稿列表 / 试卷页「查看草稿」/ 完成出卷 |
| `.../pw/check_exam2.js` | 草稿闭环：草稿 → 完成出卷 → 进试卷 → reopen → 回草稿 |
| `.../pw/check_exam3.js` | 试卷页「改回草稿」按钮 + 侧边栏顺序 + 截图 |
| `.../exam/mk_draft.py` | 造一份演示草稿（从 23 复制 + reopen）|
| `.../pw/check_text.js` | 单题模式：模式条 / A4 预览 / 保存入库 / 回填 / 互斥提示卡 / 题库结构化卡片 |
| `.../pw/shot_text.js` | 单题模式 + 题库卡片截图（`text-01-editor.png` / `text-02-bank.png`）|
| `.../pw/check_ingest.js` | 整卷导入：上传两份 PDF → 解析统计 → 预览 → 入库（`ingest-01-review.png` / `ingest-02-bank.png`）|
| `.../exam/mk_text1.py` | 直接把样卷第 1 题写成 doc 存进题库（不经 UI，验证接口）|
| `.../exam/test_pdfimp.py` | 直测 `pdf-parse` + `pdf-import`（multipart 手搓，含耗时与统计打印）|
| `.../test_gh_sync.py` | `/api/github` 直测：假 GitHub API，发布两包 → 空库拉取（3 题 2 跳过）→ 二次 pull `up_to_date` |
| `.../pw/check_gh_ui.js` | 设置页 GitHub 卡片 / 令牌未配置时禁用发布 / 题库「更新题库」 |
| `.../exam/api_paths.py <关键词>` | 读 `/openapi.json` 数接口（Windows 下别用 curl 管道，会 GBK 乱码）|

---

## 9. 给下一个会话的建议顺序

1. 读本文第 1 节，确认服务在跑（`/api/stats` 200）。
2. 跑 `pytest` + `check_all_final.js`，拿到干净基线（期望 `20 passed`、9 页 `ERRORS []`、功能轨 10）。
3. 第 6 节的高优先级四项已完成；下一步优先**重打独立版 zip**，再处理 GitHub 推送。
4. 功能迭代方向（按用户之前的意见）：题库题型/状态整理、自动分类改用「按题裁图 OCR」以减少串题、出题画布更多排版能力。
5. 每改一处：`npm run build`（前端）或重启（后端）→ 按第 8 节脚本验证 → 截图存 `data/samples/`。

---

## 10. 当前数据快照（2026-11-08 02:15）

- 接口 **173** 条（新增 GitHub 同步 6 条；此前含单题导出与讨论 `notes` 三条）：`pytest` **20 passed**
- 题库 **18 题**：9 道画布图片题（`简答 / draft`）+ 9 道**结构化题** `10`~`18`（样卷 6 页整卷导入，52 小问 156 分 15 张题干图 18 张答案图，`layout.kind=text`）
- 结构化题答案图走 `data/library/import/8610e61592a8/a*.png`（300dpi），题干图 `p*.png`（300dpi）
- 试卷 **11** 张，其中人工整卷 4 张（`37` 目前是**草稿**，只在 `/#/exam` 里出现）：`23`（样卷改编·单题 14 分 3 图）、`31`（AI 草稿验收）、`36`（整卷 9 题 52 小问 156 分，题目卷 6 页 / 答案卷 12 页）
- 模板 **3** 个；项目目录 **2.3 GB**（`.venv` + `node_modules` + `dist-app` 占大头）
- AI：`source=db`，模型 `deepseek-v4.1-flash-free`，内置免费模型常驻（`http://127.0.0.1:18903/v1`）
- 文献库：题目卷 `9e3278096684`（6 页）、答案卷 `28b61c7600ef`（11 页）
- 前端构建产物：`index-BoQpEgLo.css` + `index-DcdP_DoW.js`（1,126.20 kB）
- 草稿 **1** 份：`37` 样卷改编·第 1 题（草稿）——1 大题 8 小问 14 分
- 解析产物：`data/library/import/8610e61592a8/`（样卷 6 页：题干图 15 张 + 答案图 18 张，300dpi）
- 公共题库：仓库 `spbbhyg/tiku-public` = `6f591f8` → `e36956c` → **`b9f101f7 publish: 9 questions (1)`**；`manifest.seq=1`、`counts{questions:9, images:25}`、`packs/20261107-151437-1.jsonl` + `images/` 25 张；本机已配置令牌（DPAPI 存 `data/.secrets/github.json`）

## 附：2026-11-08 凌晨补记（文字层导出 / 拆题存文字 / 试题篮占比）

1. **导出已统一**：`POST /api/papers/{id}/export.pdf`（body: `part/title/header/footer/save`）是唯一入口；前端 `components/PaperExport.vue` 是唯一出口（试卷详情页、整卷编辑器都用它）。`Papers.vue` 行内三个按钮、`Questions.vue` 的 docx/pdf/md 导出面板、`PaperView` 的 canvas/通用导出全部删除。单题导出保留：`GET /api/exam/question/{id}/export?part=`。
2. **题包→组卷**：题库页勾选或拖拽进右下试题篮，篮内可改分值/占比，点「组卷」→ `POST /api/wb/papers` → 跳试卷详情页导出。
3. **导出后按样卷排版**（`services/paper_doc.py` + `services/exam_render.py`）：
   - 大题标题 `第N 题 标题（score 分，占 percent%）`，percent 来自试题篮手填（留空则按总分自动算）。
   - 正文按 `layout.stem[]` 的段落流排，段落缩进 2 字符、小问编号顶格；图按 `blocks` 顺序与段落交错。
   - 图注**只在显式写了 caption 时**才输出，且不再自动加「图 N」。
   - 单图按原卷物理尺寸排（`_natural_pt()` = 像素宽 × 72 / 拆题 DPI 150，夹在 90~415pt）；多图一行用等高盒子 `fbox`（`template.figure.rowHeight`，默认 150pt）。
   - 页眉页脚支持 `{page}`/`{pages}`，也支持老师直接写「第 X 页 共 Y 页」——`X`/`Y` 会被替换成真实页码。
4. **拆题现在存文字层**：`POST /api/wb/split-images` 返回每题 `blocks`（段落与图块按纵向交错），`split-import` 写进 `Question.layout = {kind:"text", version:1, meta:{title,percent}, blocks:[...]}`；`Question.images` 保持原样，题库里仍显示图片，只有导出走文字排版。
5. **已知不足**（下一步）：①个别长句跨行仍会被拆成两段（`2-5 …如` / `何对测定…`）②源 PDF 里的表格会被拍平成一串数字（如第 3 题的 C/H/N 元素分析）③同一行多图裁切尺寸不一致时，等高盒子内会出现视觉大小差异。
6. 验证：`pytest` 20 passed；`check_all_final.js` 9 页 `OK`、`功能轨 10`、`ERRORS []`；示例 PDF 存于 `data/samples/wb43-文字层-题目卷.pdf`、`wb43-文字层-答案卷.pdf`。

### 补记 2（2026-11-08 01:20，按用户三条反馈继续修）
- **图注**：`exam_render._figure()` 只有显式 `caption` 才输出，`autoNumber` 只对已有说明补「图 N」；不写就完全不渲染。
- **一行多图等宽**：`_figures()` 取该行最窄的原始宽度作为统一宽度（不再平分 415pt、也不再压成 52pt 缩略图），第 7 题的 4 张、第 8 题的 2 张现在同宽。
- **页脚页码**：`footer.format` 支持 `{page}/{pages}`、`第 X 页 共 Y 页`、`X / Y` 三种写法，导出时替换成真实页码。
- **段落合并**：`split_images.flush()` 与 `paper_doc._blocks()` 都加了「上一段未结句且下一段不是新编号 → 接回去」；同时「第N题 标题（X 分，占Y%）」强制单独成段，避免头行和正文粘在一起。
- **表格**：`rich_blocks()` 用 `page.find_tables()` 抽表并屏蔽表内文字行；但本套样卷的数字被 PDF 拆成「1466.」这类残格，检测到残格时**主动放弃表格**、退回文字，避免导出错表。表格是下一步要单独做的（按 words 坐标重建单元格）。
- 回归：`pytest` 20 passed；`check_all_final.js` 9 页 OK / `ERRORS []`。

### 补记 3（2026-11-08 01:35，长任务可切页面 + 全局提示）
- 新增 `apps/web/src/tasks.js`：模块级 `tasks.running / tasks.toasts`（不随组件卸载消失），提供 `taskBegin/taskEnd/toast`。
- `App.vue` 外壳挂了两块全局 UI：底部居中的任务条 `.gtask`（有长任务时显示标题、呼吸点）、底部居中的提示 `.gtoasts`（点一下可关闭，6 秒自动消失）。
- 整卷导入（`views/Ingest.vue`）：解析、入库都走 `taskBegin/taskEnd`，切到别的页面任务条仍在；解析结果写 `sessionStorage['tiku-ingest-draft']`，切回来提示「继续上次的草稿：N 道大题」，不用重传 PDF；入库成功 → `toast('N 道题已入库')` 且跳到题库，提示跟着走。
- 约定：以后所有超过 1 秒的前端任务都按这个模式接（`taskBegin` 开始、`taskEnd` 结束、成功/失败 `toast`），不要把状态只放在组件里。

### 补记 4（2026-11-08 11:20，桌面外壳：不再套浏览器）
- `desktop/tiku-studio.exe`：.NET Framework 4.x + WebView2 的原生窗口，双击即用，没有地址栏/标签页/收藏栏。启动时自己 `schtasks /Run /TN "tiku-studio"` 拉服务、轮询 `/api/stats` 到 200 才加载页面；服务起不来会弹提示让你去看 `start.bat`。
- 同目录三个依赖必须跟 exe 放一起：`Microsoft.Web.WebView2.Core.dll`、`Microsoft.Web.WebView2.WinForms.dll`、`WebView2Loader.dll`（x64）。依赖系统里的 **WebView2 Runtime**（本机已装 147.0.3912.60；换机器没装的话到微软官网装 Evergreen Runtime）。
- 桌面快捷方式 `题库 tiku-studio.lnk` 指向该 exe，图标用 `apps/web/public/icon.ico`。
- WebView2 里的行为：`window.open`/`target=_blank` 改为在同一窗口打开；下载（导出 PDF）落到系统「下载」文件夹；DevTools 关闭。
- 单题导出改成 blob 下载（`Bank.vue downloadOne`），否则在桌面壳里点「下载本题 PDF」会把整个窗口导航到 PDF。
- 另加了 PWA 清单 `apps/web/public/manifest.webmanifest` + `icon-192/512.png`，`index.html` 引了 manifest 和图标——用浏览器打开时也可以「安装为应用」。
- 源码在 `dsh-work/wv2/App.cs`，重新编译：`csc.exe @dsh-work/wv2/build.rsp`（需先 `cp pkg/lib/net462/*.dll` 到同目录）。旧的 `launch-app.ps1` 保留作后备（找 Edge/Chrome/Playwright Chromium 用 `--app` 打开）。
