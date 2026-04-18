"""Alembic environment."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

config = context.config

if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except KeyError:
        pass


def _default_sqlite_url() -> str:
    p = Path.home() / ".wg_hunter" / "app.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{p.resolve()}"


def get_url() -> str:
    return os.environ.get("WG_DB_URL") or _default_sqlite_url()


config.set_main_option("sqlalchemy.url", get_url())

from sqlmodel import SQLModel  # noqa: E402

from app.wg_agent import db_models  # noqa: E402, F401

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    ini_section = config.get_section(config.config_ini_section) or {}
    ini_section["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        ini_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
