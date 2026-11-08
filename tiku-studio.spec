# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把后端 + 前端 dist + 内置免费模型源码打成一个文件夹。

运行：
  .venv\\Scripts\\pyinstaller.exe tiku-studio.spec --noconfirm
产物：
  dist/tiku-studio/  （双击 tiku-studio.exe 即用）
"""
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(os.getcwd()).resolve()
API = ROOT / "apps" / "api"
DIST_WEB = ROOT / "apps" / "web" / "dist"
OFM = ROOT / "services" / "ofm"

# 只把后端 Python 包打进去；前端 dist、内置免费模型、node.exe 由 build_app.py 复制到 exe 旁边，
# 这样 data/ services/ runtime/ 都在安装目录里，路径直观、方便替换。
datas = [
    (str(API / "app"), "app"),
]
for pkg in ("pymupdf", "matplotlib", "rapidocr_onnxruntime", "onnxruntime", "cv2", "shapely"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

hiddenimports = [
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto", "uvicorn.protocols",
    "uvicorn.protocols.http", "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan", "uvicorn.lifespan.on",
    "aiosqlite", "anyio._backends._asyncio", "multipart", "python_multipart",
    "sqlalchemy.sql.default_comparator", "email.mime.text",
    "pymupdf", "pymupdf.layout", "pymupdf.table", "pymupdf.utils",
    "rapidocr_onnxruntime", "onnxruntime", "cv2", "shapely", "pyclipper",
    "matplotlib", "matplotlib.backends.backend_agg",
]

a = Analysis(
    ["launcher.py"],
    pathex=[str(ROOT), str(API)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib.tests", "pytest", "PyQt5", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="tiku-studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="tiku-studio")
