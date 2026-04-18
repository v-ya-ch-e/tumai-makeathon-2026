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
            label="Sendling, München", place_id="ChIJsendling", lat=48.116, lng=11.548
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
            preferences=["park", "gym"],
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
        assert out.main_locations[1].lng == 11.503
        assert out.has_car is True
        assert out.has_bike is False
        assert out.mode == "flat"
        assert out.preferences == ["park", "gym"]
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
        )
        l2 = Listing(
            id="wg2",
            url=HttpUrl("https://www.wg-gesucht.de/wg2"),
            title="Room B",
            price_eur=600,
        )
        repo.upsert_listing(session, hunt_id=hunt.id, listing=l1)
        repo.upsert_listing(session, hunt_id=hunt.id, listing=l2)
        repo.save_score(
            session,
            hunt_id=hunt.id,
            listing_id="wg1",
            score=0.91,
            reason="ok",
            match_reasons=["a"],
            mismatch_reasons=["b"],
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
        assert by_id["wg2"].score == 0.42

        assert repo.list_hunts_by_status(session, status=HuntStatus.running) == []

        repo.update_hunt_status(session, hunt_id=hunt.id, status=HuntStatus.running)
        running = repo.list_hunts_by_status(session, status=HuntStatus.running)
        assert len(running) == 1
        assert running[0].id == hunt.id

        repo.delete_credentials(session, username="lea")
        assert repo.credentials_status(session, username="lea") == (False, None)
