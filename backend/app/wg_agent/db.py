"""SQLModel engine + session factory (MySQL-only).

No Alembic. Schema changes are expected to be additive on an empty dev DB —
`init_db` calls `SQLModel.metadata.create_all(engine)` on startup, which
creates missing tables on first boot and silently no-ops on subsequent
boots. Destructive changes require a `DROP DATABASE; CREATE DATABASE`
(see docs/SETUP.md "Reset the database").
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from . import crypto
from . import db_models as _db_models  # noqa: F401  (registers tables on SQLModel.metadata)


def _resolve_database_url() -> str:
    raw = os.environ.get("WG_DB_URL")
    if not raw:
        raise RuntimeError(
            "WG_DB_URL is not set. WG Hunter requires a MySQL connection string; "
            "copy .env.example to .env and fill in the team-shared AWS MySQL "
            "credentials (mysql+pymysql://user:pass@host:3306/wg_hunter?charset=utf8mb4)."
        )
    return raw


DATABASE_URL = _resolve_database_url()

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,
    echo=False,
)


def init_db() -> None:
    """Ensure the Fernet key + schema exist. Safe to call repeatedly."""
    crypto.ensure_key()
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
