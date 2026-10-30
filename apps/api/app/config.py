"""路径与运行配置。所有可变数据都在 data/ 下，不入 git。

可用环境变量覆盖：TIKU_DATA_DIR / TIKU_DB_PATH / TIKU_PORT / TIKU_WEB_DIST
"""
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent            # apps/api/app
ROOT = APP_DIR.parents[2]                            # 仓库根目录

WEB_DIST = Path(os.environ.get("TIKU_WEB_DIST", ROOT / "apps" / "web" / "dist"))

DATA_DIR = Path(os.environ.get("TIKU_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("TIKU_DB_PATH", DATA_DIR / "tiku.db"))
LIBRARY_DIR = DATA_DIR / "library"                   # 原件与题目图存放处（私有）
INBOX_DIR = DATA_DIR / "inbox"                       # Agent 拆解产物投递处
SCHEMA_DIR = ROOT / "packages" / "schema"

API_PORT = int(os.environ.get("TIKU_PORT", "8100"))


def ensure_dirs() -> None:
    for d in (DATA_DIR, LIBRARY_DIR, INBOX_DIR):
        d.mkdir(parents=True, exist_ok=True)
