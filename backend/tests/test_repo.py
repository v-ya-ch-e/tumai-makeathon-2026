"""Repository round-trip tests (in-memory SQLite).

Post-refactor: listings, scores, and actions are per-user (no hunt concept).
Scraper writes `ListingRow`+`PhotoRow`; the per-user matcher writes
`UserListingRow`+`UserActionRow`.
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

from app.wg_agent import crypto, db_models, repo  # noqa: E402
from app.wg_agent.db_models import WgCredentialsRow  # noqa: E402
from app.wg_agent.models import (  # noqa: E402
    ActionKind,
    AgentAction,
    Gender,
    Listing,
    PlaceLocation,
    PreferenceWeight,
    SearchProfile,
    UserProfile,
    WGCredentials,
)


def _make_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_repo_round_trip() -> None:
    engine = _make_engine()

    with Session(engine) as session:
        u = UserProfile(
            username="lea", email="lea@example.com", age=23, gender=Gender.female
        )
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
        out, baseline_bumped = repo.upsert_search_profile(
            session, username="lea", sp=sp
        )
        assert baseline_bumped is False
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
        # Scraper-owned global pool + photos are keyed by listing id only.
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
        repo.save_user_match(
            session,
            username="lea",
            listing_id="wg1",
            score=0.91,
            reason="ok",
            match_reasons=["a"],
            mismatch_reasons=["b"],
            travel_minutes={"p1": {"mode": "TRANSIT", "minutes": 22}},
        )
        repo.save_user_match(
            session,
            username="lea",
            listing_id="wg2",
            score=0.42,
            reason="meh",
            match_reasons=[],
            mismatch_reasons=["x"],
        )

        for kind in (ActionKind.boot, ActionKind.search, ActionKind.evaluate):
            repo.append_user_action(
                session,
                username="lea",
                action=AgentAction(kind=kind, summary=f"step-{kind.value}"),
            )

        listings = repo.list_user_listings(session, username="lea")
        assert [l.id for l in listings] == ["wg1", "wg2"]  # sorted by score desc
        by_id = {x.id: x for x in listings}
        assert by_id["wg1"].score == 0.91
        assert by_id["wg1"].score_reason == "ok"
        assert by_id["wg1"].lat == 48.137
        assert by_id["wg1"].lng == 11.575
        assert (
            by_id["wg1"].cover_photo_url
            == "https://img.wg-gesucht.de/photos/wg1-cover.jpg"
        )
        assert by_id["wg1"].best_commute_minutes == 22
        assert by_id["wg2"].score == 0.42
        assert by_id["wg2"].lat is None
        assert by_id["wg2"].lng is None
        assert by_id["wg2"].cover_photo_url is None
        assert by_id["wg2"].best_commute_minutes is None

        actions = repo.list_actions_for_user(session, username="lea")
        assert [a.kind for a in actions] == [
            ActionKind.boot,
            ActionKind.search,
            ActionKind.evaluate,
        ]
        assert [a.summary for a in actions] == [
            "step-boot",
            "step-search",
            "step-evaluate",
        ]

        repo.delete_credentials(session, username="lea")
        assert repo.credentials_status(session, username="lea") == (False, None)


def test_list_scorable_listings_for_user_excludes_already_scored() -> None:
    """The matcher must not re-score the same listing twice for the same user,
    and stub listings must never be returned (deep-scrape gate)."""
    engine = _make_engine()
    with Session(engine) as session:
        repo.create_user(
            session, profile=UserProfile(username="a", age=22, gender=Gender.female)
        )
        repo.create_user(
            session, profile=UserProfile(username="b", age=23, gender=Gender.male)
        )

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
        # Stub listings must never be returned by the matcher.
        repo.upsert_global_listing(
            session,
            listing=Listing(
                id="stub1",
                url=HttpUrl("https://www.wg-gesucht.de/stub1"),
                title="Partial",
            ),
            status="stub",
        )

        # User `a` has already scored `g1`; user `b` has scored nothing.
        repo.save_user_match(
            session,
            username="a",
            listing_id="g1",
            score=0.5,
            reason="ok",
            match_reasons=[],
            mismatch_reasons=[],
        )

        a_candidates = {
            r.id
            for r in repo.list_scorable_listings_for_user(session, username="a")
        }
        b_candidates = {
            r.id
            for r in repo.list_scorable_listings_for_user(session, username="b")
        }

    assert a_candidates == {"g2"}
    assert b_candidates == {"g1", "g2"}


def test_list_scorable_listings_filters_by_mode() -> None:
    """G3: a user with mode='flat' only sees kind='flat' candidates; mode='wg'
    only sees kind='wg'; mode='both' (or None) sees everything."""
    engine = _make_engine()
    with Session(engine) as session:
        repo.create_user(
            session, profile=UserProfile(username="u", age=22, gender=Gender.female)
        )

        for lid, kind in (
            ("wg-gesucht:wg-1", "wg"),
            ("wg-gesucht:wg-2", "wg"),
            ("kleinanzeigen:flat-1", "flat"),
            ("tum-living:flat-2", "flat"),
        ):
            repo.upsert_global_listing(
                session,
                listing=Listing(
                    id=lid,
                    url=HttpUrl(f"https://example.com/{lid}"),
                    title=f"Listing {lid}",
                    kind=kind,  # type: ignore[arg-type]
                ),
                status="full",
            )

        wg_only = {
            r.id
            for r in repo.list_scorable_listings_for_user(
                session, username="u", mode="wg"
            )
        }
        flat_only = {
            r.id
            for r in repo.list_scorable_listings_for_user(
                session, username="u", mode="flat"
            )
        }
        both = {
            r.id
            for r in repo.list_scorable_listings_for_user(
                session, username="u", mode="both"
            )
        }
        unfiltered = {
            r.id
            for r in repo.list_scorable_listings_for_user(session, username="u")
        }

    assert wg_only == {"wg-gesucht:wg-1", "wg-gesucht:wg-2"}
    assert flat_only == {"kleinanzeigen:flat-1", "tum-living:flat-2"}
    assert both == {"wg-gesucht:wg-1", "wg-gesucht:wg-2", "kleinanzeigen:flat-1", "tum-living:flat-2"}
    assert unfiltered == both


def test_repo_tolerates_legacy_preference_strings() -> None:
    """Legacy rows (pre-0005 dev DBs) could store bare strings in preferences.
    `repo.get_search_profile` must parse those as weight-3 PreferenceWeights
    instead of raising, so dev databases don't need manual migration."""
    engine = _make_engine()
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


def test_create_user_persists_email() -> None:
    engine = _make_engine()
    with Session(engine) as session:
        profile = UserProfile(
            username="emailed", email="emailed@x.y", age=22, gender=Gender.female
        )
        repo.create_user(session, profile=profile)

        fetched = repo.get_user(session, username="emailed")
    assert fetched is not None
    assert fetched.email == "emailed@x.y"


def test_get_user_by_email_returns_profile() -> None:
    engine = _make_engine()
    with Session(engine) as session:
        repo.create_user(
            session,
            profile=UserProfile(
                username="with-mail",
                email="with-mail@x.y",
                age=24,
                gender=Gender.male,
            ),
        )
        repo.create_user(
            session,
            profile=UserProfile(
                username="without-mail", age=24, gender=Gender.male
            ),
        )

        hit = repo.get_user_by_email(session, email="with-mail@x.y")
        miss = repo.get_user_by_email(session, email="nobody@x.y")

    assert hit is not None
    assert hit.username == "with-mail"
    assert miss is None
