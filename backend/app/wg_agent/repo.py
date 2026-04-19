"""Domain <-> SQLModel row conversions (repository layer).

Post-refactor: listings, scores, and actions belong to users, not hunts.
Scraper owns `ListingRow`+`PhotoRow`; per-user agent owns `UserListingRow`+`UserActionRow`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from pydantic import HttpUrl
from sqlmodel import Session, select

from . import crypto
from .db_models import (
    ListingRow,
    PhotoRow,
    ScraperEventRow,
    SearchProfileRow,
    UserActionRow,
    UserAgentStateRow,
    UserListingRow,
    UserRow,
    WgCredentialsRow,
)
from .models import (
    ActionKind,
    AgentAction,
    ComponentScore,
    Gender,
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


def _user_row_to_profile(row: UserRow) -> UserProfile:
    return UserProfile(
        username=row.username,
        email=row.email,
        age=row.age,
        gender=Gender(row.gender),
        created_at=row.created_at,
        backfill_baseline_at=row.backfill_baseline_at,
        first_name=row.first_name,
        last_name=row.last_name,
        phone=row.phone,
        occupation=row.occupation,
        bio=row.bio,
        landlord_languages=list(row.languages) if row.languages else None,
    )


def create_user(session: Session, *, profile: UserProfile) -> UserProfile:
    # On signup the baseline equals `created_at`, so the initial silent
    # backfill never produces "new" badges or email digests for listings
    # that predate the account — while any listing scraped AFTER signup
    # naturally passes `first_seen_at > baseline_at` and gets surfaced.
    baseline = profile.backfill_baseline_at or profile.created_at
    row = UserRow(
        username=profile.username,
        email=profile.email,
        age=profile.age,
        gender=profile.gender.value,
        created_at=profile.created_at,
        backfill_baseline_at=baseline,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    profile.backfill_baseline_at = baseline
    return profile


def get_user(session: Session, *, username: str) -> Optional[UserProfile]:
    row = session.get(UserRow, username)
    if row is None:
        return None
    return _user_row_to_profile(row)


def get_user_by_email(session: Session, *, email: str) -> Optional[UserProfile]:
    row = session.exec(select(UserRow).where(UserRow.email == email)).first()
    if row is None:
        return None
    return _user_row_to_profile(row)


def update_user(session: Session, *, username: str, profile: UserProfile) -> UserProfile:
    row = session.get(UserRow, username)
    if row is None:
        raise KeyError(username)
    row.email = profile.email
    row.age = profile.age
    row.gender = profile.gender.value
    row.first_name = profile.first_name
    row.last_name = profile.last_name
    row.phone = profile.phone
    row.occupation = profile.occupation
    row.bio = profile.bio
    row.languages = list(profile.landlord_languages) if profile.landlord_languages else None
    session.commit()
    session.refresh(row)
    return _user_row_to_profile(row)


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


def _search_profile_material_snapshot(
    row: SearchProfileRow,
) -> tuple:
    """Normalized tuple of every field that affects listing scoring.

    Changes to `schedule` / `rescan_interval_minutes` / `updated_at` are
    intentionally excluded — they do not require re-scoring existing listings,
    so they must not trigger a wipe + re-backfill.
    """
    return (
        int(row.price_min_eur or 0),
        int(row.price_max_eur) if row.price_max_eur is not None else None,
        tuple(
            (
                str(ml.get("place_id") or ""),
                float(ml.get("lat") or 0.0),
                float(ml.get("lng") or 0.0),
                ml.get("max_commute_minutes"),
            )
            for ml in (row.main_locations or [])
        ),
        bool(row.has_car),
        bool(row.has_bike),
        str(row.mode or "wg"),
        row.move_in_from,
        row.move_in_until,
        tuple(
            (
                str(p.get("key") or "") if isinstance(p, dict) else str(p),
                int(p.get("weight", 3)) if isinstance(p, dict) else 3,
            )
            for p in (row.preferences or [])
        ),
    )


def upsert_search_profile(
    session: Session, *, username: str, sp: SearchProfile
) -> tuple[SearchProfile, bool]:
    """Create or update the user's search profile.

    Returns `(profile, baseline_bumped)`. When a pre-existing row is
    materially changed — anything that affects how a listing would be
    scored — we wipe every `UserListingRow` for this user and bump
    `UserRow.backfill_baseline_at` to `utcnow()`. The caller is expected
    to kick the matcher so the silent re-backfill starts immediately.
    Signup (row was None) never bumps: the baseline is already set to
    `created_at` by `create_user`.
    """
    row = session.get(SearchProfileRow, username)
    bumped = False
    if row is None:
        row = SearchProfileRow(username=username)
        session.add(row)
    else:
        before = _search_profile_material_snapshot(row)
        # Project the incoming SearchProfile through the same snapshot
        # shape so equal inputs produce equal tuples (the persisted row
        # stores Pydantic .model_dump() payloads; we replicate that here).
        after = _search_profile_material_snapshot(
            SearchProfileRow(
                username=username,
                price_min_eur=sp.price_min_eur,
                price_max_eur=sp.price_max_eur,
                main_locations=[ml.model_dump() for ml in sp.main_locations],
                has_car=sp.has_car,
                has_bike=sp.has_bike,
                mode=sp.mode,
                move_in_from=sp.move_in_from,
                move_in_until=sp.move_in_until,
                preferences=[p.model_dump() for p in sp.preferences],
                rescan_interval_minutes=sp.rescan_interval_minutes,
                schedule=sp.schedule,
                updated_at=sp.updated_at,
            )
        )
        if before != after:
            for match_row in session.exec(
                select(UserListingRow).where(UserListingRow.username == username)
            ).all():
                session.delete(match_row)
            user_row = session.get(UserRow, username)
            if user_row is not None:
                user_row.backfill_baseline_at = datetime.utcnow()
                session.add(user_row)
            bumped = True
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
    return (get_search_profile(session, username=username) or sp, bumped)


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


def upsert_global_listing(
    session: Session,
    *,
    listing: Listing,
    status: str = "full",
    scrape_error: Optional[str] = None,
) -> None:
    """Write a listing to the global pool (scraper only).

    Preserves `first_seen_at`, bumps `last_seen_at`, stamps `scraped_at` with
    the current UTC time, and updates `scrape_status` + `scrape_error`.
    """
    now = datetime.utcnow()
    existing = session.get(ListingRow, listing.id)
    first_seen = existing.first_seen_at if existing else now
    row = ListingRow(
        id=listing.id,
        url=str(listing.url),
        title=listing.title,
        price_eur=listing.price_eur,
        size_m2=listing.size_m2,
        wg_size=listing.wg_size,
        city=listing.city,
        district=listing.district,
        address=listing.address,
        lat=listing.lat,
        lng=listing.lng,
        available_from=listing.available_from,
        available_to=listing.available_to,
        description=listing.description,
        furnished=listing.furnished,
        pets_allowed=listing.pets_allowed,
        smoking_ok=listing.smoking_ok,
        languages=list(listing.languages) if listing.languages else None,
        kind=listing.kind,
        scrape_status=status,
        scraped_at=now,
        scrape_error=scrape_error,
        first_seen_at=first_seen,
        last_seen_at=now,
    )
    session.merge(row)
    session.commit()


def save_user_match(
    session: Session,
    *,
    username: str,
    listing_id: str,
    score: float,
    reason: Optional[str],
    match_reasons: list[str],
    mismatch_reasons: list[str],
    travel_minutes: Optional[dict] = None,
    nearby_places: Optional[dict[str, NearbyPlace]] = None,
    components: Optional[list[ComponentScore]] = None,
    veto_reason: Optional[str] = None,
    scored_against_scraped_at: Optional[datetime] = None,
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
    row = UserListingRow(
        username=username,
        listing_id=listing_id,
        score=score,
        reason=reason,
        match_reasons=list(match_reasons),
        mismatch_reasons=list(mismatch_reasons),
        travel_minutes=travel_minutes,
        nearby_places=nearby_places_json,
        components=components_json,
        veto_reason=veto_reason,
        scored_against_scraped_at=scored_against_scraped_at,
        scored_at=now,
    )
    session.merge(row)
    session.commit()


def save_photos(
    session: Session, *, listing_id: str, urls: list[str]
) -> None:
    for p in session.exec(
        select(PhotoRow).where(PhotoRow.listing_id == listing_id)
    ).all():
        session.delete(p)
    for i, u in enumerate(urls):
        session.add(PhotoRow(listing_id=listing_id, ordinal=i, url=u))
    session.commit()


def list_user_listings(session: Session, *, username: str) -> list[Listing]:
    """Return every listing this user has scored.

    Ordered by score (DESC), then `scored_at` (DESC).
    """
    pairs = session.exec(
        select(ListingRow, UserListingRow)
        .join(UserListingRow, UserListingRow.listing_id == ListingRow.id)
        .where(UserListingRow.username == username)
        .order_by(UserListingRow.score.desc(), UserListingRow.scored_at.desc())
    ).all()
    out: list[Listing] = []
    for lr, match_row in pairs:
        out.append(
            _listing_from_row(
                lr,
                match_row,
                cover_photo_url=_cover_photo_url(session, listing_id=lr.id),
            )
        )
    return out


def list_scorable_listings_for_user(
    session: Session,
    *,
    username: str,
    status: str = "full",
    limit: Optional[int] = None,
    mode: Optional[str] = None,
) -> list[ListingRow]:
    """Global listings with the given scrape status that this user has not
    yet scored.

    `mode` (one of `'wg'`, `'flat'`, `'both'`, or `None`) honors the
    user's `SearchProfile.mode` selection: pass `'wg'` to get rooms only,
    `'flat'` to get full apartments only, `'both'` or `None` for both.
    """
    already_scored = session.exec(
        select(UserListingRow.listing_id).where(UserListingRow.username == username)
    ).all()
    scored_ids = {row for row in already_scored}
    stmt = (
        select(ListingRow)
        .where(ListingRow.scrape_status == status)
        .order_by(ListingRow.last_seen_at.desc())
    )
    if mode in ("wg", "flat"):
        stmt = stmt.where(ListingRow.kind == mode)
    rows = session.exec(stmt).all()
    out: list[ListingRow] = []
    for r in rows:
        if r.id in scored_ids:
            continue
        out.append(r)
        if limit is not None and len(out) >= limit:
            break
    return out


def list_stale_listings(
    session: Session, *, older_than: datetime, limit: int
) -> list[ListingRow]:
    """Listings whose `scraped_at` is older than `older_than` (scraper refresh)."""
    stmt = (
        select(ListingRow)
        .where(ListingRow.scraped_at < older_than)
        .order_by(ListingRow.scraped_at)
        .limit(limit)
    )
    return list(session.exec(stmt).all())


def append_user_action(
    session: Session, *, username: str, action: AgentAction
) -> None:
    session.add(
        UserActionRow(
            username=username,
            kind=action.kind.value,
            summary=action.summary,
            detail=action.detail,
            listing_id=action.listing_id,
            at=action.at,
        )
    )
    session.commit()


def list_actions_for_user(
    session: Session, *, username: str, limit: Optional[int] = None
) -> list[AgentAction]:
    stmt = (
        select(UserActionRow)
        .where(UserActionRow.username == username)
        .order_by(UserActionRow.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = session.exec(stmt).all()
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


def list_usernames_with_search_profile(session: Session) -> list[str]:
    rows = session.exec(select(SearchProfileRow.username)).all()
    return [row for row in rows]


def set_user_agent_paused(session: Session, *, username: str, paused: bool) -> None:
    """Persist the user's explicit pause/resume decision.

    Upserts a `UserAgentStateRow`. Called by `POST /agent/pause` (paused=True)
    and `POST /agent/start` (paused=False). A missing row is treated as
    `paused=False` everywhere this flag is read, so calling this only on
    explicit stop/resume keeps the row absent for the common "never touched"
    case.
    """
    row = session.get(UserAgentStateRow, username)
    now = datetime.utcnow()
    if row is None:
        session.add(
            UserAgentStateRow(username=username, paused=paused, updated_at=now)
        )
    else:
        row.paused = paused
        row.updated_at = now
    session.commit()


def is_user_agent_paused(session: Session, *, username: str) -> bool:
    """True iff the user has explicitly stopped their agent (persisted flag)."""
    row = session.get(UserAgentStateRow, username)
    return bool(row and row.paused)


def list_usernames_to_resume_on_boot(session: Session) -> list[str]:
    """Usernames whose matcher should auto-start when the backend boots.

    = users with a search profile, minus users whose persisted
    `UserAgentStateRow.paused` is True. A user who pressed "Stop" stays
    stopped across backend restarts until they press "Resume".
    """
    sp_usernames = session.exec(select(SearchProfileRow.username)).all()
    paused_usernames = set(
        session.exec(
            select(UserAgentStateRow.username).where(UserAgentStateRow.paused == True)  # noqa: E712
        ).all()
    )
    return [u for u in sp_usernames if u not in paused_usernames]


def insert_scraper_event(
    session: Session, *, listing_id: str, kind: str = "new_listing"
) -> None:
    """Append one event to the scraper outbox (scraper only)."""
    session.add(
        ScraperEventRow(
            listing_id=listing_id,
            kind=kind,
            created_at=datetime.utcnow(),
        )
    )
    session.commit()


def list_scraper_events_after(
    session: Session, *, after_id: int, limit: int = 500
) -> list[ScraperEventRow]:
    """Id-ordered tail of the outbox; the watcher calls this with its watermark."""
    stmt = (
        select(ScraperEventRow)
        .where(ScraperEventRow.id > after_id)
        .order_by(ScraperEventRow.id)
        .limit(limit)
    )
    return list(session.exec(stmt).all())


def max_scraper_event_id(session: Session) -> int:
    """Highest outbox id; the watcher uses this as its boot-time watermark."""
    from sqlalchemy import func

    stmt = select(func.max(ScraperEventRow.id))
    value = session.exec(stmt).first()
    if value is None:
        return 0
    return int(value)


def row_to_domain_listing(row: ListingRow) -> Listing:
    """Rehydrate a global `ListingRow` into a domain `Listing` without a score.

    The matcher calls this before evaluating a candidate — the score, match
    reasons, etc. are filled in by the evaluator and persisted separately.
    Structured booleans (`furnished` / `pets_allowed` / `smoking_ok`) and
    `languages` come through because they feed `evaluator.hard_filter`,
    `evaluator.preference_fit`, and the `brain.vibe_score` prompt.
    """
    return Listing(
        id=row.id,
        url=HttpUrl(row.url),
        title=row.title or "",
        kind=_kind_from_row(row),
        city=row.city,
        district=row.district,
        address=row.address,
        lat=row.lat,
        lng=row.lng,
        price_eur=row.price_eur,
        size_m2=row.size_m2,
        wg_size=row.wg_size,
        available_from=row.available_from,
        available_to=row.available_to,
        description=row.description,
        languages=list(row.languages) if row.languages else [],
        furnished=row.furnished,
        pets_allowed=row.pets_allowed,
        smoking_ok=row.smoking_ok,
    )


def _kind_from_row(row: ListingRow) -> str:
    """Coerce `ListingRow.kind` (a free-form `str`) into the domain literal.

    The column defaults to `'wg'` for legacy rows; any unexpected value
    degrades to `'wg'` so `Listing` validation never raises on a stale
    pre-migration row.
    """
    raw = (row.kind or "").strip().lower()
    return raw if raw in ("wg", "flat") else "wg"


def _listing_from_row(
    row: ListingRow,
    match_row: Optional[UserListingRow],
    *,
    cover_photo_url: Optional[str] = None,
) -> Listing:
    score = match_row.score if match_row else None
    reason = match_row.reason if match_row else None
    match_reasons = list(match_row.match_reasons or []) if match_row else []
    mismatch_reasons = list(match_row.mismatch_reasons or []) if match_row else []
    components = _components_from_row(match_row)
    veto_reason = match_row.veto_reason if match_row else None
    title = row.title or ""
    return Listing(
        id=row.id,
        url=HttpUrl(row.url),
        title=title,
        kind=_kind_from_row(row),
        city=row.city,
        district=row.district,
        address=row.address,
        lat=row.lat,
        lng=row.lng,
        price_eur=row.price_eur,
        size_m2=row.size_m2,
        wg_size=row.wg_size,
        available_from=row.available_from,
        available_to=row.available_to,
        description=row.description,
        languages=list(row.languages) if row.languages else [],
        furnished=row.furnished,
        pets_allowed=row.pets_allowed,
        smoking_ok=row.smoking_ok,
        cover_photo_url=cover_photo_url,
        best_commute_minutes=_best_commute_minutes(match_row),
        first_seen_at=row.first_seen_at,
        score=score,
        score_reason=reason,
        match_reasons=match_reasons,
        mismatch_reasons=mismatch_reasons,
        components=components,
        veto_reason=veto_reason,
    )


def _components_from_row(
    match_row: Optional[UserListingRow],
) -> list[ComponentScore]:
    """Rehydrate `components` JSON into domain models, skipping malformed rows.

    Pre-migration score rows (no `components` column populated) return
    []; the UI then falls back to `score_reason` / match lists.
    """
    if match_row is None or not match_row.components:
        return []
    out: list[ComponentScore] = []
    for raw in match_row.components:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(ComponentScore.model_validate(raw))
        except Exception:  # noqa: BLE001
            continue
    return out


def _cover_photo_url(
    session: Session, *, listing_id: str
) -> Optional[str]:
    photo_row = session.exec(
        select(PhotoRow)
        .where(PhotoRow.listing_id == listing_id)
        .order_by(PhotoRow.ordinal)
    ).first()
    return photo_row.url if photo_row is not None else None


def _best_commute_minutes(match_row: Optional[UserListingRow]) -> Optional[int]:
    if match_row is None or not match_row.travel_minutes:
        return None
    best: Optional[int] = None
    for entry in match_row.travel_minutes.values():
        if not isinstance(entry, dict):
            continue
        minutes = entry.get("minutes")
        if not isinstance(minutes, int):
            continue
        if best is None or minutes < best:
            best = minutes
    return best



