"""把 GitHub 仓库的源码同步到本地目录（`push_github.py` 的反向）。

为什么不用 git clone：本机到 `github.com:443` 不通（curl 直接 000 超时），
codeload 的整包 tarball 下到 16MB 就被掐断。改成：

    清单：https://api.github.com/repos/<owner>/<repo>/git/trees/<branch>?recursive=1   # 一次请求，带 size
    内容：https://raw.githubusercontent.com/<owner>/<repo>/<branch>/<path>            # 本机实测 200 / 2.4s

清单里有 `size`，所以先用"本地体积 == 远端体积"把绝大多数文件跳过（只对体积相同的
再比一次内容，最多几十个），几百个文件也能在十几秒内判断完。

用法：
    python tools/pull_github.py               # 写盘
    python tools/pull_github.py --dry         # 只报告
    python tools/pull_github.py --prune       # 顺手删掉"远端已没有"的源码文件（默认关）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER = "spbbhyg"
REPO = "tiku-studio"
BRANCH = "main"

LIST_API = f"https://api.github.com/repos/{OWNER}/{REPO}/git/trees/{BRANCH}?recursive=1"
FILE_FMTS = (
    f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{BRANCH}/{{path}}",
    f"https://cdn.jsdelivr.net/gh/{OWNER}/{REPO}@{BRANCH}/{{path}}",
)

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    "dist-app", "runtime", "dist", ".vite", ".idea", ".vscode",
}
SKIP_EXT = {".pyc", ".pyo", ".log", ".db", ".zip", ".exe", ".7z", ".err", ".dll"}
SKIP_NAMES = {".DS_Store", "Thumbs.db"}
# --prune 只在这些目录里动手，避免误删本地产物
PRUNE_PREFIX = ("apps/", "tools/", "ketcher-embed/", "docs/", "scripts/")

UA = {"User-Agent": "shenfen-pull/1.0"}


def _skip(path: str) -> bool:
    parts = path.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return True
    if parts[0] == "data":
        return True
    name = parts[-1]
    if name in SKIP_NAMES:
        return True
    return Path(name).suffix.lower() in SKIP_EXT


def _get(url: str, timeout: float = 45.0) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return r.read()


def _sha1(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--prune", action="store_true")
    ap.add_argument("--jobs", type=int, default=6)
    args = ap.parse_args()

    print(f"读取远端清单：{LIST_API}")
    try:
        meta = json.loads(_get(LIST_API))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"清单读取失败：HTTP {e.code}（匿名接口 60 次/小时，等一会儿再试）") from e

    blobs = [
        t for t in meta.get("tree", [])
        if t.get("type") == "blob" and not _skip(t["path"])
    ]
    remote = {t["path"]: int(t.get("size") or 0) for t in blobs}
    print(f"远端源码文件：{len(remote)} 个" + ("（清单被截断）" if meta.get("truncated") else ""))

    same: list[str] = []
    suspect: list[str] = []      # 体积一样，但要再比一次内容
    add: list[str] = []
    for path, size in remote.items():
        local = ROOT / path
        if not local.is_file():
            add.append(path)
        elif local.stat().st_size != size:
            suspect.append(path)
        else:
            same.append(path)

    print(f"体积相同 {len(same)} 个、需要复核 {len(suspect)} 个、本地缺失 {len(add)} 个")

    changed: list[str] = []
    failed: list[tuple[str, str]] = []

    def fetch(path: str) -> tuple[str, bytes | None, str]:
        last = ""
        for fmt in FILE_FMTS:
            try:
                return path, _get(fmt.format(path=urllib.parse.quote(path))), ""
            except Exception as e:  # noqa: BLE001
                last = str(e)
        return path, None, last

    targets = add + suspect
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        for path, blob, err in ex.map(fetch, targets):
            local = ROOT / path
            if err or blob is None:
                failed.append((path, err or "空内容"))
                continue
            if path in suspect and local.is_file() and _sha1(local.read_bytes()) == _sha1(blob):
                continue
            changed.append(path)
            if not args.dry:
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(blob)

    pruned: list[str] = []
    if args.prune:
        for local in ROOT.rglob("*"):
            if not local.is_file():
                continue
            rel = local.relative_to(ROOT).as_posix()
            if _skip(rel) or rel in remote or not rel.startswith(PRUNE_PREFIX):
                continue
            pruned.append(rel)
            if not args.dry:
                local.unlink()

    print()
    print(f"写入 {len(changed)} 个" + ("（--dry，未写盘）" if args.dry else "")
          + (f"、删除 {len(pruned)} 个" if pruned else ""))
    for p in sorted(changed):
        print("  *", p)
    for p in sorted(pruned):
        print("  -", p)
    if failed:
        print(f"\n!! 失败 {len(failed)} 个：")
        for p, e in failed[:10]:
            print("  ", p, "→", e)
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    main()
