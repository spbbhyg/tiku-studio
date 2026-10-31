"""配置文件与可自定义数据位置。

- 默认数据目录是 <仓库>/data
- 用户可在「设置 → 数据位置」里改到任意目录；选择结果写进
  <仓库>/storage.json（或用 TIKU_CONFIG 指向的 JSON），启动时读取。
- 环境变量优先级最高：TIKU_DATA_DIR / TIKU_DB_PATH / TIKU_WEB_DIST / TIKU_PORT
"""
import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent            # apps/api/app
ROOT = APP_DIR.parents[2]                            # 仓库根目录

WEB_DIST = Path(os.environ.get("TIKU_WEB_DIST", ROOT / "apps" / "web" / "dist"))

CONFIG_PATH = Path(os.environ.get("TIKU_CONFIG", ROOT / "storage.json"))
DEFAULT_DATA_DIR = ROOT / "data"


def _load_config() -> dict:
    try:
        if CONFIG_PATH.is_file():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


CFG = _load_config()


def _resolve_data_dir() -> Path:
    env = os.environ.get("TIKU_DATA_DIR")
    if env:
        return Path(env).expanduser()
    custom = (CFG.get("data_dir") or "").strip()
    if custom:
        return Path(custom).expanduser()
    return DEFAULT_DATA_DIR


DATA_DIR = _resolve_data_dir()
DB_PATH = Path(os.environ.get("TIKU_DB_PATH", DATA_DIR / "tiku.db"))
LIBRARY_DIR = DATA_DIR / "library"                   # 原件与题目图存放处（私有）
INBOX_DIR = DATA_DIR / "inbox"                       # Agent 拆解产物投递处
SCHEMA_DIR = ROOT / "packages" / "schema"

API_PORT = int(os.environ.get("TIKU_PORT", "8100"))


def ensure_dirs() -> None:
    for d in (DATA_DIR, LIBRARY_DIR, INBOX_DIR):
        d.mkdir(parents=True, exist_ok=True)


def save_data_dir(path: str | Path) -> None:
    """把自定义数据目录写进配置文件（下次启动生效）。"""
    cfg = dict(CFG)
    cfg["data_dir"] = str(Path(path))
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    CFG["data_dir"] = cfg["data_dir"]


def reset_data_dir() -> None:
    cfg = dict(CFG)
    cfg.pop("data_dir", None)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    CFG.pop("data_dir", None)


def storage_info() -> dict:
    return {
        "data_dir": str(DATA_DIR),
        "db_path": str(DB_PATH),
        "library_dir": str(LIBRARY_DIR),
        "inbox_dir": str(INBOX_DIR),
        "default_data_dir": str(DEFAULT_DATA_DIR),
        "config_path": str(CONFIG_PATH),
        "custom": bool((CFG.get("data_dir") or "").strip()),
        "from_env": bool(os.environ.get("TIKU_DATA_DIR") or os.environ.get("TIKU_DB_PATH")),
    }
