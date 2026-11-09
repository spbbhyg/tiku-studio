# 神奋 · 题库与组卷

面向化学竞赛 / 高校教学的本地工作台：**拆卷入库 → 出题画布 → 试题篮组卷 → A4 成卷导出 → 公共题库同步**。
前后端分离（FastAPI + Vue 3）、自带 SQLite、纯本机运行、无云依赖，MIT 许可。

> 原始项目：[cy-0754/tiku-studio](https://github.com/cy-0754/tiku-studio)；本仓库是在其之上的二次开发（文献命题工作台、出题画布、导出重做、桌面版等）。

---

## 功能

| 模块 | 路由 | 说明 |
| --- | --- | --- |
| 首页 | `/#/` | 数据概览、最近试卷、AI 助教「小卷」（可联网检索） |
| 题库 | `/#/questions` | 专题筛选（Chemy 九大专题）、关键词搜索、题干与题图预览；右侧 **试题篮**（拖拽 / 「＋」加入、改分值、填占比）→ 一键组卷 |
| 出题 | `/#/author` | PDF 框选裁剪、A4 画布拖拽排版、公式与结构式、图片染色与橡皮擦、OCR 转文字、撤销/重做、多选拼接 |
| 整卷导入 | `/#/ingest` | 题目卷 + 答案卷 PDF → 自动切题、配对答案、写文字层与图块、入库前人工校对；长任务可切页面不中断 |
| 试卷 | `/#/papers` | 题目卷 / 答案卷 / 合卷预览，试卷融合，统一导出 |
| 精读 | `/#/reader` | PDF 文献阅读、关键句标注、笔记 |
| 智能出题 | `/#/smart` | AI 命题、文献改编 |
| 设置 | `/#/settings` | 数据目录、AI 模型与密钥、分类标签、**公共题库（GitHub 同步）** |

**导出**：统一入口 `POST /api/papers/{id}/export.pdf`，一次出「题目卷」「答案卷」两份 PDF；可自定义标题、页眉、页脚（支持 `{page}/{pages}` 或中文「第 X 页 共 Y 页」）；A4 双栏内边距按样卷 90/80/72.5/82pt；图片按原卷尺寸、同行等高、按块分页（不会把图切两半）；文字可搜索、可复制。单题导出保留。

**公共题库**：把题目发布到一个公开 GitHub 仓库（默认 `spbbhyg/tiku-public`），别人在设置页点「一键更新题库」即可增量拉取；发布只允许在运行服务的那台电脑上、且仓库属于你本人。

---

## 快速开始

### 方式一：直接用打包版（Windows）

1. 解压 `神奋-题库与组卷-完整版.zip`（或空白题库版 `神奋-空白题库版.zip`）。
2. 双击 `启动-神奋.cmd`（推荐）或 `神奋.exe`。
   - `神奋.exe` 是 WebView2 桌面壳；`tiku-studio.exe` 是本地服务，壳会自动拉起并轮询。
   - 需要系统已安装 **WebView2 Runtime**（Win10/11 一般自带）。
3. 浏览器/桌面窗口打开 `http://127.0.0.1:8100`。

端口被占用时：`set TIKU_PORT=8200` 后再启动。数据在包内 `data/`，换机器整体复制即可。

### 方式二：源码运行

```bash
# 后端（Python 3.13）
python -m venv .venv && ./.venv/Scripts/pip install -r apps/api/requirements.txt
./.venv/Scripts/python -m uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8100

# 前端（Node 20+）
cd apps/web && npm install && npm run build     # 产物由后端直接托管
```

开发前端时可 `npm run dev`（Vite 代理到 8100）。

---

## 目录结构

```
apps/api/app/            FastAPI 应用
  routers/               exam / papers / authoring / workbench / wb_split / github_sync …
  services/              exam_render(PDF 渲染) · paper_doc(数据归一) · split_images(拆题)
                         github_sync(公共题库) · compose_render · pdf_render
apps/web/src/            Vue 3 前端（views / components）
apps/web/public/         图标、manifest、Ketcher 静态资源
tools/                   构建与维护脚本
pack.py                  一键打完整版 zip（前端构建 → PyInstaller → 复制运行时 → 7z）
build_app.py             PyInstaller 打包
launcher.py              打包版启动器（拉起 uvicorn、按需打开浏览器）
 desktop/                 WebView2 桌面壳（源码 + 编译产物，不入库）
data/                    运行时数据（题库、图库、导出物，不入库）
```

---

## 开发与验证

```bash
cd apps/api && ../../.venv/Scripts/python -m pytest tests -q     # 20 passed
cd apps/web && npm run build
node ../dsh-work/pw/check_all_final.js                           # 9 个页面无控制台报错（需本地服务）
```

接口文档：服务启动后访问 `/openapi.json`（约 173 条接口）。

---

## 数据与隐私

- 所有数据都在本地：`data/tiku.db`（SQLite）+ `data/library/`（题图）。
- `data/`、打包产物、桌面壳二进制**不入库**。
- 公共题库同步需要你在设置页粘贴一个 GitHub Personal Access Token（只存本机、DPAPI 加密），且只用于访问你自己指定的仓库。

## 许可

MIT，见 [LICENSE](LICENSE)。原始版权归 cy-0754；本仓库的修改与新增功能版权归 spbbhyg。
