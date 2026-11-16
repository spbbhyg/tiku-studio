"""核对安装导引文件：把 .cmd 里的载荷切出来，解开，抽查几个文件和源目录是否一致。"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import zipfile
from pathlib import Path

MARK = b"__SHENFEN_PAYLOAD_V1__"
SRC = Path(r"C:\Users\Administrator\Documents\tiku-studio\dist-app\神奋-完整版")
CANDIDATES = [
    "tiku-studio.exe",
    "chemy/chemy.exe",
    "chemy/chemy/data/chemy.db",
    "apps/web/dist/index.html",
    "启动-神奋.cmd",
    "使用说明.txt",
]


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    cmd = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / "Desktop" / "神奋-一键安装.cmd")
    raw = cmd.read_bytes()
    idx = raw.find(MARK)
    if idx < 0:
        print("找不到载荷标记，导引文件坏了")
        return 1
    body = raw[:idx]
    off = len(body[: idx + len(MARK)].decode("utf-8").encode("utf-8"))
    print(f"导引文件 {cmd.name}  {len(raw) / 1024 / 1024:.0f} MB，说明部分 {idx} 字节，载荷起点 {off}")
    payload = raw[off:]
    with tempfile.TemporaryDirectory() as td:
        zpath = Path(td) / "payload.zip"
        zpath.write_bytes(payload)
        with zipfile.ZipFile(zpath) as zf:
            bad = zf.testzip()
            names = zf.namelist()
            print(f"载荷 zip 校验 {'OK' if bad is None else '坏了：' + bad}，条目 {len(names)}")
            ok = True
            for rel in CANDIDATES:
                try:
                    with zf.open(rel) as f:
                        h = hashlib.sha256()
                        for chunk in iter(lambda: f.read(1 << 20), b""):
                            h.update(chunk)
                    same = h.hexdigest() == sha(SRC / rel)
                except KeyError:
                    same = False
                print(f"  {rel:34s} {'一致' if same else '不一致/缺失'}")
                ok = ok and same
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
