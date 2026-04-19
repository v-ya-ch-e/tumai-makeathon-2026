"""SQLModel engine + session factory (MySQL-only).

Database connection is assembled from five required env vars:
`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`. All five are
required — `db.py` refuses to import if any are missing, so a
misconfigured environment fails loud instead of silently writing to a
phantom DB.

No Alembic. Schema changes are expected to be additive on an empty dev DB —
`init_db` calls `SQLModel.metadata.create_all(engine)` on startup, which
creates missing tables on first boot and silently no-ops on subsequent
boots. Destructive changes require a `DROP DATABASE; CREATE DATABASE`
(see docs/SETUP.md "Reset the database").
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from . import crypto
from . import db_models as _db_models  # noqa: F401  (registers tables on SQLModel.metadata)

logger = logging.getLogger(__name__)


_REQUIRED_DB_VARS = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME")


def _resolve_database_url() -> str:
    """Assemble the MySQL DSN from five required env vars.

    Raises a single `RuntimeError` listing every missing / empty variable
    so contributors see all fixups at once.
    """
    missing = [name for name in _REQUIRED_DB_VARS if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Database credentials are incomplete. Missing env var(s): "
            f"{', '.join(missing)}. "
            "Copy .env.example to .env and fill in the team-shared AWS RDS "
            "credentials (DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME)."
        )
    host = os.environ["DB_HOST"]
    port = os.environ["DB_PORT"]
    user = quote_plus(os.environ["DB_USER"])
    password = quote_plus(os.environ["DB_PASSWORD"])
    name = os.environ["DB_NAME"]
    return (
        f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}"
        "?charset=utf8mb4"
    )


DATABASE_URL = _resolve_database_url()

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,
    echo=False,
)


def describe_database() -> str:
    """Password-free identifier for logs (`user@host:port/name`)."""
    user = os.environ.get("DB_USER", "?")
    host = os.environ.get("DB_HOST", "?")
    port = os.environ.get("DB_PORT", "?")
    name = os.environ.get("DB_NAME", "?")
    return f"{user}@{host}:{port}/{name}"


_USERROW_LANDLORD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("first_name", "TEXT"),
    ("last_name", "TEXT"),
    ("phone", "TEXT"),
    ("occupation", "TEXT"),
    ("bio", "TEXT"),
    ("languages", "JSON"),
)


def _ensure_userrow_landlord_columns() -> None:
    """Idempotently add the optional landlord-intro columns on `userrow`.

    `SQLModel.metadata.create_all` does not alter existing tables, so new
    columns on an already-provisioned MySQL database require hand-coded
    `ALTER TABLE`. Each column is gated on a fresh `information_schema`
    check so this is safe to call on every boot.
    """
    with Session(engine) as session:
        for column, sql_type in _USERROW_LANDLORD_COLUMNS:
            present = session.exec(
                text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() "
                    "AND table_name = 'userrow' "
                    "AND column_name = :c"
                ).bindparams(c=column)
            ).first()
            count = present[0] if present is not None else 0
            if int(count or 0) > 0:
                continue
            logger.info("userrow: adding column %s %s", column, sql_type)
            session.exec(text(f"ALTER TABLE userrow ADD COLUMN {column} {sql_type}"))
        session.commit()


def init_db() -> None:
    """Ensure the Fernet key + schema exist. Safe to call repeatedly."""
    crypto.ensure_key()
    SQLModel.metadata.create_all(engine)
    _ensure_userrow_landlord_columns()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
