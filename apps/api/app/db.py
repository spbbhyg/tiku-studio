from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DB_PATH, ensure_dirs

ensure_dirs()
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _columns(conn, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


def _migrate() -> None:
    """轻量迁移：SQLite 不支持 ALTER 复杂列的增删，这里只做幂等的补列/删表。"""
    with engine.begin() as conn:
        tables = {r[0] for r in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
        if "questions" in tables:
            cols = _columns(conn, "questions")
            if "source_title" not in cols:
                conn.execute(text("ALTER TABLE questions ADD COLUMN source_title VARCHAR(200)"))
            if "source_citation" not in cols:
                conn.execute(text("ALTER TABLE questions ADD COLUMN source_citation TEXT"))
            if "layout" not in cols:
                conn.execute(text("ALTER TABLE questions ADD COLUMN layout JSON"))
        # 文献出题已并入主题库，独立表不再使用
        if "authoring_questions" in tables:
            conn.execute(text("DROP TABLE authoring_questions"))


def init_db() -> None:
    from . import models  # noqa: F401  确保模型注册

    Base.metadata.create_all(engine)
    _migrate()
