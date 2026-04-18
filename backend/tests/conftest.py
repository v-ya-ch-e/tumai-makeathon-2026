"""Pytest setup: isolate test imports from the production MySQL requirement.

Backend `db.py` insists on `WG_DB_URL` at import time so production fails
fast if the env file is missing. Tests don't need MySQL — they build their
own in-memory SQLite engine per test and monkey-patch `db_module.engine`.
Pointing `WG_DB_URL` at `sqlite://` before any test module loads keeps
imports happy without introducing a SQLite code path in production.
"""

from __future__ import annotations

import os
import pathlib
import sys

os.environ.setdefault("WG_DB_URL", "sqlite://")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
