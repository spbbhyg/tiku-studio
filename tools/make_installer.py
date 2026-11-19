"""把「神奋 + 本地题库」打成一个安装导引文件：双击它，弹指引、按指引装好。

产物是一个 `.cmd`（自解压）：前面是纯 ASCII 的批处理，末尾挂着压缩包本体。
双击后：PowerShell 打中文指引 → cmd 问装到哪（`set /p`，兼容管道/自动化）→
PowerShell 切包+解压+建桌面快捷方式 → cmd 问要不要现在启动 → PowerShell 启动。

四个必须守住的坑（都踩过）：
  1. 载荷标记不能带 `<>`：cmd 会把命令行里的 `>>>` 当重定向，把 -Command 截断，
     PowerShell 报 "The string is missing the terminator"。
  2. 中文不能出现在 .cmd 正文里：`chcp` 之后 cmd 按字节读批处理，遇到多字节行会串行，
     出现 "'页，跟神奋是同一份数据）' is not recognized" 和
     "The filename, directory name, or volume label syntax is incorrect."。
     → 正文纯 ASCII，中文全部由 PowerShell 打。
  3. cmd 单行上限 8191：脚本不能用一条 -EncodedCommand 塞进去（会把 cmd 撑崩成 0xC0000409），
     要分块写进临时 .b64，命令行里只解码执行。
  4. PowerShell 读不到 cmd 重定向进来的 stdin（Read-Host/[Console]::In 都是空）：
     交互一律交给 cmd 的 `set /p`，PowerShell 只负责把问题打出来（-NoNewline）。

用法：
    .venv\\Scripts\\python tools\\make_installer.py --src dist-app\\神奋-完整版 --out 桌面\\神奋-一键安装.cmd
    .venv\\Scripts\\python tools\\make_installer.py --src ... --out ... --zip 已有的payload.zip   # 不重压
    .venv\\Scripts\\python tools\\make_installer.py --selftest     # 用小样验证解包链路
"""
from __future__ import annotations

import argparse
import base64
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# 不能带 < >（cmd 的重定向），也不能太常见（zip 里不能撞上）
MARK = b"__SHENFEN_PAYLOAD_V1__"
SEVENZIP = Path(r"C:\Program Files\7-Zip\7z.exe")

# 三步都在这段 PowerShell 里（UTF-16 传进去，可以放心写中文）。
# 由 $env:SF_STEP 决定跑哪一步：guide / main / launch。
MAIN_PS = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

function Say([string]$t = '', [string]$c = 'Gray') { Write-Host $t -ForegroundColor $c }
function Ask([string]$q) { Write-Host -NoNewline $q -ForegroundColor Gray }

