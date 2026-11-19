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

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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

README = """神奋  题库 · 对纲 · 组卷（0.5.1 · 第四版修正）
=====================================

怎么用
  双击「启动-神奋.cmd」（或者桌面上的「神奋」），等几秒浏览器自己打开。
  题库跟神奋一起起来，界面左边「题库」那一页就是题库本体。

装了些什么
  tiku-studio.exe     神奋本体（本地服务，只在这台机器上跑）
  _internal\ apps\ services\ runtime\   程序文件，别删别挪
  chemy\              本地题库（chemy.exe + 它的数据）
  data\               你的数据：数据库、题图、草稿、导出（备份就备份这个目录）

出题页有四个模式（同一行按钮切换）
  画布   把文献裁图拖进来排版，导出走题库
  单题   纯文本 + 自动排版，A4 预览里图片能拖能缩放
  整卷   录大题小问，出「题目卷 + 答案卷」
  文档   直接用 Word 编辑 .docx（表格、修订、页眉页脚都保留），改完「保存到项目」
         工具条已全部中文化。上面一排「公式」「结构式」两个按钮能把化学公式和
         结构式直接插进 Word：
           公式   写 \ce{...}（化学式/反应式，箭头、条件、可逆箭头都支持）或普通 LaTeX，
                  点「预览」看效果，点「插到光标处」就进文档；勾「独立成行」=大公式居中。
           结构式 在弹出画板里画（支持反应箭头、文字标注），点「取结构并插入」整块进文档。
         第一次打开文档模式要等几秒（在加载 Word 引擎），等它出来别关页面；
         文档存在 data/docx 里，插图存在 data/docx-assets，备份就备份整个 data 目录。

三栏工作台（出题 → 文档）
  左栏写题目文本，中栏是真正的 Word，右栏是画布；左上「«」右上「»」可以收起左右两栏。
  左边写完点「生成排版稿 →」自动排成 Word 段落；「画布图片进文档」把画布上的图整块搬进 Word。
  「题目文档 / 答案文档」两个标签各自记着内容，来回切不会丢。

组卷与删题
  组卷页左栏按专题浏览 200+ 道题库原题，点一行就进卷；右栏的「×」只是从卷子移出，
  「从题库删除」才是真删（要勾确认）。卷名留空就不印标题，分值默认不印。

格式转换（四合一）
  文档互转（Word↔PDF）、PDF 拆分（按页码，打包 zip）、PDF 合并（可调顺序）、
  扫描件转 Word（OCR 认字）。都在本机转，每步会说明用了哪个引擎。

本地模型（可选）
  设置 → 本地模型：三档千问 0.5B / 1.5B / 3B，先装 Ollama 引擎（页面里有下载链接），
  再点「下载」把模型拉到本机，之后断网也能用；有独立显卡会自动用显卡。
  AI 工作台里还有「小工具」：给想法→写题干、翻译并保留公式。

报错
  任何报错的第一行都是中文说明该做什么（例如「连不上本地服务：先确认软件开着」），
  技术细节留在后面。

更新
  设置 → 软件更新 → 检查更新（从 GitHub 拉最新版，自己下载、覆盖、重启）。
  版本号会跟发布页对上，看到「已是最新」就说明没出新版。

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
        ok = r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 1024
        if not ok:
            msg = r.stderr.decode("utf-8", "replace")
            msg = "".join(ch if ord(ch) < 128 else "?" for ch in msg)[:200]
            print(f"7z 失败（退出码 {r.returncode}），回退到 Python zipfile：{msg}", flush=True)
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
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
