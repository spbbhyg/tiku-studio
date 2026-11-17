"""用 GitHub Git Data API 把本地源码推上去（本机 github.com:443 走不通，只能走 api.github.com）。

用法：python tools/push_github.py [--dry]
token 从环境变量 GH_TOKEN 读，绝不写进仓库。
"""
from __future__ import annotations

import base64
import hashlib
import time
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER, REPO, BRANCH = "spbbhyg", "tiku-studio", "main"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
             "dist-app", "runtime", "dist", ".vite", ".idea", ".vscode"}
SKIP_EXT = {".pyc", ".pyo", ".log", ".db", ".zip", ".exe", ".7z", ".err", ".dll"}
SKIP_NAMES = {".DS_Store", "Thumbs.db"}


def token() -> str:
    t = os.environ.get("GH_TOKEN", "").strip()
    if not t:
        raise SystemExit("没有 GH_TOKEN 环境变量")
    return t


def api(path: str, method: str = "GET", body: dict | None = None):
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token()}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    last = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            if e.code in (500, 502, 503, 504) and attempt < 4:
                time.sleep(2 + attempt * 3)
                last = f"{e.code}: {detail}"
                continue
            raise SystemExit(f"GitHub API {method} {path} → {e.code}: {detail}") from e
        except Exception as e:            # noqa: BLE001  网络被重置时重试
            last = str(e)
            if attempt < 4:
                print(f"  重试 {attempt + 1}/4：{e}")
                time.sleep(2 + attempt * 3)
                continue
            raise SystemExit(f"GitHub API {method} {path} 连不上：{last}") from e
    raise SystemExit(f"GitHub API {method} {path} 失败：{last}")


def tracked_files() -> list[Path]:
    out: list[Path] = []
    for base, dirs, files in os.walk(ROOT):
        b = Path(base)
        rel_dir = b.relative_to(ROOT)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (b / d / ".gitignore").exists()]
        if rel_dir.parts and rel_dir.parts[0] == "data":
            continue
        for f in files:
            p = b / f
            rel = p.relative_to(ROOT)
            if rel.parts[0] == "data":
                continue
            if p.suffix.lower() in SKIP_EXT or f in SKIP_NAMES:
                continue
            if p.stat().st_size > 40 * 1024 * 1024:
                continue
            out.append(rel)
    return sorted(out)


def blob_sha(data: bytes) -> str:
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def main() -> None:
    dry = "--dry" in sys.argv
    ref = api(f"/git/ref/heads/{BRANCH}")
    head = ref["object"]["sha"]
    commit = api(f"/git/commits/{head}")
    base_tree = commit["tree"]["sha"]
    tree = api(f"/git/trees/{base_tree}?recursive=1")
    remote = {e["path"]: e["sha"] for e in tree.get("tree", []) if e["type"] == "blob"}
    print(f"remote {head[:8]} tree={base_tree[:8]} files={len(remote)} truncated={tree.get('truncated')}")

    files = tracked_files()
    print(f"local files={len(files)}")
    changed, new = [], []
    for rel in files:
        data = (ROOT / rel).read_bytes()
        sha = blob_sha(data)
        key = rel.as_posix()
        if remote.get(key) == sha:
            continue
        (new if key not in remote else changed).append((key, data, sha))
    print(f"需要上传：新增 {len(new)} / 修改 {len(changed)}")
    for k, _, _ in (new + changed)[:20]:
        print("  ", k)
    if dry:
        return

    entries = []
    total = len(new) + len(changed)
    for i, (key, data, sha) in enumerate(new + changed, 1):
        print(f"  [{i}/{total}] {key} ({len(data)} 字节)")
        if len(data) < 30 * 1024 * 1024:
            r = api("/git/blobs", "POST", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
        else:
            r = api("/git/blobs", "POST", {"content": data.decode("utf-8", "replace"), "encoding": "utf-8"})
        entries.append({"path": key, "mode": "100644", "type": "blob", "sha": r["sha"]})
    if not entries:
        print("远端已经是最新的，没有需要提交的内容")
        return
    nt = api("/git/trees", "POST", {"base_tree": base_tree, "tree": entries})
    msg = ("出题页新增「文档」模式（SuperDoc）：Word 原件编辑 + 化学公式/结构式插入\n\n"
           "- 文档模式与画布/单题/整卷同级，支持打开本机 docx、保存回项目、导出 docx\n"
           "- 工具条与提示全部中文化（DOM 层精确词条翻译，不动正文）\n"
           "- 新增 /api/doce/formula、/api/doce/structure：KaTeX(mhchem) + Ketcher → 透明 PNG 插进 Word\n"
           "- 新增 /api/doce 文件管理（data/docx）\n")
    nc = api("/git/commits", "POST", {"message": msg, "tree": nt["sha"], "parents": [head]})
    api(f"/git/refs/heads/{BRANCH}", "PATCH", {"sha": nc["sha"], "force": False})
    print("已推送：", nc["sha"], "→", f"https://github.com/{OWNER}/{REPO}/commit/{nc['sha']}")


if __name__ == "__main__":
    main()
