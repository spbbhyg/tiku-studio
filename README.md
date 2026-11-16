# 神奋 · 化学竞赛出题工作台

> 题库 · 对纲 · 组卷
> 面向化学竞赛教练的本地出题软件：从文献里裁题、在 A4 画布上排版、存进本地题库、按题库选题组卷、导出可直接印的题目卷与答案卷。
> 全程只在本机跑，不联网也能用；题库、题图、草稿都在自己的 `data\` 里。

本仓库是 [cy-0754/tiku-studio](https://github.com/cy-0754/tiku-studio) 的二次开发版（第二版），
这一版的重心是把出题这件事从头到尾做顺：**画布 → 单题 → 整卷 → 题库 → 组卷**，并把它做成一个能直接发给别人的独立软件。

---

## 它解决什么问题

竞赛题大多藏在文献 PDF 里：一段机理、一张 Scheme、几行小问，还要按 A4 排得好看，
最后还要能追到「这道题是谁出的、属于哪个专题」。手工 Word 排一遍要半小时，改一次再来一遍。

神奋把这条链路固定成四步：

1. **导入文献** — PDF 直接拖进画布或右侧文献栏，页面在上面，拖框就裁进题目画布。
2. **裁图排版** — 画布是真实 A4 版面（595×842pt），图片、文字块、结构式都是可拖可缩的元素，能组合、能整组拖。
3. **写答案** — 答案画布上一个按钮就能把「我的题目」整块垫上去，在题目下面接着写解析。
4. **收入题库** — 一次入库同时做三件事：写进本地题库、按题库的分类落专题、把出题人记进题目标签。

---

## 界面一览

左侧导航十项：

| 页面 | 干什么 |
| --- | --- |
| 首页 | 概览、最近用的东西、环境自检 |
| 出题 | 画布 / 单题 / 整卷 三种模式，见下 |
| 草稿 | 出到一半关掉页面也不丢（自动存 `data/drafts`），可续编、删除 |
| 题库 | 直接嵌本地题库（chemy）本体的界面，检索、看题、校对 |
| 组卷 | 从题库勾题 → 中间 A4 实时预览 → 右栏版式与页脚 → 导出 PDF / Word |
| 出整卷 | 新建整卷、录大题小问、导出题目卷 + 答案卷（先草稿、后成卷） |
| 格式转换 | docx ↔ pdf 双向批量转换 |
| AI 工作台 | 智能出题 / 精读文献 / 对话 / 模型与用量，四个子页签 |
| 试卷 | 成卷列表、预览、导出记录 |
| 设置 | 数据目录、AI 模型、快捷键、分类、导出默认值、局域网、软件更新、上手指引 |

**出题页三种模式**共用同一份题目数据，切换不丢内容：

- **画布**：主界面。拖入图片/PDF、裁图、公式面板、Ketcher 结构式、上色（先选字再点色块）、组合/拆分、撤销重做（60 步）、自动对齐。
- **单题**：纯文本 + 行内图的结构化编辑，右边实时预览 A4。预览里的图片可以直接拖位置、滚轮调宽窄、双击复位——文字仍自动排版，只有图和结构式可动。
- **整卷**：内嵌「出整卷」，从这里直接进整卷编辑。

---

## 和本地题库的关系

这一版把原来的内置题库整个换成了本机上的独立题库 [chemy]（默认 `http://127.0.0.1:8080`，随安装包一起发）。

**只读 + 粘贴导入，绝不改题库的任何文件**：

- 读：专题、题库树、检索、题目详情、草稿列表都由 `apps/api/app/routers/chemy.py` 转发，前端拿到的形状不变。
- 写：只有一条路——把题目拼成题库的粘贴导入格式（`===题目===` / `===题干===` / `===小问===` / `===答案===`）POST 给 `/api/admin/import/paste`，再按批次发布。**入库必须填出题人**，写完记进题目标签（`出题人：张三`），题库里能追到人。
- 分类严格跟题库走：专题（九大专题）、机构、年份都从题库的 `/api/topics`、`/api/taxonomy` 现取，不自己维护一套。
- 题库没起来时，「题库」页会给「启动题库 / 重试 / 仍然打开」三个选项，也能从设置里配基址（`TIKU_CHEMY_BASE`）。

