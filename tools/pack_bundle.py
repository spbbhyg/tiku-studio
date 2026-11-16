"""组装「神奋 + 本地题库」完整目录，并生成一个安装导引文件。

输入：
  dist-app/tiku-studio/         pack.py 打出来的神奋本体
  D:\\化学竞赛题库\\chemy.exe    + _internal（题库本体，PyInstaller 产物）
  D:\\化学竞赛题库\\chemy\\       题库的数据与网页（data/chemy.db、web/…）

输出：
  dist-app/神奋-完整版/         可直接运行的目录（给内部人拷）
  桌面/神奋-一键安装.cmd         单文件安装导引（发给老师用这个）

题库是复制进来的，源目录一个字节都不动（用户第 23 条）。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dist-app" / "tiku-studio"
OUT = ROOT / "dist-app" / "神奋-完整版"
DESKTOP = Path.home() / "Desktop"
BANK = Path(r"D:\化学竞赛题库")
SEVENZIP = Path(r"C:\Program Files\7-Zip\7z.exe")

START_CMD = r"""@echo off
chcp 65001 >nul
cd /d %~dp0
echo 正在启动神奋（第一次会慢一点，浏览器稍后自己会打开）…
start "" tiku-studio.exe
"""

README = """神奋  题库 · 对纲 · 组卷（第二版）
=====================================

怎么用
  双击「启动-神奋.cmd」（或者桌面上的「神奋」），等几秒浏览器自己打开。
  题库跟神奋一起起来，界面左边「题库」那一页就是题库本体。

装了些什么
  tiku-studio.exe     神奋本体（本地服务，只在这台机器上跑）
  _internal\ apps\ services\ runtime\   程序文件，别删别挪
  chemy\              本地题库（chemy.exe + 它的数据）
  data\               你的数据：数据库、题图、草稿、导出（备份就备份这个目录）

更新
  设置 → 软件更新 → 检查更新（从 GitHub 拉最新版，自己下载、覆盖、重启）。

局域网
  设置 → 局域网：把上面的地址发给同办公室的老师，他们用浏览器打开就是同一份数据。
  人多时建议设一个访问码。

卸载
  删掉这个目录就行，不写注册表。数据在 data\ 里，想留就先拷出来。
"""


def step(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def copytree(src: Path, dst: Path, skip=()) -> None:
    if not src.is_dir():
        raise SystemExit(f"缺少目录：{src}")

    def ignore(dir_path: str, names: list[str]) -> set[str]:
        dropped = set()
        for n in names:
            if n in skip:
                dropped.add(n)
        return dropped

    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)


def build_tree() -> Path:
    step("清空输出目录")
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    step("复制神奋本体")
    copytree(APP, OUT, skip={"data"})
    (OUT / "data").mkdir(exist_ok=True)

    step("复制本地题库（只读复制，不动源目录）")
    bank_dir = OUT / "chemy"
    bank_dir.mkdir(exist_ok=True)
    for name in ("chemy.exe", "_internal"):
        src = BANK / name
        if src.is_dir():
            copytree(src, bank_dir / name)
        elif src.is_file():
            shutil.copy2(src, bank_dir / name)
        else:
            raise SystemExit(f"题库里找不到 {src}")
    # 题库自己的数据与网页（data/chemy.db 里有题，web/ 是它的界面）
    copytree(BANK / "chemy", bank_dir / "chemy",
             skip={"__pycache__", "cache", "build", "tmp", "samples"})

    step("写启动脚本与说明")
    (OUT / "启动-神奋.cmd").write_text(START_CMD, encoding="utf-8")
    (OUT / "使用说明.txt").write_text(README, encoding="utf-8")
    return OUT


def make_zip(src: Path, target: Path) -> Path:
    step("压缩（几分钟）")
    tmp = target.with_name("bundle-tmp.zip")
    ok = False
    if SEVENZIP.is_file():
        # -mx=1：zip 里再高的档位对 1.5G 的程序文件收益很小，却容易在长路径上出错
        r = subprocess.run([str(SEVENZIP), "a", "-tzip", "-mx=1", str(tmp), "."],
                           cwd=str(src), capture_output=True)
        ok = r.returncode == 0 and tmp.exists()
        if not ok:
            print("7z 失败，回退到 Python zipfile：", r.stderr.decode("utf-8", "replace")[:300])
    if not ok:
        import zipfile

        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for f in src.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(src).as_posix())
    if target.exists():
        target.unlink()
    tmp.replace(target)
    print(f"zip → {target}  ({target.stat().st_size / 1024 / 1024:.0f} MB)")
    return target


def human(n: int) -> str:
    return f"{n / 1024 / 1024:.0f} MB"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-installer", action="store_true", help="只组装目录，不生成单文件安装包")
    ap.add_argument("--out-dir", default=str(DESKTOP), help="安装导引文件放哪")
    args = ap.parse_args()

    t0 = time.time()
    src = build_tree()
    total = sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
    print(f"\n目录体积：{human(total)}  →  {src}")

    if args.no_installer:
        print(f"用时 {time.time() - t0:.0f} 秒")
        return 0

    stamp = datetime.now().strftime("%Y%m%d")
    zip_path = ROOT / "dist-app" / f"神奋-完整版-{stamp}.zip"
    make_zip(src, zip_path)

    step("生成安装导引文件（单文件自解压）")
    out = Path(args.out_dir) / "神奋-一键安装.cmd"
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_installer.py"),
                    "--src", str(src), "--out", str(out)], check=True)
    print(f"\n完成，用时 {time.time() - t0:.0f} 秒")
    print(f"目录：{src}")
    print(f"压缩包：{zip_path}（给内部人）")
    print(f"安装导引：{out}（发给老师，只发这一个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