$def = Join-Path $env:LOCALAPPDATA '神奋'
$raw = $env:SF_TARGET
if ([string]::IsNullOrWhiteSpace($raw)) { $dir = $def }
else {
  $dir = $raw.Trim().Trim('"').Trim()
  if ($dir -match '^[A-Za-z]:\\+$') { $dir = $dir.Substring(0, 2) + '\' }
  elseif ($dir.Length -gt 3) { $dir = $dir.TrimEnd('\') }
}

function ShowBanner {
  Say ''
  Say '  ============================================================'
  Say '     神 奋   题库 · 对纲 · 组卷' 'White'
  Say '  ============================================================'
  Say ''
  Say '  这个文件里已经带上了全部程序，不用再下载别的东西：'
  Say ''
  Say '    · 神奋本体（出题 / 组卷 / 精读 / 格式转换 / 局域网共备）'
  Say '    · 本地题库（题库、专题、打印页，跟神奋是同一份数据）'
  Say '    · 启动器、说明、桌面快捷方式'
  Say ''
  Say '  安装只是解压到你自己选的目录：不写注册表、不改系统设置。'
  Say '  数据放在安装目录的 data 里，想卸载就删掉那个目录。'
  Say ''
  Say '  ------------------------------------------------------------'
  Say ''
}

function DoGuide {
  ShowBanner
  Ask "  装到哪个目录？（直接回车 = $def）: "
}

function DoInstall {
  Say ''
  Say "  安装到：$dir"
  Say '  正在准备（文件比较大，要等一会儿，别关这个窗口）…'
  Say ''

  $mark = $env:SF_MARK
  $self = $env:SF_SELF
  $zip = Join-Path $env:TEMP ('shenfen-' + [Guid]::NewGuid().ToString('N').Substring(0, 12) + '.zip')

  # 1) 从自己身上切出压缩包
  $fs = [IO.File]::OpenRead($self)
  try {
    $buf = New-Object byte[] 4194304
    $n = $fs.Read($buf, 0, $buf.Length)
    $txt = [Text.Encoding]::UTF8.GetString($buf, 0, $n)
    $i = $txt.IndexOf($mark)
    if ($i -lt 0) { throw '文件不完整（找不到数据段），请重新拷一遍这个安装文件' }
    $off = [Text.Encoding]::UTF8.GetByteCount($txt.Substring(0, $i)) + [Text.Encoding]::UTF8.GetByteCount($mark)
    $fs.Position = $off
    $out = [IO.File]::Create($zip)
    try { $fs.CopyTo($out) } finally { $out.Close() }
  } finally { $fs.Close() }
  Say ('  取到安装包：{0:N0} MB' -f ((Get-Item $zip).Length / 1MB))

  # 2) 解压
  if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  Say '  正在解压（几分钟，别关窗口）…'
  Expand-Archive -LiteralPath $zip -DestinationPath $dir -Force
  Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
  Say '  文件已解压'

  # 3) 桌面快捷方式
  Say '  正在建桌面快捷方式…'
  try {
    $ws = New-Object -ComObject WScript.Shell
    $lnkPath = [IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), '神奋.lnk')
    $lnk = $ws.CreateShortcut($lnkPath)
    $lnk.TargetPath = [IO.Path]::Combine($dir, '启动-神奋.cmd')
    $lnk.WorkingDirectory = $dir
    $lnk.IconLocation = [IO.Path]::Combine($dir, 'tiku-studio.exe') + ',0'
    $lnk.Description = '神奋：题库 · 对纲 · 组卷'
    $lnk.Save()
  } catch { Say ('  （桌面快捷方式没建上，不影响使用：进安装目录双击「启动-神奋.cmd」即可）') 'Yellow' }

  Say ''
  Say '  ============================================================'
  Say '     装好了' 'Green'
  Say '  ============================================================'
  Say ''
  Say '    · 桌面上多了「神奋」，双击就能用'
  Say "    · 安装位置：$dir"
  Say '    · 第一次启动会慢一点（要起本地服务），浏览器会自己打开'
  Say '    · 题库跟神奋一起启动，题都在「题库」那一页'
  Say ''
  Ask '  现在启动？（直接回车 = 启动，输 n = 稍后）: '
}

function DoLaunch {
  Say ''
  $go = $env:SF_LAUNCH
  if ($go -notmatch '^\s*[nN]') {
    Start-Process -FilePath ([IO.Path]::Combine($dir, '启动-神奋.cmd'))
    Say '  已开始启动。浏览器过一会儿会自己打开；没打开就再双击桌面上的「神奋」。' 'Green'
    Say ''
  }
  Say '  装完了，这个安装文件可以删了。'
}

try {
  switch ($env:SF_STEP) {
    'guide'  { DoGuide }
    'main'   { DoInstall }
    'launch' { DoLaunch }
    default  { throw "未知步骤：$($env:SF_STEP)" }
  }
} catch {
  Say ''
  Say ('  !! 没装成功：' + $_.Exception.Message) 'Red'
  Say '  !! 常见原因：神奋还开着（先退干净再装）、目标目录没权限（换 D:\神奋 再试）、' 'Red'
  Say '  !! 或者杀毒软件拦了 PowerShell 解压。' 'Red'
  exit 1
}
exit 0
"""

# 正文：只有 ASCII（含注释）。中文全部由上面的脚本打。
# 脚本走「分块写进临时 .b64 → 命令行里只解码执行」：cmd 单行上限 8191，
# 一条 -EncodedCommand 塞进去会把 cmd 撑崩（0xC0000409）。
HEADER_TMPL = r"""@echo off
setlocal
title Shenfen Setup
set "SF_SELF=%~f0"
set "SF_MAIN=%TEMP%\shenfen-setup-%RANDOM%%RANDOM%.b64"
rem keep the mark split so this file never contains it verbatim
set "SF_MARK=__SHENFEN"
set "SF_MARK=%SF_MARK%_PAYLOAD_V1__"
@@CHUNKS@@

if defined SF_TARGET goto :install
set "SF_STEP=guide"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=$env:SF_MAIN; $s=[IO.File]::ReadAllText($p); Invoke-Expression ([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($s)))" < nul
if errorlevel 1 goto :fail

set /p "SF_TARGET="

:install
set "SF_STEP=main"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=$env:SF_MAIN; $s=[IO.File]::ReadAllText($p); Invoke-Expression ([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($s)))" < nul
if errorlevel 1 goto :fail

if defined SF_LAUNCH goto :launch
set /p "SF_LAUNCH="

:launch
set "SF_STEP=launch"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=$env:SF_MAIN; $s=[IO.File]::ReadAllText($p); Invoke-Expression ([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($s)))" < nul
del /q "%SF_MAIN%" >nul 2>&1
if errorlevel 1 goto :fail

echo.
pause
exit /b 0

:fail
del /q "%SF_MAIN%" >nul 2>&1
echo.
echo   ============================================================
echo     Setup did not finish. See the message above.
echo     Common causes: no write permission on that folder,
echo     or antivirus blocking PowerShell from extracting.
echo   ============================================================
echo.
pause
exit /b 1
"""

CHUNK = 6000


def encoded(ps: str) -> str:
    """PowerShell 用的是 UTF-16LE 的 base64。"""
    return base64.b64encode(ps.encode("utf-16-le")).decode("ascii")


def header() -> bytes:
    b64 = encoded(MAIN_PS)
    parts = [b64[i:i + CHUNK] for i in range(0, len(b64), CHUNK)]
    lines = ['> "%SF_MAIN%" echo ' + parts[0]]
    lines += ['>> "%SF_MAIN%" echo ' + part for part in parts[1:]]
    text = HEADER_TMPL.replace("@@CHUNKS@@", "\n".join(lines))
    bad_chars = sorted({ch for ch in text if ord(ch) >= 128})
    if bad_chars:
        raise SystemExit(f"安装导引正文必须是纯 ASCII（cmd 会串行），发现了：{bad_chars[:10]}")
    return text.replace("\n", "\r\n").encode("ascii")


def make_zip(src: Path, out: Path) -> None:
    """把 src 目录打成 zip（优先 7z，压得快；7z 在大目录上会抽风，失败就换 zipfile）。"""
    if SEVENZIP.is_file():
        cmd = [str(SEVENZIP), "a", "-tzip", "-mx=1", "-bso0", "-bsp0", str(out), "."]
        r = subprocess.run(cmd, cwd=str(src))
        if r.returncode == 0 and out.is_file() and out.stat().st_size > 1024:
            return
        print(f"  7z 失败（退出码 {r.returncode}），改用 Python zipfile 重压，慢一点但稳")
        try:
            out.unlink()
        except OSError:
            pass
    print("  （没装 7-Zip，用 Python 的 zipfile，会慢一点）")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src).as_posix())


def build(src: Path, out: Path, reuse_zip: Path | None = None) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="sf-pack-"))
    try:
        if reuse_zip:
            zip_path = reuse_zip
            print(f"复用压缩包 {zip_path}")
        else:
            zip_path = tmp / "payload.zip"
            print(f"打包 {src} → {zip_path}")
            make_zip(src, zip_path)
        size = zip_path.stat().st_size
        print(f"  压缩包 {size / 1024 / 1024:.0f} MB")

        head = header()
        if MARK in head:
            raise SystemExit("脚本正文里出现了载荷标记，会把切包位置搞错")

        with out.open("wb") as fh:
            fh.write(head)
            fh.write(MARK)
            with zip_path.open("rb") as z:
                shutil.copyfileobj(z, fh, 1 << 20)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"安装导引文件：{out}  ({out.stat().st_size / 1024 / 1024:.0f} MB)")


def selftest() -> int:
    """用小样验证：标记只出现一次、Python 与 PowerShell 切出来的字节完全一致。"""
    tmp = Path(tempfile.mkdtemp(prefix="sf-test-"))
    src = tmp / "sample"
    (src / "sub").mkdir(parents=True)
    (src / "启动-神奋.cmd").write_text("@echo off\r\necho 神奋 OK\r\npause\r\n", encoding="utf-8")
    (src / "sub" / "readme.txt").write_text("题库在这里\n", encoding="utf-8")
    out = tmp / "安装导引.cmd"
    build(src, out)

    data = out.read_bytes()
    if data.count(MARK) != 1:
        print("标记出现了", data.count(MARK), "次，应该只有 1 次")
        return 1
    idx = data.index(MARK)
    payload = tmp / "cut.zip"
    payload.write_bytes(data[idx + len(MARK):])
    dest = tmp / "installed"
    with zipfile.ZipFile(payload) as zf:
        names = zf.namelist()
        zf.extractall(dest)
    ok = (dest / "启动-神奋.cmd").is_file() and (dest / "sub" / "readme.txt").is_file()
    print("包里文件：", names)
    print("Python 切包解压：", "OK" if ok else "FAIL")
    if not ok:
        return 1

    ps = tmp / "cut.ps1"
    ps.write_text(r"""
$ErrorActionPreference = 'Stop'
$m = $env:SF_MARK
$fs = [IO.File]::OpenRead($env:SF_SELF)
try {
  $buf = New-Object byte[] 4194304
  $n = $fs.Read($buf, 0, $buf.Length)
  $txt = [Text.Encoding]::UTF8.GetString($buf, 0, $n)
  $i = $txt.IndexOf($m)
  if ($i -lt 0) { Write-Host 'NO MARK'; exit 2 }
  $off = [Text.Encoding]::UTF8.GetByteCount($txt.Substring(0, $i)) + [Text.Encoding]::UTF8.GetByteCount($m)
  $fs.Position = $off
  $dst = [IO.File]::Create($env:SF_OUT)
  try { $fs.CopyTo($dst) } finally { $dst.Close() }
} finally { $fs.Close() }
Write-Host ('CUT {0:N0} bytes' -f (Get-Item $env:SF_OUT).Length)
""".strip(), encoding="utf-8-sig")
    env = dict(os.environ, SF_SELF=str(out), SF_OUT=str(tmp / "ps-cut.zip"), SF_MARK=MARK.decode())
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps)],
                   env=env, check=False)
    same = (tmp / "ps-cut.zip").is_file() and (tmp / "ps-cut.zip").read_bytes() == payload.read_bytes()
    print("PowerShell 切包一致：", "OK" if same else "FAIL")
    print("样本目录：", tmp)
    return 0 if same else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", help="要打包的目录（里面应该有 启动-神奋.cmd）")
    ap.add_argument("--out", help="生成的安装导引文件路径（.cmd）")
    ap.add_argument("--zip", help="复用已有的 payload.zip，不重新压缩")
    ap.add_argument("--selftest", action="store_true", help="用小样验证打包/解包链路")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.src or not args.out:
        ap.error("要么 --selftest，要么同时给 --src 和 --out")
    src = Path(args.src).resolve()
    if not src.is_dir():
        print(f"没有这个目录：{src}")
        return 2
    reuse = Path(args.zip).resolve() if args.zip else None
    if reuse and not reuse.is_file():
        print(f"没有这个压缩包：{reuse}")
        return 2
    build(src, Path(args.out).resolve(), reuse)
    return 0


if __name__ == "__main__":
    sys.exit(main())