> 题库进程由安装包自带的 `chemy\` 目录提供；神奋启动时会顺手把题库一起拉起来。

---

## 安装与更新

### 给使用者：只需要一个文件

`神奋-一键安装.cmd`（约 700MB，自带题库，单文件自解压）：

1. 双击，看到中文指引，问「装到哪个目录」（回车用默认 `%LOCALAPPDATA%\神奋`，也可以填 `D:\神奋`）。
2. 它会自己切出压缩包、解压、在桌面建「神奋」快捷方式。
3. 桌面双击「神奋」→ 浏览器自动打开界面，题库同时起来。

卸载就是删掉那个目录（不写注册表、不动系统）。数据都在 `data\` 里，想留就先拷出来。

### 覆盖升级

退出神奋（关掉托盘/命令行窗口），再跑一次新的安装导引、目录填同一个，就会覆盖程序文件、保留 `data\`。

### 软件内更新

设置 → 软件更新 → 检查更新：从 GitHub 本仓库的 release 拿最新版，
下载 → 校验 → 解包 → 写 `data/update/pending.json`；
点「应用并重启」后由一个独立 PowerShell 脚本等主进程退出、robocopy 覆盖、再拉起，脚本自己删自己。

- 检查更新走 GitHub 公开 API，**不需要 token**（匿名每小时 60 次，够用）。
- 发布新版本才需要一个 fine-grained PAT（只勾本仓库 `Contents: Read and write`），用完即撤。

### 局域网共创

设置 → 局域网：列出本机所有可发地址（`http://192.168.x.x:8100/`），点一下复制发给同办公室的老师，
他们浏览器打开就是同一份数据。可以设一个访问码：非本机访问必须带码（`?c=` 或 cookie），
本机不受影响，码只认字母数字（免得同事输不进去）。

---

## 其它值得说的实现

- **结构式不再丢箭头**：Ketcher 的画板是一个 SVG，直接按 DOM 抽整块画布（含反应箭头、加号、文字标注）比走 molfile 可靠——
  事实上 Ketcher 自己就拒绝把带反应箭头的结构存成 MOL（`The structure cannot be saved as *.MOL due to reaction arrows.`）。
  抽取逻辑在 `apps/web/src/ketcherSvg.js`，抽出来交给后端用 PyMuPDF 栅格化。
- **组卷排版不再压平**：题库题干是「一个 `<p>` 里用 `<br>` 表示原 PDF 换行 + `<sub>/<sup>` 上下标」，
  旧的纯文本化会把它变成「一句断三行、上下标消失」。现在走 `apps/api/app/services/rich_text.py`：
  解析成块 + 行内 run，HTML 预览保留上下标/表格，Word 导出逐 run 设置（含 `w:eastAsia` 中文字体），
  段内软换行会自动接回一句（中文不加空格，西文补空格，行尾连字符去掉）。
- **草稿**：编辑动作后 3 秒 debounce 落盘，`data/drafts/*.json`；打开出题页会提示「发现未完成的草稿，要继续吗」。
- **格式转换**（`apps/api/app/routers/convert.py`）：
  - docx → pdf：**优先调用本机 WPS/Office 的 COM**（版式和原文件一致），没有则 LibreOffice，再没有才用内置 Chromium 渲染（版式近似）。
  - pdf → docx：自己按坐标重建——双栏自动拆开、断行接回段落、矢量图按「文字之间的空白带」整块裁成图片插进去。
    PDF→Word 本质是重排，单栏化后页数会变多；要 1:1 版式请直接看 PDF。

---

## 从源码跑

```bash
# 后端
python -m venv .venv
.venv/Scripts/pip install -r apps/api/requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --app-dir apps/api --host 0.0.0.0 --port 8100

# 前端（开发时另开一个终端）
cd apps/web && npm install && npm run dev      # 或 npm run build 后由后端直接托管
```

- 后端默认 8100，接口文档 `http://127.0.0.1:8100/docs`。
- 题库在 `http://127.0.0.1:8080`，没起来时「题库」页会提示并由「启动题库」按钮拉起。
- 数据目录默认 `data/`，可用环境变量 `TIKU_DATA_DIR` 改。

### 测试

```bash
.venv/Scripts/python -m pytest apps/api/tests -q     # 18 passed
cd apps/web && npm run build                          # vite 构建（约 10–15s）
```

### 打包

```bash
python pack.py --no-data --no-zip            # 前端构建 + PyInstaller → dist-app/tiku-studio
python tools/pack_bundle.py                  # 拼上题库 → dist-app/神奋-完整版 + zip
python tools/make_installer.py --src dist-app/神奋-完整版 --out ..\神奋-一键安装.cmd
python tools/verify_installer.py ..\神奋-一键安装.cmd   # 校验载荷与抽查文件 hash
```

安装导引是「批处理正文 + `__SHENFEN_PAYLOAD_V1__` 标记 + zip」的单文件：
正文纯 ASCII（cmd 按字节读批处理，中文会串行）；PowerShell 逻辑经 base64 分块写临时文件再解码
（cmd 单行 8191 上限、`chcp` 会吞掉重定向进来的 stdin、`>>>` 会被当成重定向）。

---

## 目录

```
apps/api/app/            FastAPI 后端
  routers/               chemy(题库接入) drafts compose convert update lan exam papers …
  services/              rich_text 富文本、exam_render 试卷渲染、authoring_export docx …
apps/web/                Vue 3 + Vite 前端
  src/components/        CanvasPane(画布) QuestionSheet(A4) ComposePane(组卷) TextMode(单题) …
  src/views/             各页面
  public/katex/          公式渲染静态资源
ketcher-embed/           Ketcher 结构式编辑器桥接
tools/                  打包与安装导引
data/                   运行时数据（不入库）：tiku.db、library/、drafts/、导出
```

---

## 许可

见 [LICENSE](LICENSE)。题库（chemy）与本仓库分开发布，题库的数据始终留在使用者自己的机器上。
