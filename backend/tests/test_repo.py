"""Repository round-trip tests (in-memory SQLite)."""

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

from app.wg_agent import crypto, db_models, repo  # noqa: E402
from app.wg_agent.db_models import WgCredentialsRow  # noqa: E402
from app.wg_agent.models import (  # noqa: E402
    ActionKind,
    AgentAction,
    Gender,
    HuntStatus,
    Listing,
    PlaceLocation,
    PreferenceWeight,
    SearchProfile,
    UserProfile,
    WGCredentials,
)


def test_repo_round_trip() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        u = UserProfile(username="lea", age=23, gender=Gender.female)
        assert repo.create_user(session, profile=u) == u
        assert repo.get_user(session, username="lea") == u

        sendling = PlaceLocation(
            label="Sendling, München",
            place_id="ChIJsendling",
            lat=48.116,
            lng=11.548,
            max_commute_minutes=25,
        )
        laim = PlaceLocation(
            label="Laim, München", place_id="ChIJlaim", lat=48.143, lng=11.503
        )
        sp = SearchProfile(
            city="München",
            max_rent_eur=900,
            price_min_eur=400,
            price_max_eur=950,
            main_locations=[sendling, laim],
            has_car=True,
            has_bike=False,
            mode="flat",
            preferences=[
                PreferenceWeight(key="park", weight=5),
                PreferenceWeight(key="gym", weight=2),
            ],
            rescan_interval_minutes=60,
            schedule="periodic",
            updated_at=datetime(2024, 1, 2, 3, 4, 5),
        )
        out = repo.upsert_search_profile(session, username="lea", sp=sp)
        assert out.price_min_eur == 400
        assert out.price_max_eur == 950
        assert out.main_locations == [sendling, laim]
        assert out.main_locations[0].place_id == "ChIJsendling"
        assert out.main_locations[0].lat == 48.116
        assert out.main_locations[0].max_commute_minutes == 25
        assert out.main_locations[1].lng == 11.503
        assert out.main_locations[1].max_commute_minutes is None
        assert out.city == "München"
        assert out.has_car is True
        assert out.has_bike is False
        assert out.mode == "flat"
        assert out.preferences == [
            PreferenceWeight(key="park", weight=5),
            PreferenceWeight(key="gym", weight=2),
        ]
        assert out.rescan_interval_minutes == 60
        assert out.schedule == "periodic"

        creds = WGCredentials(
            username="x@example.com",
            password="s3cr3t!",
            storage_state_path="/tmp/foo.json",
        )
        repo.upsert_credentials(session, username="lea", creds=creds)
        connected, saved_at = repo.credentials_status(session, username="lea")
        assert connected is True
        assert saved_at is not None
        row = session.get(WgCredentialsRow, "lea")
        assert row is not None
        assert b"s3cr3t!" not in row.encrypted_payload
        plain = crypto.decrypt(row.encrypted_payload)
        assert "s3cr3t!" in plain

        hunt = repo.create_hunt(session, username="lea", schedule="one_shot")
        for kind in (ActionKind.boot, ActionKind.search, ActionKind.evaluate):
            repo.append_action(
                session,
                hunt_id=hunt.id,
                action=AgentAction(kind=kind, summary=f"step-{kind.value}"),
            )
        l1 = Listing(
            id="wg1",
            url=HttpUrl("https://www.wg-gesucht.de/wg1"),
            title="Room A",
            price_eur=500,
            lat=48.137,
            lng=11.575,
        )
        l2 = Listing(
            id="wg2",
            url=HttpUrl("https://www.wg-gesucht.de/wg2"),
            title="Room B",
            price_eur=600,
        )
        # Global listing pool (scraper-owned) + photos are keyed by listing id only.
        repo.upsert_global_listing(session, listing=l1, status="full")
        repo.upsert_global_listing(session, listing=l2, status="full")
        repo.save_photos(
            session,
            listing_id="wg1",
            urls=[
                "https://img.wg-gesucht.de/photos/wg1-cover.jpg",
                "https://img.wg-gesucht.de/photos/wg1-detail.jpg",
            ],
        )
        repo.save_score(
            session,
            hunt_id=hunt.id,
            listing_id="wg1",
            score=0.91,
            reason="ok",
            match_reasons=["a"],
            mismatch_reasons=["b"],
            travel_minutes={"p1": {"mode": "TRANSIT", "minutes": 22}},
        )
        repo.save_score(
            session,
            hunt_id=hunt.id,
            listing_id="wg2",
            score=0.42,
            reason="meh",
            match_reasons=[],
            mismatch_reasons=["x"],
        )

        full = repo.get_hunt(session, hunt_id=hunt.id)
        assert full is not None
        assert len(full.listings) == 2
        assert len(full.actions) == 3
        by_id = {x.id: x for x in full.listings}
        assert by_id["wg1"].score == 0.91
        assert by_id["wg1"].score_reason == "ok"
        assert by_id["wg1"].lat == 48.137
        assert by_id["wg1"].lng == 11.575
        assert by_id["wg1"].cover_photo_url == "https://img.wg-gesucht.de/photos/wg1-cover.jpg"
        assert by_id["wg1"].best_commute_minutes == 22
        assert by_id["wg2"].score == 0.42
        assert by_id["wg2"].lat is None
        assert by_id["wg2"].lng is None
        assert by_id["wg2"].cover_photo_url is None
        assert by_id["wg2"].best_commute_minutes is None

        assert repo.list_hunts_by_status(session, status=HuntStatus.running) == []

        repo.update_hunt_status(session, hunt_id=hunt.id, status=HuntStatus.running)
        running = repo.list_hunts_by_status(session, status=HuntStatus.running)
        assert len(running) == 1
        assert running[0].id == hunt.id

        repo.delete_credentials(session, username="lea")
        assert repo.credentials_status(session, username="lea") == (False, None)


