"""Chromium（Playwright）统一入口。

为什么单独抽一份：便携版把浏览器放在安装目录的 `runtime/ms-playwright`，
而 Playwright 默认去 `%LOCALAPPDATA%` 找，找不到就报
"Executable doesn't exist"；公式渲染（doce.py）一开始没设这个环境变量，
用户换一台电脑就必然弹「没有可用的浏览器内核」。所有需要浏览器的地方
都要从这里走，才不会各写一半。

查找顺序：
    1. 已经设了 PLAYWRIGHT_BROWSERS_PATH（launcher.py 会设）→ 直接用
    2. %TIKU_ROOT%/runtime/ms-playwright            （便携版）
    3. 源码树 runtime/ms-playwright                  （开发机）
    4. PyInstaller 解包目录 _MEIPASS/runtime/ms-playwright
启动顺序：
    内置 chromium → 系统 Edge（channel=msedge）→ 系统 Chrome（channel=chrome）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_KEY = "PLAYWRIGHT_BROWSERS_PATH"


def _candidates() -> list[Path]:
    out: list[Path] = []
    root = os.environ.get("TIKU_ROOT")
    if root:
        out.append(Path(root) / "runtime" / "ms-playwright")
    here = Path(__file__).resolve()
    # apps/api/app/services/browser.py → parents[4] = 项目根
    try:
        out.append(here.parents[4] / "runtime" / "ms-playwright")
    except IndexError:
        pass
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        out.append(Path(meipass) / "runtime" / "ms-playwright")
        exe_dir = Path(sys.executable).resolve().parent
        out.append(exe_dir / "runtime" / "ms-playwright")
    return out


def ensure_path() -> str:
    """把 PLAYWRIGHT_BROWSERS_PATH 指到随包的浏览器目录，返回当前值（可能是空串）。"""
    cur = os.environ.get(ENV_KEY) or ""
    if cur and Path(cur).is_dir():
        return cur
    for p in _candidates():
        if p.is_dir():
            os.environ[ENV_KEY] = str(p)
            return str(p)
    return cur


def browsers_ready() -> bool:
    p = ensure_path()
    return bool(p) and Path(p).is_dir()


def launch(pw, *, headless: bool = True):
    """在给定的 playwright 实例上启动浏览器；内置不行就回落系统 Edge / Chrome。"""
    errors: list[str] = []
    tries = [
        ({"args": ["--no-sandbox"], "headless": headless}, "内置 Chromium"),
        ({"channel": "msedge", "headless": headless}, "系统 Edge"),
        ({"channel": "chrome", "headless": headless}, "系统 Chrome"),
    ]
    for kwargs, label in tries:
        try:
            return pw.chromium.launch(**kwargs)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {str(exc).splitlines()[0][:120]}")
    raise BrowserMissing("；".join(errors))


class BrowserMissing(RuntimeError):
    """三路都起不来时的异常——调用方负责转成中文提示。"""

    def user_message(self) -> str:
        if not browsers_ready():
            return ("公式/排版渲染组件缺失：安装目录下没找到 runtime\\ms-playwright。"
                    "怎么办：重装一次（安装包自带），或在设置 →「软件更新」里点「修复组件」。")
        return ("浏览器起不来（可能被杀毒软件拦了，或系统 Edge/Chrome 版本太旧）。"
                "怎么办：先在设置 →「软件更新」里点「修复组件」；还不行就重装一次。")
