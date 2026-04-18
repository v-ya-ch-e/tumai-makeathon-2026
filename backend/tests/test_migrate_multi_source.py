"""Smoke tests for the multi-source migration script.

The script targets MySQL (uses `information_schema` + `MODIFY` syntax for
the column-widening ALTERs in step 1), but steps 2 + 3 — namespacing
existing rows and the rescrape-trigger UPDATE — use portable SQL we can
exercise against an in-memory SQLite database.

What's verified here:

- Step 2 namespaces every bare `listingrow.id` and the matching FK
  columns (G1 verification query returns 0 after the run).
- Step 3 flips every `'full'` row to `'stub'`.
- Re-running the script is a no-op when there's nothing left to do
  (idempotent).

What's NOT verified here (requires a real MySQL):

- Step 1's `ALTER TABLE listingrow MODIFY <col> TEXT` (SQLite has no
  `MODIFY` and no information_schema).
- Adding the `kind` column on a pre-migration schema (the SQLModel
  in-memory DB already has the column, since `metadata.create_all`
  reflects today's model).

For the MySQL-specific verification path, run the script against the
shared RDS with `--dry-run` first.
"""

from __future__ import annotations

import os
import pathlib
import sys
from datetime import datetime

from cryptography.fernet import Fernet
from pydantic import HttpUrl
from sqlmodel import Session, SQLModel, create_engine

os.environ.setdefault("WG_SECRET_KEY", Fernet.generate_key().decode())

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.scraper import migrate_multi_source  # noqa: E402
from app.wg_agent import db as db_module, repo  # noqa: E402
from app.wg_agent.db_models import (  # noqa: E402
    ListingRow,
    PhotoRow,
    UserActionRow,
    UserListingRow,
    UserRow,
)
from app.wg_agent.models import (  # noqa: E402
    ActionKind,
    AgentAction,
    Gender,
    Listing,
    UserProfile,
)


def _make_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_pre_migration(engine) -> None:
    """Insert pre-migration rows: bare ids, status='full', plus FK children."""
    with Session(engine) as session:
        repo.create_user(
            session, profile=UserProfile(username="alice", age=22, gender=Gender.female)
        )
        for lid, status in (("12345", "full"), ("67890", "full"), ("partial", "stub")):
            repo.upsert_global_listing(
                session,
                listing=Listing(
                    id=lid,
                    url=HttpUrl(f"https://www.wg-gesucht.de/{lid}.html"),
                    title=f"Listing {lid}",
                    description=f"description for {lid}",
                    lat=48.1,
                    lng=11.5,
                ),
                status=status,
            )
        # Two photos for one listing, one match row, one action row — the
        # FK targets that need their listing_id rewritten.
        repo.save_photos(
            session,
            listing_id="12345",
            urls=[
                "https://img.wg-gesucht.de/12345-cover.jpg",
                "https://img.wg-gesucht.de/12345-second.jpg",
            ],
        )
        repo.save_user_match(
            session,
            username="alice",
            listing_id="12345",
            score=0.7,
            reason="ok",
            match_reasons=[],
            mismatch_reasons=[],
        )
        repo.append_user_action(
            session,
            username="alice",
            action=AgentAction(
                kind=ActionKind.evaluate,
                summary="scored 12345",
                listing_id="12345",
            ),
        )


def test_step_2_namespaces_ids_and_fk_children(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)
    _seed_pre_migration(engine)

    with Session(engine) as session:
        migrate_multi_source.step_2_namespace_ids(session, dry_run=False)
        session.commit()

    with Session(engine) as session:
        listing_ids = sorted(r.id for r in session.exec(__sm_select(ListingRow)).all())
        photo_ids = sorted(r.listing_id for r in session.exec(__sm_select(PhotoRow)).all())
        user_listing_ids = sorted(
            r.listing_id for r in session.exec(__sm_select(UserListingRow)).all()
        )
        action_ids = sorted(
            r.listing_id
            for r in session.exec(__sm_select(UserActionRow)).all()
            if r.listing_id is not None
        )

    assert listing_ids == [
        "wg-gesucht:12345",
        "wg-gesucht:67890",
        "wg-gesucht:partial",
    ]
    assert photo_ids == [
        "wg-gesucht:12345",
        "wg-gesucht:12345",
    ]
    assert user_listing_ids == ["wg-gesucht:12345"]
    assert action_ids == ["wg-gesucht:12345"]


def test_step_2_is_idempotent(monkeypatch) -> None:
    """Re-running the migration on already-namespaced rows is a no-op."""
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)
    _seed_pre_migration(engine)

    with Session(engine) as session:
        migrate_multi_source.step_2_namespace_ids(session, dry_run=False)
        session.commit()

    with Session(engine) as session:
        before = sorted(r.id for r in session.exec(__sm_select(ListingRow)).all())

    # Second invocation: nothing new to do.
    with Session(engine) as session:
        migrate_multi_source.step_2_namespace_ids(session, dry_run=False)
        session.commit()

    with Session(engine) as session:
        after = sorted(r.id for r in session.exec(__sm_select(ListingRow)).all())

    assert before == after  # no double-prefixing


def test_step_3_marks_full_rows_as_stub(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)
    _seed_pre_migration(engine)

    with Session(engine) as session:
        migrate_multi_source.step_3_force_rescrape(session, dry_run=False)
        session.commit()

    with Session(engine) as session:
        rows = list(session.exec(__sm_select(ListingRow)).all())
        full_count = sum(1 for r in rows if r.scrape_status == "full")
        stub_count = sum(1 for r in rows if r.scrape_status == "stub")

    assert full_count == 0  # everything that was 'full' was flipped
    assert stub_count == 3  # 2 originally 'full' + 1 originally 'stub'


def test_dry_run_does_not_mutate(monkeypatch) -> None:
    engine = _make_engine()
    monkeypatch.setattr(db_module, "engine", engine)
    _seed_pre_migration(engine)

    with Session(engine) as session:
        migrate_multi_source.step_2_namespace_ids(session, dry_run=True)
        migrate_multi_source.step_3_force_rescrape(session, dry_run=True)
        session.commit()

    with Session(engine) as session:
        listing_ids = sorted(r.id for r in session.exec(__sm_select(ListingRow)).all())
        full_count = sum(
            1
            for r in session.exec(__sm_select(ListingRow)).all()
            if r.scrape_status == "full"
        )

    # Untouched bare ids and unchanged status counts.
    assert listing_ids == ["12345", "67890", "partial"]
    assert full_count == 2


def __sm_select(model):
    """Tiny helper to keep imports tidy in the assertions above."""
    from sqlmodel import select  # local import to avoid top-level noise

    return select(model)
