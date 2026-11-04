"""试卷渲染：HTML 打印视图（保底）+ Playwright 导出 PDF（可选增强）。"""
from __future__ import annotations

import base64
import html
import io
import mimetypes
import re
from urllib.parse import unquote

from sqlalchemy.orm import Session

from ..config import LIBRARY_DIR
from ..models import Paper


def _esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _stem_html(item) -> str:
    snap = item.snapshot
    stem = _esc(snap.get("stem"))
    # 手工换行保留
    stem = stem.replace("\n", "<br/>")
    if snap.get("options"):
        opts = "　　".join(f"{_esc(o)}" for o in snap["options"])
        stem += f'<div class="opts">{opts}</div>'
    if snap.get("images"):
        for img in snap["images"]:
            stem += f'<div class="qimg"><img src="/api/images/{_esc(img.get("path"))}" alt="题图"/></div>'
    return stem


_ALIGN_CSS = {"left": "left", "center": "center", "right": "right", "justify": "justify"}


def _item_style(snap: dict) -> str:
    """试题篮里为每道题设的排版：对齐、字号、颜色。"""
    parts: list[str] = []
    align = str(snap.get("align") or "").strip().lower()
    if align in _ALIGN_CSS:
        parts.append(f"text-align:{_ALIGN_CSS[align]}")
    try:
        size = float(snap.get("font_size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size:
        parts.append(f"font-size:{size:g}pt")
    color = str(snap.get("color") or "").strip()
    if color.startswith("#") and len(color) in (4, 7):
        parts.append(f"color:{color}")
    return ";".join(parts)


def render_paper_html(session: Session, paper: Paper, part: str = "all") -> str:
    """part: all（打印视图，题目+答案）/ questions（题目卷）/ answers（答案卷）。"""
    by_type: dict[str, list] = {}
    for it in paper.items:
        by_type.setdefault(it.snapshot.get("q_type", "其他"), []).append(it)

    q_rows: list[str] = []
    a_rows: list[str] = []
    for t, items in by_type.items():
        score = sum(it.score for it in items)
        q_rows.append(
            f"<div class='section-title'>{_esc(t)}（共 {len(items)} 题，{score:g} 分）</div>")
        q_rows.append("<ol class='qs'>")
        a_rows.append(f"<div class='section-title'>{_esc(t)}</div><ol class='qs anslist'>")
        for it in items:
            snap = it.snapshot or {}
            style = _item_style(snap)
            q_rows.append(
                f"<li data-pq='{it.id}'><div class='stem' contenteditable='true' style='{style}' "
                f"data-field='stem' title='可直接改题干，改完点上方「保存修改」'>{_stem_html(it)}"
                f"<span class='score'>({it.score:g}分)</span></div></li>")
            ans = it.snapshot.get("answer") or ""
            analysis = it.snapshot.get("analysis") or ""
            unit = it.snapshot.get("unit_name") or ""
            a_rows.append(
                f"<li data-ans-pq='{it.id}'><div class='ans'><b>答案：</b>"
                f"<span contenteditable='true' data-field='answer'>{_esc(ans)}</span>"
                + (f"　<b>解析：</b><span contenteditable='true' data-field='analysis'>{_esc(analysis)}</span>" if analysis else "")
                + (f"　<span class='src'>[{_esc(unit)}·{_esc(it.snapshot.get('source_book_title'))}]</span>" if unit else "")
                + "</div></li>")
        q_rows.append("</ol>")
        a_rows.append("</ol>")
    q_body = "\n".join(q_rows)
    a_body = "\n".join(a_rows)

    note = ""
    if (paper.config or {}).get("shortage"):
        note = "<p class='warn'>配额提示：" + _esc("；".join(paper.config["shortage"])) + "</p>"

    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>{_esc(paper.title)}</title>
<style>
  @page {{ size: A4; margin: 16mm 14mm; }}
  html {{ color-scheme: light; }}
  body {{ background: #fff; color: #111; font-family: "KaiTi","楷体","STKaiti","SimSun",serif; font-size: 13.5pt; line-height: 2; }}
  h1 {{ text-align:center; font-size: 19pt; margin: 0 0 4px; }}
  .meta {{ text-align:center; color:#333; font-size: 11pt; margin-bottom: 8px; }}
  .headrow {{ display:flex; justify-content: space-between; font-size: 12pt; margin: 10px 0 4px; }}
  .note {{ text-align:center; color:#555; font-size: 10.5pt; margin-bottom: 6px; }}
  .warn {{ color:#b00; font-size: 10.5pt; }}
  .section-title {{ font-weight: bold; font-size: 14pt; margin: 14px 0 2px; }}
  ol.qs {{ margin: 0 0 0 1.6em; padding: 0; }}
  ol.qs > li {{ margin-bottom: 10px; }}
  .score {{ color:#666; font-size: 10.5pt; }}
  .opts {{ margin-top: 2px; }}
  .qimg img {{ max-width: 60%; }}
  .ans {{ font-size: 10.5pt; color:#060; border-left: 3px solid #9c9; padding-left: 8px; margin: 2px 0 6px; }}
  .src {{ color:#999; }}
  .anssheet {{ page-break-before: always; }}
  .anssheet h2 {{ text-align:center; font-size: 16pt; }}
  .blank {{ border-bottom: 1px solid #333; display:inline-block; min-width: 3em; }}
  .editable:focus {{ outline: 2px dashed #2f6fed; outline-offset: 2px; background: #f8fbff; }}
  @media screen {{
    body {{ max-width: 800px; margin: 0 auto; padding: 24px; }}
    .toolbar {{ position: fixed; top: 8px; right: 8px; display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end; max-width: 340px; }}
    .toolbar button, .toolbar a {{ font-size: 13px; padding: 6px 12px; cursor: pointer; }}
    .toolbar a button {{ width: 100%; }}
  }}
  @media print {{ .toolbar {{ display: none }} }}
</style></head>
<body>
<div class="toolbar">
  <button onclick="window.print()">打印 / 另存为 PDF</button>
  <button onclick="savePaper()">保存修改</button>
  <a href="/api/papers/{paper.id}/export?part=questions"><button>下载题目卷</button></a>
  <a href="/api/papers/{paper.id}/export?part=answers"><button>下载答案卷</button></a>
</div>
<h1 id="ptitle" class="editable" contenteditable="true" title="点这里改标题">{_esc(paper.title)}</h1>
<div class="meta" contenteditable="true" title="可直接改科目/分数/时长">{_esc(paper.subject)} · 满分 {paper.total_score:g} 分 · 限时 {paper.duration_minutes} 分钟</div>
<div class="headrow"><span>姓名：＿＿＿＿＿＿</span><span>班级：＿＿＿＿＿＿</span><span>评分：＿＿＿＿＿＿</span></div>
<div class="note">{'答案卷（可单独打印）' if part == 'answers' else '（可直接在页面上改题干/答案，改完点「保存修改」；打印建议：A4 纵向）'}</div>
{note}
{'' if part == 'answers' else f'<div class="questions">{q_body}</div>'}
{'' if part == 'questions' else f'<div class="anssheet"><h2>参考答案（家长用）</h2><div class="questions">{a_body}</div></div>'}
<script>
async function savePaper() {{
  const items = {{}};
  document.querySelectorAll('[data-pq]').forEach((li) => {{
    const id = li.getAttribute('data-pq');
    const stemEl = li.querySelector('[data-field="stem"]');
    if (!stemEl) return;
    const clone = stemEl.cloneNode(true);
    clone.querySelectorAll('.score').forEach((x) => x.remove());
    items[id] = {{ id: Number(id), stem: clone.innerText.trim() }};
  }});
  document.querySelectorAll('[data-ans-pq]').forEach((li) => {{
    const id = li.getAttribute('data-ans-pq');
    const cur = items[id] || {{ id: Number(id) }};
    const a = li.querySelector('[data-field="answer"]');
    const n = li.querySelector('[data-field="analysis"]');
    if (a) cur.answer = a.innerText.trim();
    if (n) cur.analysis = n.innerText.trim();
    items[id] = cur;
  }});
  const t = document.getElementById('ptitle');
  const r = await fetch(location.pathname.replace(/\\/view$/, '/items'), {{
    method: 'PATCH', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ title: t ? t.innerText.trim() : '', items: Object.values(items) }}),
  }});
  if (r.ok) alert('已保存修改'); else alert('保存失败：' + (await r.text()));
}}
</script>
</body></html>"""


def _inline_images(html_doc: str) -> str:
    """把 <img src="/api/images/..."> 换成 base64 data URI。

    Playwright 用 set_content 渲染时页面来源是 about:blank，Chrome 会拦掉
    file:// 子资源，所以不能指望本地路径；内嵌最稳，也不依赖服务端口。
    """
    lib = LIBRARY_DIR.resolve()

    def repl(m):
        rel = unquote(m.group(1))
        path = (lib / rel)
        try:
            path = path.resolve()
            if not str(path).startswith(str(lib)) or not path.is_file():
                return m.group(0)
            mime = mimetypes.guess_type(str(path))[0] or "image/png"
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            return m.group(0)
        return f'src="data:{mime};base64,{b64}"'

    return re.sub(r'src="/api/images/([^"]+)"', repl, html_doc)


def export_pdf(session: Session, paper: Paper, part: str = "all") -> bytes:
    """用 Playwright 把打印视图转成 PDF。未安装时抛 RuntimeError。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "未安装 playwright：请执行 pip install playwright && playwright install chromium，"
            "或直接使用网页打印视图。") from e
    html_doc = _inline_images(render_paper_html(session, paper, part))
    # 兜底：仍未内嵌的题图退回资料库 file:// 绝对地址
    lib_base = LIBRARY_DIR.resolve().as_uri().rstrip("/") + "/"
    html_doc = html_doc.replace('src="/api/images/', f'src="{lib_base}')
    buf = io.BytesIO()
    try:
        with sync_playwright() as p:
            browser = None
            # 优先用 playwright 自带 Chromium；没有就借用系统已装的 Edge/Chrome（免下载）
            for kwargs in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
                try:
                    browser = p.chromium.launch(**kwargs)
                    break
                except Exception:
                    continue
            if browser is None:
                raise RuntimeError("没有可用的浏览器内核")
            page = browser.new_page()
            page.set_content(html_doc, wait_until="networkidle")
            pdf = page.pdf(format="A4", print_background=True)
            browser.close()
            buf.write(pdf)
    except Exception as e:
        raise RuntimeError(
            f"PDF 导出失败（{e}）。可执行 playwright install chromium，"
            "或直接用网页打印视图（/view 页面里点『打印』）。") from e
    return buf.getvalue()
