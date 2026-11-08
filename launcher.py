"""独立版启动器：起本地服务 + 内置免费模型，然后打开浏览器。

冻结（PyInstaller）后：exe 旁边找 web/ 与 services/ofm/、data/。
开发态：直接 python launcher.py。
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

PORT = int(os.environ.get("TIKU_PORT", "8100"))


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def open_when_ready(url: str, timeout: float = 60.0) -> None:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url.replace("/#/", "/api/stats").rstrip("/"), timeout=1.5).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.7)
    if os.environ.get("TIKU_NO_BROWSER") != "1":
        webbrowser.open(url)


def main() -> None:
    root = app_root()
    os.environ.setdefault("TIKU_ROOT", str(root))

    # 独立版自带 chromium headless shell（PDF 导出用），优先指向包内目录；
    # 没有的话回落到系统 %LOCALAPPDATA%\ms-playwright 或系统 Edge。
    pw = root / "runtime" / "ms-playwright"
    if pw.exists():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(pw))

    if port_busy(PORT):
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
        print(f"已经在运行：http://127.0.0.1:{PORT}/")
        return

    if getattr(sys, "frozen", False):
        sys.path.insert(0, str(root))
        # 冻结包里 app 目录以 tiku_apps 的形式随包
        sys.path.insert(0, str(root / "_internal")) if (root / "_internal").exists() else None

    from app.main import app  # noqa: E402
    import uvicorn  # noqa: E402

    threading.Thread(target=open_when_ready, args=(f"http://127.0.0.1:{PORT}/",), daemon=True).start()
    print(f"tiku-studio 启动中：http://127.0.0.1:{PORT}/")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
