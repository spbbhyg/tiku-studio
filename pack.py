"""一键更新独立版：构建前端 → PyInstaller 打包 → 组装 dist-app → 打 zip 到桌面 → 清理中间件。

用法（项目根）：
    .venv\\Scripts\\python.exe pack.py            # 带当前 data/（推荐，题库一起带走）
    .venv\\Scripts\\python.exe pack.py --no-data  # 不带 data/，给别人用时是干净的
    .venv\\Scripts\\python.exe pack.py --no-zip   # 只打包不压缩（自己本机跑）

也可以直接双击桌面的「更新并打包独立版.cmd」。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "dist-app" / "tiku-studio"
DESKTOP = Path.home() / "Desktop"
SEVENZIP = Path(r"C:\Program Files\7-Zip\7z.exe")

# data/ 里不该进包的东西
DATA_SKIP_DIRS = {"ofm", "cache", "__pycache__", "samples", "tmp", ".cache"}
DATA_SKIP_FILES = {"service.lock", "server.log", "ofm.log", "pack.log"}


def step(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=str(cwd or ROOT), check=True)


def build_web() -> None:
    step("构建前端（apps/web → dist）")
    npm = Path(r"C:\Program Files\nodejs\npm.cmd")
    if not npm.exists():
        npm = Path("npm.cmd")
    run([npm, "run", "build"], cwd=ROOT / "apps" / "web")


def build_exe() -> None:
    step("PyInstaller 打后端 exe（约 1-3 分钟）")
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    run([py, "build_app.py"])


def copy_data() -> None:
    step("复制 data/（题库、图库、内置模型账号）")
    src = ROOT / "data"
    dst = OUT / "data"
    if not src.exists():
        dst.mkdir(parents=True, exist_ok=True)
        return

    def ignore(dir_path: str, names: list[str]) -> set[str]:
        p = Path(dir_path)
        skipped: set[str] = set()
        for n in names:
            if n in DATA_SKIP_DIRS or n in DATA_SKIP_FILES:
                skipped.add(n)
            elif n.startswith("tiku.db.bak"):
                skipped.add(n)
            elif (p / n).is_dir() and n.startswith("chem"):
                pass  # 保留结构式图
        return skipped

    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, ignore=ignore)
    size = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
    print(f"data/ → {size / 1024 / 1024:.0f} MB")


def make_zip() -> Path | None:
    step("打包 zip 到桌面")
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    target = DESKTOP / f"tiku-studio-独立版-{stamp}.zip"
    if SEVENZIP.exists():
        run([SEVENZIP, "a", "-tzip", "-mx=3", "-bso0", "-bsp0", str(target), "."], cwd=OUT)
    else:
        shutil.make_archive(str(target.with_suffix("")), "zip", root_dir=OUT.parent, base_dir=OUT.name)
        target = target.with_suffix(".zip")
    if not target.exists():
        print("！压缩失败，dist-app 仍然可用")
        return None
    print(f"zip → {target}  ({target.stat().st_size / 1024 / 1024:.0f} MB)")
    return target


def clean_build() -> None:
    step("清理中间件（build/ dist/）")
    for name in ("build", "dist"):
        p = ROOT / name
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            print(f"删除 {name}/")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-data", action="store_true", help="不把 data/ 打进包")
    ap.add_argument("--no-zip", action="store_true", help="只组装 dist-app，不压缩")
    args = ap.parse_args()

    t0 = time.time()
    build_web()
    build_exe()
    if not args.no_data:
        copy_data()
    z = None if args.no_zip else make_zip()
    clean_build()

    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print("\n" + "=" * 60)
    print(f"完成，用时 {time.time() - t0:.0f} 秒")
    print(f"可运行目录：{OUT}")
    print(f"目录体积：{total / 1024 / 1024:.0f} MB")
    if z:
        print(f"发布包：{z}")
        print("把这个 zip 拷到别的电脑，解压后双击「启动-tiku-studio.cmd」即可")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
