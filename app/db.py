"""Database engine, sessions, and migrations."""

from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine

from app.config import Settings, get_settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


@lru_cache
def _engine_for(url: str) -> Engine:
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        # The run worker writes while web requests read and write: WAL lets readers proceed,
        # and busy_timeout makes concurrent writers wait instead of failing.
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    return engine


def get_engine(settings: Settings | None = None) -> Engine:
    return _engine_for((settings or get_settings()).database_url)


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(get_engine()) as session:
        yield session


def alembic_config(settings: Settings | None = None) -> Config:
    settings = settings or get_settings()
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    # Don't let alembic.ini's logging config replace ours.
    cfg.attributes["configure_logger"] = False
    return cfg


def migrate(settings: Settings | None = None) -> None:
    """Create the data dir and bring the DB schema to the latest migration."""
    settings = settings or get_settings()
    settings.ensure_data_dir()
    command.upgrade(alembic_config(settings), "head")


def db_exists(settings: Settings | None = None) -> bool:
    return (settings or get_settings()).db_path.exists()
