# tiku-studio · 本地 Agent 拆解手册

> 给谁看：ZCode / Claude Code / Cursor 等本地编码 Agent，以及想手动跑流程的人。
> 你的任务：把用户指定的课本 / 教辅 / 试卷 PDF，拆解成符合本仓库 schema 的 JSON bundle，投递到 `data/inbox/`。

## 铁律（先读这个）

1. **版权**：只处理用户本地的文件。拆解产物 JSON 是结构化数据，投递到 `data/inbox/`（gitignored）。
   **绝不**把 PDF、扫描图、整页图片提交进 git。
2. **产物必须过校验**：写完 bundle 必须执行
   `.venv/Scripts/python -c "from app.services.bundle_import import validate_bundle; ..."`
   或直接 `python scripts/import_bundle.py <bundle>`（会校验并入库），失败就修到通过。
3. **不碰 data/ 与 .venv/** 之外的运行时数据。
4. **先查后拆**：拆之前先看数据库里是否已有同名书（`GET /api/books` 或直接查 data/tiku.db），避免重复建册。

## 两种拆解任务

### A. 目录级拆解（课本 / 教辅的目录索引）

产物：**catalog-bundle**（book + units）。课本（role=textbook）是全系统的『纲』，
它的 `knowledge_points` 决定后面的题算不算超纲——宁可细，不要空着。
知识点命名保持人类可读的中文短语，如 `分与合`、`两拼音节拼读`。

做法：
1. 用 PyMuPDF（`fitz`）打开 PDF，把目录页/每页转成 PNG（`page.get_pixmap(zoom=2)`）。
2. 逐页读图（你有视觉能力），识别：单元序号与名称、课时名、页码。
3. 为每个单元整理 knowledge_points（依据单元名 + 页面内容，不要编造超前的考点名）。
4. 产出 bundle，参考 `packages/schema/catalog-bundle.schema.json` 与 `seed/catalog-*.json` 的写法。

### B. 题目级拆解（试卷 / 教辅的每道题）

产物：**question-bundle**（book + questions）。要求：
1. 每道题独立拆出：stem（图形用 ●▲★■○ 等符号写在题干里）、options、answer、analysis、
   q_type、difficulty(1/2/3)、knowledge_points。
2. **知识点命名必须与挂靠课本纲里的名字完全一致**（先读纲：
   `select name, knowledge_points from units where book_id = <课本id>`）。
   纲里没有的考点名 → 先判断：属于本年级内容就补进课本纲的 bundle（先拆目录级），
   是有意超前的提优题就给 `scope_status: "extension"` + `scope_reason`。
3. 看图题：能用符号表达的写进 stem；表达不了的就地裁剪题图区域存 PNG
   （放 `data/inbox/images/<册名>/`，bundle 里写相对路径），导入时会自动收进资料库。
4. bundle 里给 `source_textbook_title`（挂靠的课本标题）和每题 `unit_hint`（{seq: N}）。

## 标准工作流

```
1. 用户说：「拆解 xxx.pdf（一年级数学教辅）」
2. 确认元信息：role(课本/教辅/试卷集) subject grade semester publisher edition_year title
   ——不确定就问用户或从封面页识别
3. PDF → 页图 → 逐页识别 → 生成 bundle JSON 到 data/inbox/<名字>.json
4. 自我校验：jsonschema 校验 + 抽查 3 道题的答案正确性
5. 投递完成，告诉用户：「已投递到 data/inbox/，打开网页在资料库页点导入，或运行
   python scripts/import_bundle.py data/inbox/<名字>.json」
6. 导入后所有题目处于待审核状态，提示用户在网页「待审核」页确认
```

## 校验命令（在仓库根目录）

```bash
# 校验 + 入库一步到位（推荐）
python scripts/import_bundle.py data/inbox/your-bundle.json

# 只校验不入库
.venv/Scripts/python -c "import json,sys; sys.path.insert(0,'apps/api'); \
from app.services.bundle_import import validate_bundle; \
print(validate_bundle(json.load(open('data/inbox/your-bundle.json',encoding='utf-8'))))"

# 跑测试确认没改坏东西
.venv/Scripts/python -m pytest apps/api/tests -q
```

## 常见坑

- **Windows 编码**：所有文件读写显式 `encoding="utf-8"`；运行 pytest 从仓库根目录。
- **同名知识点**：`比大小（＞＜＝）` ≠ `比大小`。复制纲里的原字符串，不要手打。
- **口算题去重**：同题干不同考点是两道题（哈希含知识点）；完全相同才是重复。
- **一图多题**：大题里的每个小题拆成独立 question，用 `source_page` 标注如 "三、1"。
- **unit_hint 优先**：教辅目录和课本目录不完全对齐时，以你能确认的对应关系给 unit_hint。
