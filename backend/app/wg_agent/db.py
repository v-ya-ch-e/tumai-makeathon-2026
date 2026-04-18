"""SQLModel engine, session factory, and SQLite WAL setup."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, create_engine

from . import crypto

_DEFAULT_DB_PATH = Path.home() / ".wg_hunter" / "app.db"


def _default_sqlite_url() -> str:
    _DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{_DEFAULT_DB_PATH.resolve()}"


def _resolve_database_url() -> str:
    raw = os.environ.get("WG_DB_URL")
    if raw:
        return raw
    return _default_sqlite_url()


DATABASE_URL = _resolve_database_url()

_connect_args = (
    {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)
engine = create_engine(DATABASE_URL, connect_args=_connect_args, echo=False)


@event.listens_for(engine, "connect")
def _sqlite_wal(dbapi_connection, connection_record) -> None:  # noqa: ARG001
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def init_db() -> None:
    crypto.ensure_key()
    with engine.connect() as conn:
        conn.close()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
