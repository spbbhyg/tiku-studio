"""导入拆解产物 bundle 到数据库。

用法：
  python scripts/import_bundle.py seed/questions-xxx.json
  python scripts/import_bundle.py data/inbox/*.json

数据库位置：data/tiku.db（可用环境变量 TIKU_DB_PATH 覆盖）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.db import SessionLocal, init_db          # noqa: E402
from app.services.bundle_import import import_bundle_file  # noqa: E402


def main(paths: list[str]) -> int:
    init_db()
    rc = 0
    for p in paths:
        with SessionLocal() as db:
            try:
                result = import_bundle_file(db, Path(p))
                db.commit()
                print(f"[OK] {p} -> {result}")
            except Exception as e:
                db.rollback()
                print(f"[FAIL] {p}: {e}")
                rc = 1
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
