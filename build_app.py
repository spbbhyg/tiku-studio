"""一键打出独立版：dist-app/tiku-studio/

步骤：
1) PyInstaller 打后端 exe（只含 Python 代码）
2) 复制前端 dist、内置免费模型、node.exe 到 exe 旁边
3) 生成 启动-tiku-studio.cmd / 说明.txt
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "dist-app" / "tiku-studio"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    pyi = ROOT / ".venv" / "Scripts" / "pyinstaller.exe"
    if not pyi.exists():
        pyi = Path(sys.executable).parent / "pyinstaller.exe"

    if (ROOT / "build").exists():
        shutil.rmtree(ROOT / "build", ignore_errors=True)
    if (ROOT / "dist").exists():
        shutil.rmtree(ROOT / "dist", ignore_errors=True)

    run([str(pyi), "tiku-studio.spec", "--noconfirm", "--clean"])

    src = ROOT / "dist" / "tiku-studio"
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, OUT)

    # 运行期资源：放在 exe 旁边，改起来直观
    shutil.copytree(ROOT / "apps" / "web" / "dist", OUT / "apps" / "web" / "dist")
    shutil.copytree(ROOT / "services" / "ofm", OUT / "services" / "ofm")
    runtime = ROOT / "runtime"
    if runtime.exists():
        shutil.copytree(runtime, OUT / "runtime")

    (OUT / "data").mkdir(exist_ok=True)

    (OUT / "启动-tiku-studio.cmd").write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "cd /d %~dp0\r\n"
        "start \"\" tiku-studio.exe\r\n",
        encoding="utf-8",
    )

    (OUT / "说明.txt").write_text(
        "tiku-studio 独立版\n"
        "=================\n\n"
        "1. 双击「启动-tiku-studio.cmd」（或直接双击 tiku-studio.exe）\n"
        "2. 浏览器会自动打开 http://127.0.0.1:8100/\n"
        "3. 数据都在本目录 data/ 里（题库、图库、内置免费模型的账号与 key）\n\n"
        "不需要安装 Python、Node、DeepSeek Harness，也不需要联网登录：\n"
        "内置免费模型由 runtime\\node.exe + services\\ofm 在本机拉起（端口 18903）。\n\n"
        "目录说明：\n"
        "  tiku-studio.exe   主程序（后端 + 前端）\n"
        "  apps\\web\\dist     前端页面\n"
        "  services\\ofm      内置免费模型服务（Node 源码）\n"
        "  runtime\\node.exe  内置的 Node 运行时\n"
        "  data\\             你的数据（可整目录备份/迁移）\n\n"
        "想换端口：启动前设置环境变量 TIKU_PORT（默认 8100）。\n",
        encoding="utf-8",
    )

    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"\n完成：{OUT}")
    print(f"体积：{total/1024/1024:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
