"""Domain <-> SQLModel row conversions (repository layer)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import HttpUrl
from sqlmodel import Session, select

from . import crypto
from .db_models import (
    AgentActionRow,
    HuntRow,
    ListingRow,
    ListingScoreRow,
    PhotoRow,
    SearchProfileRow,
    UserRow,
    WgCredentialsRow,
)
from .models import (
    ActionKind,
    AgentAction,
    ComponentScore,
    Gender,
    Hunt,
    HuntStatus,
    Listing,
    NearbyPlace,
    PlaceLocation,
    PreferenceWeight,
    SearchProfile,
    UserProfile,
    WGCredentials,
)


def _default_requirements() -> SearchProfile:
    return SearchProfile(city="München", max_rent_eur=2000)


def create_user(session: Session, *, profile: UserProfile) -> UserProfile:
    row = UserRow(
        username=profile.username,
        age=profile.age,
        gender=profile.gender.value,
        created_at=profile.created_at,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return profile


def get_user(session: Session, *, username: str) -> Optional[UserProfile]:
    row = session.get(UserRow, username)
    if row is None:
        return None
    return UserProfile(
        username=row.username,
        age=row.age,
        gender=Gender(row.gender),
        created_at=row.created_at,
    )


def update_user(session: Session, *, username: str, profile: UserProfile) -> UserProfile:
    row = session.get(UserRow, username)
    if row is None:
        raise KeyError(username)
    row.age = profile.age
    row.gender = profile.gender.value
    session.commit()
    session.refresh(row)
    return UserProfile(
        username=row.username,
        age=row.age,
        gender=Gender(row.gender),
        created_at=row.created_at,
    )


def _parse_preference(raw: object) -> Optional[PreferenceWeight]:
    """Accept both new `{key, weight}` dicts and legacy bare strings."""
    if isinstance(raw, str):
        return PreferenceWeight(key=raw)
    if isinstance(raw, dict):
        try:
            return PreferenceWeight.model_validate(raw)
        except Exception:  # noqa: BLE001
            return None
    return None


def upsert_search_profile(
    session: Session, *, username: str, sp: SearchProfile
) -> SearchProfile:
    row = session.get(SearchProfileRow, username)
    if row is None:
        row = SearchProfileRow(username=username)
        session.add(row)
    row.price_min_eur = sp.price_min_eur
    row.price_max_eur = sp.price_max_eur
    row.main_locations = [ml.model_dump() for ml in sp.main_locations]
    row.has_car = sp.has_car
    row.has_bike = sp.has_bike
    row.mode = sp.mode
    row.move_in_from = sp.move_in_from
    row.move_in_until = sp.move_in_until
    row.preferences = [p.model_dump() for p in sp.preferences]
    row.rescan_interval_minutes = sp.rescan_interval_minutes
    row.schedule = sp.schedule
    row.updated_at = sp.updated_at
    session.commit()
    session.refresh(row)
    return get_search_profile(session, username=username) or sp


def get_search_profile(session: Session, *, username: str) -> Optional[SearchProfile]:
    row = session.get(SearchProfileRow, username)
    if row is None:
        return None
    main = [PlaceLocation.model_validate(d) for d in (row.main_locations or [])]
    prefs_raw = row.preferences or []
    prefs = [p for p in (_parse_preference(x) for x in prefs_raw) if p is not None]
    # Main locations are commute anchors, not the search city itself.
    city = "München"
    max_rent_eur = row.price_max_eur if row.price_max_eur is not None else 2000
    min_rent_eur = row.price_min_eur
    return SearchProfile(
        city=city,
        max_rent_eur=max_rent_eur,
        min_rent_eur=min_rent_eur,
        price_min_eur=row.price_min_eur,
        price_max_eur=row.price_max_eur,
        main_locations=main,
        has_car=row.has_car,
        has_bike=row.has_bike,
        mode=row.mode,  # type: ignore[arg-type]
        preferences=prefs,
        rescan_interval_minutes=row.rescan_interval_minutes,
        schedule=row.schedule,  # type: ignore[arg-type]
        updated_at=row.updated_at,
        move_in_from=row.move_in_from,
        move_in_until=row.move_in_until,
    )


def upsert_credentials(session: Session, *, username: str, creds: WGCredentials) -> None:
    payload = json.dumps(
        {
            "username": creds.username,
            "password": creds.password,
            "storage_state_path": creds.storage_state_path,
        }
    )
    blob = crypto.encrypt(payload)
    row = session.get(WgCredentialsRow, username)
    now = datetime.utcnow()
    if row is None:
        session.add(
            WgCredentialsRow(
                username=username, encrypted_payload=blob, saved_at=now
            )
        )
    else:
        row.encrypted_payload = blob
        row.saved_at = now
    session.commit()


def delete_credentials(session: Session, *, username: str) -> None:
    row = session.get(WgCredentialsRow, username)
    if row is not None:
        session.delete(row)
        session.commit()


def credentials_status(
    session: Session, *, username: str
) -> tuple[bool, Optional[datetime]]:
    row = session.get(WgCredentialsRow, username)
    if row is None:
        return (False, None)
    return (True, row.saved_at)


def create_hunt(session: Session, *, username: str, schedule: str) -> Hunt:
    hunt_id = uuid4().hex[:12]
    now = datetime.utcnow()
    row = HuntRow(
        id=hunt_id,
        username=username,
        status=HuntStatus.pending.value,
        schedule=schedule,
        started_at=now,
        stopped_at=None,
    )
    session.add(row)
    session.commit()
    h = get_hunt(session, hunt_id=hunt_id)
    assert h is not None
    return h


def get_hunt(session: Session, *, hunt_id: str) -> Optional[Hunt]:
    hunt_row = session.get(HuntRow, hunt_id)
    if hunt_row is None:
        return None
    req = get_search_profile(session, username=hunt_row.username)
    if req is None:
        req = _default_requirements()
    listings = list_listings_for_hunt(session, hunt_id=hunt_id)
    actions = list_actions_for_hunt(session, hunt_id=hunt_id)
    return Hunt(
        id=hunt_row.id,
        status=HuntStatus(hunt_row.status),
        started_at=hunt_row.started_at,
        finished_at=hunt_row.stopped_at,
        requirements=req,
        listings=listings,
        messages=[],
        actions=actions,
    )


def update_hunt_status(
    session: Session,
    *,
    hunt_id: str,
    status: HuntStatus,
    stopped_at: Optional[datetime] = None,
) -> None:
    row = session.get(HuntRow, hunt_id)
    if row is None:
        return
    row.status = status.value
    if stopped_at is not None:
        row.stopped_at = stopped_at
    session.add(row)
    session.commit()


def append_action(session: Session, *, hunt_id: str, action: AgentAction) -> None:
    session.add(
        AgentActionRow(
            hunt_id=hunt_id,
            kind=action.kind.value,
            summary=action.summary,
            detail=action.detail,
            listing_id=action.listing_id,
            at=action.at,
        )
    )
    session.commit()


def upsert_listing(session: Session, *, hunt_id: str, listing: Listing) -> None:
    now = datetime.utcnow()
    stmt = select(ListingRow).where(
        ListingRow.id == listing.id, ListingRow.hunt_id == hunt_id
    )
    existing = session.exec(stmt).first()
    first_seen = existing.first_seen_at if existing else now
    row = ListingRow(
        id=listing.id,
        hunt_id=hunt_id,
        url=str(listing.url),
        title=listing.title,
        price_eur=listing.price_eur,
        size_m2=listing.size_m2,
        wg_size=listing.wg_size,
        district=listing.district,
        lat=listing.lat,
        lng=listing.lng,
        available_from=listing.available_from,
        available_to=listing.available_to,
        description=listing.description,
        first_seen_at=first_seen,
        last_seen_at=now,
    )
    session.merge(row)
    session.commit()


def save_score(
    session: Session,
    *,
    hunt_id: str,
    listing_id: str,
    score: float,
    reason: Optional[str],
    match_reasons: list[str],
    mismatch_reasons: list[str],
    travel_minutes: Optional[dict] = None,
    nearby_places: Optional[dict[str, NearbyPlace]] = None,
    components: Optional[list[ComponentScore]] = None,
    veto_reason: Optional[str] = None,
) -> None:
    now = datetime.utcnow()
    nearby_places_json = (
        [place.model_dump(mode="json") for place in nearby_places.values()]
        if nearby_places is not None
        else None
    )
    components_json = (
        [c.model_dump(mode="json") for c in components]
        if components is not None
        else None
    )
    row = ListingScoreRow(
        listing_id=listing_id,
        hunt_id=hunt_id,
        score=score,
        reason=reason,
        match_reasons=list(match_reasons),
        mismatch_reasons=list(mismatch_reasons),
        travel_minutes=travel_minutes,
        nearby_places=nearby_places_json,
        components=components_json,
        veto_reason=veto_reason,
        scored_at=now,
    )
    session.merge(row)
    session.commit()


def save_photos(
    session: Session, *, hunt_id: str, listing_id: str, urls: list[str]
) -> None:
    for p in session.exec(
        select(PhotoRow).where(
            PhotoRow.listing_id == listing_id, PhotoRow.hunt_id == hunt_id
        )
    ).all():
        session.delete(p)
    for i, u in enumerate(urls):
        session.add(
            PhotoRow(listing_id=listing_id, hunt_id=hunt_id, ordinal=i, url=u)
        )
    session.commit()


def list_hunts_by_status(session: Session, *, status: HuntStatus) -> list[Hunt]:
    rows = session.exec(
        select(HuntRow).where(HuntRow.status == status.value)
    ).all()
    out: list[Hunt] = []
    for r in rows:
        h = get_hunt(session, hunt_id=r.id)
        if h is not None:
            out.append(h)
    return out


def list_listings_for_hunt(session: Session, *, hunt_id: str) -> list[Listing]:
    lrows = session.exec(
        select(ListingRow).where(ListingRow.hunt_id == hunt_id)
    ).all()
    out: list[Listing] = []
    for lr in lrows:
        score_row = session.exec(
            select(ListingScoreRow).where(
                ListingScoreRow.listing_id == lr.id,
                ListingScoreRow.hunt_id == hunt_id,
            )
        ).first()
        out.append(
            _listing_from_row(
                lr,
                score_row,
                cover_photo_url=_cover_photo_url(
                    session, hunt_id=hunt_id, listing_id=lr.id
                ),
            )
        )
    return out


def list_actions_for_hunt(session: Session, *, hunt_id: str) -> list[AgentAction]:
    rows = session.exec(
        select(AgentActionRow)
        .where(AgentActionRow.hunt_id == hunt_id)
        .order_by(AgentActionRow.id)
    ).all()
    return [
        AgentAction(
            at=r.at,
            kind=ActionKind(r.kind),
            summary=r.summary,
            detail=r.detail,
            listing_id=r.listing_id,
        )
        for r in rows
    ]


def _listing_from_row(
    row: ListingRow,
    score_row: Optional[ListingScoreRow],
    *,
    cover_photo_url: Optional[str] = None,
) -> Listing:
    score = score_row.score if score_row else None
    reason = score_row.reason if score_row else None
    match_reasons = list(score_row.match_reasons or []) if score_row else []
    mismatch_reasons = list(score_row.mismatch_reasons or []) if score_row else []
    components = _components_from_row(score_row)
    veto_reason = score_row.veto_reason if score_row else None
    title = row.title or ""
    return Listing(
        id=row.id,
        url=HttpUrl(row.url),
        title=title,
        district=row.district,
        lat=row.lat,
        lng=row.lng,
        price_eur=row.price_eur,
        size_m2=row.size_m2,
        wg_size=row.wg_size,
        available_from=row.available_from,
        available_to=row.available_to,
        description=row.description,
        cover_photo_url=cover_photo_url,
        best_commute_minutes=_best_commute_minutes(score_row),
        score=score,
        score_reason=reason,
        match_reasons=match_reasons,
        mismatch_reasons=mismatch_reasons,
        components=components,
        veto_reason=veto_reason,
    )


def _components_from_row(
    score_row: Optional[ListingScoreRow],
) -> list[ComponentScore]:
    """Rehydrate `components` JSON into domain models, skipping malformed rows.

    Pre-migration score rows (no `components` column populated) return
    []; the UI then falls back to `score_reason` / match lists.
    """
    if score_row is None or not score_row.components:
        return []
    out: list[ComponentScore] = []
    for raw in score_row.components:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(ComponentScore.model_validate(raw))
        except Exception:  # noqa: BLE001
            continue
    return out


def _cover_photo_url(
    session: Session, *, hunt_id: str, listing_id: str
) -> Optional[str]:
    photo_row = session.exec(
        select(PhotoRow)
        .where(PhotoRow.listing_id == listing_id, PhotoRow.hunt_id == hunt_id)
        .order_by(PhotoRow.ordinal)
    ).first()
    return photo_row.url if photo_row is not None else None


def _best_commute_minutes(score_row: Optional[ListingScoreRow]) -> Optional[int]:
    if score_row is None or not score_row.travel_minutes:
        return None
    best: Optional[int] = None
    for entry in score_row.travel_minutes.values():
        if not isinstance(entry, dict):
            continue
        minutes = entry.get("minutes")
        if not isinstance(minutes, int):
            continue
        if best is None or minutes < best:
            best = minutes
    return best
