"""SQLAlchemy engine + session factory for the shared SQLite file."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    """Shared declarative base for all SQLAlchemy models."""


def _make_engine() -> Engine:
    settings = get_settings()
    url = f"sqlite:///{settings.database_path}"
    # check_same_thread=False is safe because we use one session per request
    # and SQLite (with WAL) tolerates cross-thread access at the file level.
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 5.0},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode = WAL")
        cur.execute("PRAGMA foreign_keys = ON")
        cur.execute("PRAGMA busy_timeout = 5000")
        cur.execute("PRAGMA synchronous = NORMAL")
        cur.close()

    return engine


_engine: Engine = _make_engine()
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)


def get_engine() -> Engine:
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager that commits on success and rolls back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