def test_list_listings_for_hunt_joins_through_score_row() -> None:
    """Membership moved from ListingRow.hunt_id to ListingScoreRow (ADR-018).

    A listing appears in a hunt's matched set only when a ListingScoreRow
    exists for (listing_id, hunt_id), regardless of how many other hunts
    have also scored the same global listing.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        repo.create_user(
            session,
            profile=UserProfile(username="alice", age=22, gender=Gender.female),
        )
        repo.create_user(
            session,
            profile=UserProfile(username="bob", age=24, gender=Gender.male),
        )
        hunt_a = repo.create_hunt(session, username="alice", schedule="one_shot")
        hunt_b = repo.create_hunt(session, username="bob", schedule="one_shot")

        # Scraper populates the global pool — no hunt_id involved.
        l1 = Listing(id="lx", url=HttpUrl("https://www.wg-gesucht.de/lx"), title="X")
        l2 = Listing(id="ly", url=HttpUrl("https://www.wg-gesucht.de/ly"), title="Y")
        repo.upsert_global_listing(session, listing=l1, status="full")
        repo.upsert_global_listing(session, listing=l2, status="full")

        # Only hunt A has scored lx; hunt B has not scored anything.
        repo.save_score(
            session,
            hunt_id=hunt_a.id,
            listing_id="lx",
            score=0.7,
            reason="ok",
            match_reasons=[],
            mismatch_reasons=[],
        )

        a_listings = repo.list_listings_for_hunt(session, hunt_id=hunt_a.id)
        b_listings = repo.list_listings_for_hunt(session, hunt_id=hunt_b.id)

    assert [l.id for l in a_listings] == ["lx"]
    assert b_listings == []


def test_list_scorable_listings_excludes_already_scored() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        repo.create_user(
            session,
            profile=UserProfile(username="u", age=22, gender=Gender.female),
        )
        hunt_a = repo.create_hunt(session, username="u", schedule="one_shot")
        hunt_b = repo.create_hunt(session, username="u", schedule="one_shot")

        for lid in ("g1", "g2"):
            repo.upsert_global_listing(
                session,
                listing=Listing(
                    id=lid,
                    url=HttpUrl(f"https://www.wg-gesucht.de/{lid}"),
                    title=f"Room {lid}",
                ),
                status="full",
            )
        # Stub listing must not be returned.
        repo.upsert_global_listing(
            session,
            listing=Listing(
                id="stub1",
                url=HttpUrl("https://www.wg-gesucht.de/stub1"),
                title="Partial",
            ),
            status="stub",
        )

        repo.save_score(
            session,
            hunt_id=hunt_a.id,
            listing_id="g1",
            score=0.5,
            reason="ok",
            match_reasons=[],
            mismatch_reasons=[],
        )

        a_candidates = {
            r.id
            for r in repo.list_scorable_listings(session, hunt_id=hunt_a.id)
        }
        b_candidates = {
            r.id
            for r in repo.list_scorable_listings(session, hunt_id=hunt_b.id)
        }

    assert a_candidates == {"g2"}
    assert b_candidates == {"g1", "g2"}


def test_repo_tolerates_legacy_preference_strings() -> None:
    """Legacy rows (pre-0005 dev DBs) could store bare strings in preferences.
    `repo.get_search_profile` must parse those as weight-3 PreferenceWeights
    instead of raising, so dev databases don't need manual migration."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        repo.create_user(
            session,
            profile=UserProfile(username="legacy", age=25, gender=Gender.diverse),
        )
        row = db_models.SearchProfileRow(username="legacy")
        row.preferences = ["park", {"key": "gym", "weight": 4}, 123, {"bad": True}]
        session.add(row)
        session.commit()

        out = repo.get_search_profile(session, username="legacy")
        assert out is not None
        assert out.preferences == [
            PreferenceWeight(key="park", weight=3),
            PreferenceWeight(key="gym", weight=4),
        ]
