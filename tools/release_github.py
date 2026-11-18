"""把打好的 zip 发成一个 GitHub Release（代码推送见 tools/push_github.py）。

用法（token 只走环境变量，不落盘、不进仓库）：
    GH_TOKEN=xxxx .venv/Scripts/python tools/release_github.py --tag v0.4.0 \
        --name "神奋 0.4.0 · 第三版" --zip dist-app/神奋-完整版-20261118.zip

做的事：
1. 建 release（已存在就复用，不会报错）；
2. 把 zip 作为资产传到 uploads.github.com（747MB，慢慢传，失败自动重试 3 次）；
3. 打印资产 id / 大小 / 下载地址。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

OWNER = "spbbhyg"
REPO = "tiku-studio"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
UPLOADS = f"https://uploads.github.com/repos/{OWNER}/{REPO}"


def token() -> str:
    t = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not t:
        sys.exit("没有 GH_TOKEN：先 set 环境变量再跑")
    return t


def api(method: str, url: str, tk: str, **kw):
    headers = {"Authorization": f"Bearer {tk}", "Accept": "application/vnd.github+json",
               "User-Agent": "shenfen-release"}
    headers.update(kw.pop("headers", {}) or {})
    last = None
    for attempt in range(1, 4):
        try:
            r = httpx.request(method, url, headers=headers, timeout=120, **kw)
            if r.status_code < 300:
                return r
            if r.status_code in (401, 403, 404, 422):
                sys.exit(f"{method} {url} -> {r.status_code}: {r.text[:400]}")
            last = f"{r.status_code} {r.text[:200]}"
        except Exception as exc:                       # noqa: BLE001
            last = str(exc)
        print(f"  重试 {attempt}/3：{last}")
    sys.exit(f"{method} {url} 失败：{last}")


def find_release(tk: str, tag: str) -> dict | None:
    try:
        r = httpx.get(f"{API}/releases/tags/{tag}", timeout=30,
                      headers={"Authorization": f"Bearer {tk}", "User-Agent": "shenfen-release"})
        if r.status_code == 200:
            return r.json()
    except Exception:                                  # noqa: BLE001
        pass
    return None


def make_release(tk: str, tag: str, name: str, body: str) -> dict:
    got = find_release(tk, tag)
    if got:
        print(f"release {tag} 已存在（id={got['id']}），复用")
        return got
    r = api("POST", f"{API}/releases", tk, json={
        "tag_name": tag, "name": name, "body": body,
        "target_commitish": "main", "draft": False, "prerelease": False,
    })
    print(f"release 已创建：{tag}（id={r.json()['id']}）")
    return r.json()


def upload(tk: str, release_id: int, path: Path, name: str) -> dict:
    size = path.stat().st_size
    print(f"上传 {name}（{size/1024/1024:.0f} MB），网络慢的话要几分钟到几十分钟…")
    url = f"{UPLOADS}/releases/{release_id}/assets?name={name}"
    chunk = 1 << 20
    sent = 0

    def gen():
        nonlocal sent
        with path.open("rb") as fh:
            while True:
                b = fh.read(chunk)
                if not b:
                    break
                sent += len(b)
                if sent % (50 * chunk) < chunk:
                    print(f"  …{sent/1024/1024:.0f} / {size/1024/1024:.0f} MB", flush=True)
                yield b

    headers = {"Authorization": f"Bearer {tk}", "Content-Type": "application/zip",
               "Content-Length": str(size), "User-Agent": "shenfen-release"}
    last = None
    for attempt in range(1, 4):
        sent = 0
        try:
            with httpx.Client(timeout=None) as c:
                r = c.post(url, headers=headers, content=gen())
            if r.status_code in (200, 201):
                d = r.json()
                print(f"上传完成：id={d['id']} size={d['size']} state={d['state']}")
                return d
            last = f"{r.status_code} {r.text[:300]}"
        except Exception as exc:                        # noqa: BLE001
            last = str(exc)
        print(f"  上传失败（{last}），第 {attempt}/3 次重试…")
    sys.exit(f"上传失败：{last}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="v0.4.0")
    ap.add_argument("--name", default="神奋 0.4.0 · 第三版")
    ap.add_argument("--notes", default="")
    ap.add_argument("--notes-file", default="")
    ap.add_argument("--zip", required=True)
    ap.add_argument("--asset-name", default="")
    args = ap.parse_args()

    tk = token()
    body = args.notes
    if args.notes_file:
        body = Path(args.notes_file).read_text(encoding="utf-8")
    path = Path(args.zip)
    if not path.is_file():
        sys.exit(f"找不到 {path}")
    asset_name = args.asset_name or f"shenfen-{args.tag.lstrip('v')}-win64.zip"

    rel = make_release(tk, args.tag, args.name, body)
    have = {a["name"]: a for a in rel.get("assets") or []}
    if asset_name in have:
        a = have[asset_name]
        print(f"资产已存在：{a['name']}（{a['size']} 字节），跳过上传")
    else:
        a = upload(tk, rel["id"], path, asset_name)
    print(json.dumps({"release": rel["html_url"], "asset": a.get("browser_download_url"),
                      "id": a.get("id"), "size": a.get("size")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
