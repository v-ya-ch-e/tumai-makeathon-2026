"""Pytest setup: isolate test imports from the production MySQL requirement.

`backend/app/wg_agent/db.py` assembles its DSN from five required env vars
(`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`) and creates
the production engine at import time, so tests need those set before any
test module imports `db`. We set inert placeholder values here; tests
never touch the real MySQL because they build their own in-memory SQLite
engine per test and monkey-patch `db_module.engine`.
"""

from __future__ import annotations

import os
import pathlib
import sys

os.environ.setdefault("DB_HOST", "tests-do-not-connect")
os.environ.setdefault("DB_PORT", "3306")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
