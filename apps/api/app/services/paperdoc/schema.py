"""paperdoc/v2 契约：JSON Schema + 语义校验。

设计前提（见《重改导出-提示词》§1）：版式不是渲染器猜出来的，而是数据里写死的。
本模块只做三件事：

1. 定义 paperdoc/v2 的 JSON Schema（结构校验）；
2. 做 Schema 表达不了的语义校验（编号不得写进正文、表格不得带 PDF 残格、
   ansblock 必须指向存在的小问……）；
3. 把版式常量集中在一处（版心宽、字号、行距），供渲染器与导入器共用同一套数字。

文本内联标记（para / sub / heading / table 单元格都适用）：
    <sub>…</sub>  <sup>…</sup>  <b>…</b>  <i>…</i>  <br>  <score>…</score>
<score> 只用于答案里的红字分值标注，例如
    M 的元素名称：金<score>（1 分）</score>
渲染器只认这六个标记，不做 HTML 直通，其它标签在校验期就拒绝。

块类型只有 7 种（§1.1）：
    heading  小节标题（不属于某道题的标题，如「第一部分」）
    para     段落，style ∈ body/score/answer/note
    figure   图片，必须带 w（pt），禁止渲染器再猜
    table    结构化表格 head + rows，style 可选：给一个值则全表同色，
             给数组则按列上色（长度须等于 head 长度），表头始终加粗黑字
    sub      小问编号行（1-1、1-2…），编号由渲染器生成
    pagebreak 只在需要时显式分页
    ansblock 答案块容器，只出现在 item.answer.blocks 里，for 指向小问编号

一道题的 blocks 顺序就是版式顺序，渲染器不许重排（§1.1 第 5 条）。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

try:  # jsonschema 在 requirements.txt 里，但允许降级为纯语义校验
    import jsonschema

    _HAS_JSONSCHEMA = True
except Exception:  # pragma: no cover - 环境缺包时的兜底
    jsonschema = None  # type: ignore[assignment]
    _HAS_JSONSCHEMA = False


# ---------------------------------------------------------------- 版式常量
# 全部来自样张实测（§2）。渲染器的 CSS 变量、导入器的 bbox 换算都从这里取值，
# 任何一处要改数字，只改这里。

SCHEMA_ID = "paperdoc/v2"

PAGE_W = 595.3           # A4 宽 pt
PAGE_H = 841.9           # A4 高 pt
MARGIN_X0 = 90.0         # 版心左边界
MARGIN_X1 = 505.6        # 版心右边界
CONTENT_W = round(MARGIN_X1 - MARGIN_X0, 1)   # 415.6
FIRST_LINE_Y = 89.8      # 首行起点

BODY_SIZE = 10.56        # 正文
LINE_HEIGHT = 15.6       # 正文行距 = 1.48 × 字号
TABLE_SIZE = 9.96        # 表格内文字
CAPTION_SIZE = 6.96      # 图注 / 脚注 / 角标
FOOTER_SIZE = 9.0        # 页脚

FIRST_INDENT = 21.0      # 段首缩进 2 字符
ITEM_GAP = 18.0          # 大题之间 margin-top

FALLBACK_FIGURE_W = round(CONTENT_W * 0.55, 1)  # 228.6：没有原始几何时的兜底宽度
FIGURE_MAX_W = round(CONTENT_W + 0.6, 1)         # 416.2：允许 0.6pt 舍入

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".svg")

ALLOWED_TAGS = frozenset({"sub", "sup", "b", "i", "br", "score"})
VOID_TAGS = frozenset({"br"})

FOOTER_PLACEHOLDERS = frozenset({"page", "pages", "title", "subject"})


# ---------------------------------------------------------------- 校验用正则
_TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)\s*(/?)\s*>")
_TAGLIKE_RE = re.compile(r"<[^>]*$")

# 正文里不该出现的编号：第 3 题 / 1-1
_LEAD_NUMBER_RE = re.compile(r"^\s*(?:第\s*\d+\s*题\s|\d+\s*[-－—]\s*\d+)")

# PDF 抽取残格：「1466.」「.5」这类被拆断的数字碎片
_RESIDUAL_RE = re.compile(r"^\s*(?:\d+\s*[.．]\s*|[.,;:、]\s*\d*\s*)$")

_SUB_NO_RE = re.compile(r"^\d+-\d+(?:-\d+)*$")

# 兜底：页脚里手写的 X / Y 占位
_LITERAL_XY_RE = re.compile(r"(?<![A-Za-z])[XY](?![A-Za-z])")


# ---------------------------------------------------------------- JSON Schema
_TEXT = {"type": "string", "minLength": 1}

_SOURCE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "file": {"type": "string"},
        "page": {"type": "integer", "minimum": 1},
        "y0": {"type": "number"},
        "y1": {"type": "number"},
    },
}

_HEADING = {
    "type": "object",
    "required": ["t", "text"],
    "additionalProperties": False,
    "properties": {
        "t": {"const": "heading"},
        "text": _TEXT,
        "level": {"enum": [1, 2]},
    },
}

_STYLE_ENUM = ["body", "score", "answer", "note"]

_PARA = {
    "type": "object",
    "required": ["t", "text"],
    "additionalProperties": False,
    "properties": {
        "t": {"const": "para"},
        "text": _TEXT,
        "style": {"enum": _STYLE_ENUM},
        "indent": {"type": "boolean"},
    },
}

_FIGURE = {
    "type": "object",
    "required": ["t", "path", "w"],
    "additionalProperties": False,
    "properties": {
        "t": {"const": "figure"},
        "path": {"type": "string", "minLength": 1},
        "w": {"type": "number", "exclusiveMinimum": 0, "maximum": FIGURE_MAX_W},
        "h": {"type": "number", "exclusiveMinimum": 0},
        "align": {"enum": ["left", "center", "right"]},
        "caption": {"type": "string"},
        "source": _SOURCE,
    },
}

_TABLE = {
    "type": "object",
    "required": ["t", "head", "rows"],
    "additionalProperties": False,
    "properties": {
        "t": {"const": "table"},
        "head": {"type": "array", "minItems": 1, "items": _TEXT},
        "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
        "caption": {"type": "string"},
        # 单个值 = 全表数据行同色；数组 = 按列上色（长度必须等于 head 长度）。
        # 表头不参与着色，始终加粗黑字。
        # 用 if/then/else 而不是 oneOf：数组里某个值写错时，报错能落到 items[0].style[1]。
        "style": {
            "allOf": [
                {
                    "if": {"type": "array"},
                    "then": {"minItems": 1, "items": {"enum": _STYLE_ENUM}},
                    "else": {"enum": _STYLE_ENUM},
                }
            ]
        },
    },
}

_SUB = {
    "type": "object",
    "required": ["t", "no", "text"],
    "additionalProperties": False,
    "properties": {
        "t": {"const": "sub"},
        "no": {"type": "string", "pattern": r"^\d+-\d+(?:-\d+)*$"},
        # 允许空串：小问只有编号没有文字时（如「4-1」单独一行，图在下一块），
        # 渲染器只印编号不印空格。
        "text": {**_TEXT, "minLength": 0},
    },
}

_PAGEBREAK = {
    "type": "object",
    "required": ["t"],
    "additionalProperties": False,
    "properties": {"t": {"const": "pagebreak"}},
}

_ANSBLOCK = {
    "type": "object",
    "required": ["t", "blocks"],
    "additionalProperties": False,
    "properties": {
        "t": {"const": "ansblock"},
        "for": {"type": "string", "pattern": r"^\d+-\d+(?:-\d+)*$"},
        "label": {"type": "string"},
        "blocks": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/inner"}},
    },
}

def _block_union(kinds: tuple[str, ...]) -> dict:
    """按 block["t"] 分派到对应分支。

    不用 oneOf：oneOf 的报错是「not valid under any of the given schemas」，
    定位不到具体字段。if/then 能让错误直接落在 w / rows[1][2] / for 上。
    """
    return {
        "type": "object",
        "required": ["t"],
        "properties": {"t": {"enum": list(kinds)}},
        "allOf": [
            {
                "if": {
                    "properties": {"t": {"const": kind}},
                    "required": ["t"],
                },
                "then": {"$ref": f"#/$defs/{kind}"},
            }
            for kind in kinds
        ],
    }


BLOCK_TYPES = ("heading", "para", "figure", "table", "sub", "pagebreak", "ansblock")
INNER_TYPES = tuple(k for k in BLOCK_TYPES if k != "ansblock")

_BLOCK = _block_union(BLOCK_TYPES)
_INNER = _block_union(INNER_TYPES)

_META = {
    "type": "object",
    "required": ["title"],
    "additionalProperties": False,
    "properties": {
        "title": _TEXT,
        "subject": {"type": "string"},
        "grade": {"type": "string"},
        "total_score": {"type": "number", "minimum": 0},
        "duration": {"type": "number", "minimum": 0},
        "header": {"type": "string"},
        "footer": {"type": "string"},
        "source": {"type": "object"},
    },
}

_ITEM = {
    "type": "object",
    "required": ["number", "title", "score", "blocks", "answer"],
    "additionalProperties": False,
    "properties": {
        "number": {"type": "integer", "minimum": 1},
        "title": {"type": "string"},
        "score": {"type": "number", "minimum": 0},
        "percent": {"type": "number", "minimum": 0, "maximum": 100},
        "blocks": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/block"}},
        "answer": {
            "type": "object",
            "required": ["blocks"],
            "additionalProperties": False,
            "properties": {
                "blocks": {"type": "array", "items": {"$ref": "#/$defs/block"}},
            },
        },
    },
}

JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": SCHEMA_ID,
    "type": "object",
    "required": ["schema", "meta", "items"],
    "additionalProperties": False,
    "properties": {
        "schema": {"const": SCHEMA_ID},
        "meta": {"$ref": "#/$defs/meta"},
        "front": {"type": "array", "items": {"$ref": "#/$defs/inner"}},
        "items": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/item"}},
    },
    "$defs": {
        "meta": _META,
        "item": _ITEM,
        "block": _BLOCK,
        "inner": _INNER,
        "heading": _HEADING,
        "para": _PARA,
        "figure": _FIGURE,
        "table": _TABLE,
        "sub": _SUB,
        "pagebreak": _PAGEBREAK,
        "ansblock": _ANSBLOCK,
    },
}


class PaperdocError(ValueError):
    """契约校验失败。errors 里是逐条错误，渲染器不该吞掉它。"""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


# ---------------------------------------------------------------- 文本校验
def _check_text(text: str, path: str, errors: list[str]) -> None:
    """校验内联标记是否闭合、是否只用了允许的标签；顺便查编号有没有写进正文。"""
    stack: list[str] = []
    pos = 0
    for m in _TAG_RE.finditer(text):
        if "<" in text[pos : m.start()]:
            errors.append(f"{path}: 文本里有裸 '<'，请写成 &lt;")
            return
        closing, name, selfclose = m.group(1), m.group(2).lower(), m.group(3)
        if name not in ALLOWED_TAGS:
            errors.append(
                f"{path}: 不允许的内联标签 <{name}>，只允许 {', '.join(sorted(ALLOWED_TAGS))}"
            )
        elif selfclose or name in VOID_TAGS:
            pass
        elif closing:
            if not stack or stack[-1] != name:
                errors.append(f"{path}: 标签 <{name}> 没有正确闭合")
                return
            stack.pop()
        else:
            stack.append(name)
        pos = m.end()
    if "<" in text[pos:]:
        errors.append(f"{path}: 文本里有裸 '<'，请写成 &lt;")
        return
    if stack:
        errors.append(f"{path}: 标签 <{stack[-1]}> 未闭合")

    plain = _TAG_RE.sub("", text)
    if _LEAD_NUMBER_RE.match(plain):
        errors.append(
            f"{path}: 正文以编号开头（{plain[:12]!r}）—— 「第 N 题」「1-1」必须由渲染器生成，"
            "只把题号写进 item.number / sub.no"
        )


def _is_residual(cell: str) -> bool:
    return bool(_RESIDUAL_RE.match(cell))


# ---------------------------------------------------------------- 各类块
def _check_figure(block: dict, path: str, errors: list[str]) -> None:
    rel = block["path"]
    if Path(rel).is_absolute() or rel.startswith(("/", "\\")) or ":" in rel[:3]:
        errors.append(f"{path}.path: 必须是相对 data/library 的 posix 路径，不能是绝对路径：{rel}")
    if ".." in Path(rel).parts:
        errors.append(f"{path}.path: 不允许 '..'：{rel}")
    if "\\" in rel:
        errors.append(f"{path}.path: 用 '/' 而不是反斜杠：{rel}")
    if not rel.lower().endswith(IMAGE_EXTS):
        errors.append(f"{path}.path: 扩展名必须是 {IMAGE_EXTS}：{rel}")
    if block["w"] > CONTENT_W + 0.6:
        errors.append(
            f"{path}.w: {block['w']}pt 超过版心宽 {CONTENT_W}pt —— 整页图请用 w={CONTENT_W}"
        )
    caption = block.get("caption") or ""
    if caption:
        _check_text(caption, f"{path}.caption", errors)


def _table_styles(block: dict) -> list[str]:
    """把 table.style 归一化成「每列一个样式名」。

    None → 全 body；字符串 → 全表同色；数组 → 逐列。
    """
    head = block["head"]
    style = block.get("style")
    if style is None:
        return ["body"] * len(head)
    if isinstance(style, str):
        return [style] * len(head)
    return list(style)


def _check_table(block: dict, path: str, errors: list[str], warnings: list[str],
                 in_answer: bool = False) -> None:
    head = block["head"]
    rows = block["rows"]
    if not rows:
        errors.append(f"{path}.rows: 空表没有意义，要么给数据，要么整块转成 figure")
    style = block.get("style")
    if isinstance(style, list) and len(style) != len(head):
        errors.append(
            f"{path}.style: 按列上色给了 {len(style)} 个值，表头是 {len(head)} 列"
        )
    if in_answer and all(s in ("body", "note") for s in _table_styles(block)):
        warnings.append(f"{path}: 答案表格没有着色（style 全是 body），会印成黑字")
    for i, cell in enumerate(head):
        _check_text(cell, f"{path}.head[{i}]", errors)
        if _is_residual(cell):
            errors.append(
                f"{path}.head[{i}]: 表头 '{cell}' 是 PDF 抽取残格 —— 该表必须整块转成 figure"
            )
    for r, row in enumerate(rows):
        if len(row) != len(head):
            errors.append(
                f"{path}.rows[{r}]: {len(row)} 列，表头是 {len(head)} 列"
            )
        for c, cell in enumerate(row):
            _check_text(cell, f"{path}.rows[{r}][{c}]", errors)
            if _is_residual(cell):
                errors.append(
                    f"{path}.rows[{r}][{c}]: 单元格 '{cell}' 是 PDF 抽取残格（如「1466.」）"
                    " —— 该表必须整块转成 figure（300dpi 裁图）"
                )


def _check_block(block: dict, path: str, errors: list[str], warnings: list[str],
                 allow_ansblock: bool, in_answer: bool = False) -> None:
    kind = block["t"]
    if kind == "figure":
        _check_figure(block, path, errors)
    elif kind == "table":
        _check_table(block, path, errors, warnings, in_answer)
    elif kind == "para":
        _check_text(block["text"], f"{path}.text", errors)
        style = block.get("style") or "body"
        if in_answer and style != "answer" and style != "note":
            warnings.append(f"{path}: 答案正文没有用 style=answer，会印成黑字")
        if style == "score":
            warnings.append(f"{path}: style=score 是蓝字分值说明，答案正文请用 answer")
    elif kind in ("heading", "sub"):
        _check_text(block["text"], f"{path}.text", errors)
    elif kind == "ansblock":
        if not allow_ansblock:
            errors.append(f"{path}: ansblock 只能出现在 item.answer.blocks 里")
            return
        for i, sub in enumerate(block["blocks"]):
            _check_block(sub, f"{path}.blocks[{i}]", errors, warnings,
                         allow_ansblock=False, in_answer=True)


# ---------------------------------------------------------------- 语义校验
def _check_footer(meta: dict, errors: list[str]) -> None:
    footer = meta.get("footer") or ""
    if not footer:
        return
    used = set(re.findall(r"\{([a-z_]+)\}", footer))
    bad = used - FOOTER_PLACEHOLDERS
    if bad:
        errors.append(
            f"meta.footer: 未知占位符 {sorted(bad)}，只支持 {sorted(FOOTER_PLACEHOLDERS)}"
        )
    if "page" not in used:
        errors.append("meta.footer: 必须包含 {page}，页脚必须印真页码")
    if "pages" not in used:
        errors.append("meta.footer: 必须包含 {pages}，否则「共 Y 页」是假数字")
    if _LITERAL_XY_RE.search(footer):
        errors.append(f"meta.footer: 出现了手写占位 X/Y：{footer!r}")


def _jpath(parts: Iterable[Any]) -> str:
    """把 jsonschema 的绝对路径写成 items[0].blocks[3].rows[1] 这种形式。"""
    out = ""
    for p in parts:
        if isinstance(p, int):
            out += f"[{p}]"
        else:
            out += ("." if out else "") + str(p)
    return out or "<root>"


def validate_doc(doc: Any) -> tuple[list[str], list[str]]:
    """返回 (errors, warnings)。errors 非空就是不合格的 paperdoc/v2。"""
    errors: list[str] = []
    warnings: list[str] = []

    if not _HAS_JSONSCHEMA:
        warnings.append("jsonschema 未安装，本次只做了语义校验")

    if isinstance(doc, dict):
        if _HAS_JSONSCHEMA:
            validator = jsonschema.Draft202012Validator(JSON_SCHEMA)
            seen: set[str] = set()
            for err in sorted(validator.iter_errors(doc), key=lambda e: list(map(str, e.absolute_path))):
                msg = f"{_jpath(err.absolute_path)}: {err.message}"
                if msg in seen:
                    continue
                seen.add(msg)
                errors.append(msg)
                if len(errors) >= 40:
                    errors.append("……结构错误过多，已截断；先修最早的一批")
                    break
        else:
            if doc.get("schema") != SCHEMA_ID:
                errors.append(f"<root>: schema 必须是 {SCHEMA_ID!r}")
            if "meta" not in doc or "items" not in doc:
                errors.append("<root>: 缺 meta 或 items")
    else:
        return ["<root>: 根节点必须是对象"], warnings

    if errors:  # 结构已经错到不能走下去
        return errors, warnings

    meta = doc["meta"]
    _check_footer(meta, errors)
    if not (meta.get("title") or "").strip():
        errors.append("meta.title: 不能为空，卷头就是它")

    for i, blk in enumerate(doc.get("front") or []):
        if blk["t"] in ("sub", "ansblock"):
            errors.append(f"front[{i}]: 试卷级前言里不能出现 {blk['t']}")
        _check_block(blk, f"front[{i}]", errors, warnings, allow_ansblock=False)

    seen_numbers: list[int] = []
    score_sum = 0.0
    for i, item in enumerate(doc["items"]):
        ipath = f"items[{i}]"
        number = item["number"]
        if number in seen_numbers:
            errors.append(f"{ipath}.number: 题号 {number} 重复")
        if seen_numbers and number < seen_numbers[-1]:
            errors.append(f"{ipath}.number: 题号必须递增（{number} 出现在 {seen_numbers[-1]} 之后）")
        seen_numbers.append(number)
        score_sum += float(item["score"])

        subs: list[str] = []
        for j, blk in enumerate(item["blocks"]):
            bpath = f"{ipath}.blocks[{j}]"
            _check_block(blk, bpath, errors, warnings, allow_ansblock=False)
            if blk["t"] == "sub":
                if not _SUB_NO_RE.match(blk["no"]):
                    errors.append(f"{bpath}.no: 小问编号应形如 1-1，实际 {blk['no']!r}")
                if blk["no"] in subs:
                    errors.append(f"{bpath}.no: 小问编号 {blk['no']} 在同一题里重复")
                subs.append(blk["no"])

        if not subs:
            warnings.append(f"{ipath}: 没有 sub 小问编号块")

        ans = item["answer"]["blocks"]
        ans_subs: list[str] = []
        for k, blk in enumerate(ans):
            apath = f"{ipath}.answer.blocks[{k}]"
            _check_block(blk, apath, errors, warnings, allow_ansblock=True, in_answer=True)
            if blk["t"] == "ansblock" and blk.get("for"):
                target = blk["for"]
                if target not in subs:
                    errors.append(
                        f"{apath}.for: 指向的小问 {target} 在 blocks 里不存在"
                        f"（已有 {subs or '无'}）"
                    )
                if target in ans_subs:
                    errors.append(f"{apath}.for: 小问 {target} 出现了两个 ansblock")
                ans_subs.append(target)
        if subs and ans_subs and ans_subs != [s for s in subs if s in ans_subs]:
            warnings.append(f"{ipath}.answer: ansblock 顺序与 sub 顺序不一致（{ans_subs} vs {subs}）")

    total = meta.get("total_score")
    if total is not None and abs(float(total) - score_sum) > 0.01:
        warnings.append(
            f"meta.total_score={total} 与题目分值和 {score_sum:g} 不一致"
        )

    return errors, warnings


def check_doc(doc: Any) -> dict:
    """不合格就抛 PaperdocError，合格原样返回 doc。"""
    errors, _warnings = validate_doc(doc)
    if errors:
        raise PaperdocError(errors)
    return doc


def load_doc(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text("utf-8"))
    return check_doc(payload)


# ---------------------------------------------------------------- CLI
def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.services.paperdoc.schema",
                                 description="校验 paperdoc/v2 JSON 文件")
    ap.add_argument("path", nargs="+", help="paperdoc/v2 JSON 文件")
    ap.add_argument("--quiet", action="store_true", help="只打印结论")
    args = ap.parse_args(list(argv) if argv is not None else None)

    bad = 0
    for p in args.path:
        doc = json.loads(Path(p).read_text("utf-8"))
        errors, warnings = validate_doc(doc)
        if not args.quiet:
            for w in warnings:
                print(f"WARN  {w}")
        for e in errors:
            print(f"FAIL  {e}")
        status = "OK" if not errors else f"{len(errors)} error(s)"
        print(f"{p}: {status}, {len(warnings)} warning(s)")
        bad += 1 if errors else 0
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
