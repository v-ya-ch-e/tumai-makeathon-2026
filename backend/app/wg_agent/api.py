"""FastAPI router: v1 JSON + SSE endpoints for the WG hunter agent."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

import os

from . import notifier, periodic, repo
from .db import engine, get_session
from .db_models import ListingRow, PhotoRow, UserListingRow
from .dto import (
    ActionDTO,
    ComponentDTO,
    CreateUserBody,
    CredentialsBody,
    CredentialsStatusDTO,
    ListingDetailDTO,
    ListingDTO,
    NearbyPlaceDTO,
    SearchProfileDTO,
    UpdateUserBody,
    UpsertSearchProfileBody,
    UserDTO,
    action_to_dto,
    listing_to_dto,
    search_profile_to_dto,
    upsert_body_to_search_profile,
    user_to_dto,
)
from .models import (
    AgentAction,
    Gender,
    UserProfile,
    WGCredentials,
)

router = APIRouter(prefix="/api", tags=["wg-hunter"])


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


# --- Users ------------------------------------------------------------------


@router.post("/users", status_code=201, response_model=UserDTO)
def create_user(
    body: CreateUserBody,
    session: Session = Depends(get_session),
) -> UserDTO:
    if repo.get_user(session, username=body.username) is not None:
        raise HTTPException(status_code=409, detail="Username already taken")
    if body.email is not None:
        existing_by_email = repo.get_user_by_email(session, email=str(body.email))
        if existing_by_email is not None:
            raise HTTPException(status_code=409, detail="Email already in use")
    profile = UserProfile(
        username=body.username,
        age=body.age,
        gender=Gender(body.gender),
        email=body.email,
    )
    repo.create_user(session, profile=profile)
    return user_to_dto(profile)


@router.get("/users/{username}", response_model=UserDTO)
def get_user(username: str, session: Session = Depends(get_session)) -> UserDTO:
    u = repo.get_user(session, username=username)
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user_to_dto(u)


@router.put("/users/{username}", response_model=UserDTO)
def update_user(
    username: str,
    body: UpdateUserBody,
    session: Session = Depends(get_session),
) -> UserDTO:
    existing = repo.get_user(session, username=username)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")
    if body.email is not None:
        conflict = repo.get_user_by_email(session, email=str(body.email))
        if conflict is not None and conflict.username != username:
            raise HTTPException(status_code=409, detail="Email already in use")
    updated = repo.update_user(
        session,
        username=username,
        profile=UserProfile(
            username=existing.username,
            email=body.email,
            age=body.age,
            gender=Gender(body.gender),
            created_at=existing.created_at,
        ),
    )
    return user_to_dto(updated)


@router.put("/users/{username}/search-profile", response_model=SearchProfileDTO)
async def put_search_profile(
    username: str,
    body: UpsertSearchProfileBody,
    session: Session = Depends(get_session),
) -> SearchProfileDTO:
    # async so we can call `asyncio.create_task` inside `spawn_user_agent`.
    if repo.get_user(session, username=username) is None:
        raise HTTPException(status_code=404, detail="User not found")
    sp = upsert_body_to_search_profile(body)
    out = repo.upsert_search_profile(session, username=username, sp=sp)
    # Boot (or refresh) the per-user agent. spawn_user_agent is idempotent.
    periodic.spawn_user_agent(
        username, interval_minutes=body.rescan_interval_minutes
    )
    return search_profile_to_dto(out)


@router.get("/users/{username}/search-profile", response_model=SearchProfileDTO)
def get_search_profile_endpoint(
    username: str, session: Session = Depends(get_session)
) -> SearchProfileDTO:
    if repo.get_user(session, username=username) is None:
        raise HTTPException(status_code=404, detail="User not found")
    sp = repo.get_search_profile(session, username=username)
    if sp is None:
        raise HTTPException(status_code=404, detail="Search profile not found")
    return search_profile_to_dto(sp)


@router.put("/users/{username}/credentials", status_code=204)
def put_credentials(
    username: str,
    body: CredentialsBody,
    session: Session = Depends(get_session),
) -> Response:
    if repo.get_user(session, username=username) is None:
        raise HTTPException(status_code=404, detail="User not found")
    if body.storage_state is not None:
        creds = WGCredentials(
            username="__storage_state__",
            password=json.dumps(body.storage_state),
            storage_state_path=None,
        )
    else:
        creds = WGCredentials(
            username=str(body.email),
            password=body.password or "",
            storage_state_path=None,
        )
    repo.upsert_credentials(session, username=username, creds=creds)
    return Response(status_code=204)


@router.delete("/users/{username}/credentials", status_code=204)
def delete_credentials_endpoint(
    username: str, session: Session = Depends(get_session)
) -> Response:
    if repo.get_user(session, username=username) is None:
        raise HTTPException(status_code=404, detail="User not found")
    repo.delete_credentials(session, username=username)
    return Response(status_code=204)


@router.get("/users/{username}/credentials", response_model=CredentialsStatusDTO)
def get_credentials_status(
    username: str, session: Session = Depends(get_session)
) -> CredentialsStatusDTO:
    if repo.get_user(session, username=username) is None:
        raise HTTPException(status_code=404, detail="User not found")
    connected, saved_at = repo.credentials_status(session, username=username)
    return CredentialsStatusDTO(connected=connected, saved_at=saved_at)


# --- Agent control ---------------------------------------------------------


@router.post("/users/{username}/agent/start", status_code=204)
async def start_agent(
    username: str, session: Session = Depends(get_session)
) -> Response:
    # async so `spawn_user_agent` can schedule the task on the running loop.
    if repo.get_user(session, username=username) is None:
        raise HTTPException(status_code=404, detail="User not found")
    sp = repo.get_search_profile(session, username=username)
    if sp is None:
        raise HTTPException(status_code=400, detail="User has no search profile yet")
    periodic.spawn_user_agent(
        username, interval_minutes=sp.rescan_interval_minutes
    )
    return Response(status_code=204)


@router.post("/users/{username}/agent/pause", status_code=204)
def pause_agent(
    username: str, session: Session = Depends(get_session)
) -> Response:
    if repo.get_user(session, username=username) is None:
        raise HTTPException(status_code=404, detail="User not found")
    periodic.cancel_user_agent(username)
    return Response(status_code=204)


@router.get("/users/{username}/agent")
def get_agent_status(
    username: str, session: Session = Depends(get_session)
) -> dict:
    if repo.get_user(session, username=username) is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"running": periodic.is_agent_running(username)}


# --- Listings, actions, stream --------------------------------------------


@router.get("/users/{username}/listings", response_model=list[ListingDTO])
def list_user_listings_endpoint(
    username: str, session: Session = Depends(get_session)
) -> list[ListingDTO]:
    if repo.get_user(session, username=username) is None:
        raise HTTPException(status_code=404, detail="User not found")
    listings = repo.list_user_listings(session, username=username)
    return [listing_to_dto(l, username=username) for l in listings]


@router.get("/users/{username}/actions", response_model=list[ActionDTO])
def list_user_actions_endpoint(
    username: str,
    limit: Optional[int] = Query(default=None, ge=1, le=10_000),
    session: Session = Depends(get_session),
) -> list[ActionDTO]:
    if repo.get_user(session, username=username) is None:
        raise HTTPException(status_code=404, detail="User not found")
    actions = repo.list_actions_for_user(session, username=username, limit=limit)
    return [action_to_dto(a) for a in actions]


@router.get("/users/{username}/stream")
async def stream_user_events(username: str) -> StreamingResponse:
    # Do not use Depends(get_session) here: SSE responses are long-lived, and
    # keeping the injected session open for the whole stream would leak a DB
    # connection per subscriber and quickly exhaust the SQLAlchemy pool
    # (QueuePool limit ... reached). Use a short-lived session only for the
    # existence check, and let event_source open its own scoped sessions.
    with Session(engine) as s:
        if repo.get_user(s, username=username) is None:
            raise HTTPException(status_code=404, detail="User not found")

    async def event_source():
        seen: set[tuple[datetime, str, str]] = set()
        with Session(engine) as s:
            initial = repo.list_actions_for_user(s, username=username)
        for a in initial:
            seen.add((a.at, a.kind.value, a.summary))
            yield _sse(action_to_dto(a).model_dump(mode="json"))

        while True:
            action: AgentAction | None = None
            queue = periodic.event_queue_for(username)
            if queue is not None:
                try:
                    action = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(1.0)

            if action is not None:
                key = (action.at, action.kind.value, action.summary)
                if key not in seen:
                    seen.add(key)
                    yield _sse(action_to_dto(action).model_dump(mode="json"))

            with Session(engine) as s:
                fresh = repo.list_actions_for_user(s, username=username)
            for a in fresh:
                key = (a.at, a.kind.value, a.summary)
                if key not in seen:
                    seen.add(key)
                    yield _sse(action_to_dto(a).model_dump(mode="json"))
            yield ": keep-alive\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


# --- Listing detail --------------------------------------------------------


@router.get("/listings/{listing_id}", response_model=ListingDetailDTO)
def get_listing_detail(
    listing_id: str,
    username: str = Query(..., description="User scope for the listing match"),
    session: Session = Depends(get_session),
) -> ListingDetailDTO:
    detail = _get_listing_detail(session, listing_id=listing_id, username=username)
    if detail is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return detail


# --- Debug / smoke tests ---------------------------------------------------


def _email_debug_enabled() -> bool:
    raw = os.environ.get("ENABLE_EMAIL_DEBUG", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@router.get("/debug/send-test-email")
def debug_send_test_email(
    to: str = Query(..., description="Destination email address"),
) -> dict:
    """Fire a single SES test email. Disabled unless ENABLE_EMAIL_DEBUG is set.

    Useful for verifying that SES credentials + verified identity + sandbox
    status are all correctly configured without waiting for a real 0.9+ match.
    """
    if not _email_debug_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    notifier.send_test_email(to)
    return {"status": "dispatched", "to": to}


# --- Helpers ---------------------------------------------------------------


def _get_listing_detail(
    session: Session, *, listing_id: str, username: str
) -> Optional[ListingDetailDTO]:
    row = session.get(ListingRow, listing_id)
    if row is None:
        return None
    match_row = session.get(UserListingRow, (username, listing_id))
    if match_row is None:
        return None
    photo_rows = session.exec(
        select(PhotoRow)
        .where(PhotoRow.listing_id == listing_id)
        .order_by(PhotoRow.ordinal)
    ).all()
    photos = [p.url for p in photo_rows]
    score_val = match_row.score
    reason = match_row.reason
    match_reasons = list(match_row.match_reasons or [])
    mismatch_reasons = list(match_row.mismatch_reasons or [])
    components_dto = _components_dto_from_row(match_row)
    veto_reason = match_row.veto_reason
    listing_dto = ListingDTO(
        id=row.id,
        username=username,
        url=row.url,
        title=row.title,
        district=row.district,
        lat=row.lat,
        lng=row.lng,
        price_eur=row.price_eur,
        size_m2=row.size_m2,
        wg_size=row.wg_size,
        available_from=row.available_from,
        available_to=row.available_to,
        description=row.description,
        cover_photo_url=photos[0] if photos else None,
        best_commute_minutes=_best_commute_minutes(match_row),
        score=score_val,
        score_reason=reason,
        match_reasons=match_reasons,
        mismatch_reasons=mismatch_reasons,
        components=components_dto,
        veto_reason=veto_reason,
    )
    travel_minutes_per_location = _travel_minutes_by_label(
        session, username=username, match_row=match_row
    )
    nearby_preference_places = _nearby_places_from_row(match_row)
    return ListingDetailDTO(
        listing=listing_dto,
        photos=photos,
        score=score_val,
        travel_minutes_per_location=travel_minutes_per_location,
        nearby_preference_places=nearby_preference_places,
    )


def _components_dto_from_row(
    match_row: Optional[UserListingRow],
) -> list[ComponentDTO]:
    """Rehydrate `components` JSON into DTOs for the listing drawer.

    Pre-migration rows (no `components`) return []; the drawer then
    falls back to the `score_reason` block.
    """
    if match_row is None or not match_row.components:
        return []
    out: list[ComponentDTO] = []
    for raw in match_row.components:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(ComponentDTO.model_validate(raw))
        except Exception:  # noqa: BLE001
            continue
    return out


def _travel_minutes_by_label(
    session: Session,
    *,
    username: str,
    match_row: Optional[UserListingRow],
) -> Optional[dict[str, dict[str, str | int]]]:
    """Convert the persisted `{place_id: {mode, minutes}}` blob into
    `{label: {mode, minutes}}` by looking up each place_id in the user's
    SearchProfile.main_locations."""
    if match_row is None or not match_row.travel_minutes:
        return None
    sp = repo.get_search_profile(session, username=username)
    if sp is None or not sp.main_locations:
        return None
    label_by_pid = {loc.place_id: loc.label for loc in sp.main_locations}
    out: dict[str, dict[str, str | int]] = {}
    for place_id, entry in match_row.travel_minutes.items():
        if not isinstance(entry, dict):
            continue
        minutes = entry.get("minutes")
        mode = entry.get("mode")
        if not isinstance(minutes, int) or not isinstance(mode, str):
            continue
        label = label_by_pid.get(place_id)
        if label:
            out[label] = {"minutes": minutes, "mode": mode}
    return out or None


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


def _nearby_places_from_row(
    match_row: Optional[UserListingRow],
) -> list[NearbyPlaceDTO]:
    if match_row is None or not match_row.nearby_places:
        return []
    out: list[NearbyPlaceDTO] = []
    for raw in match_row.nearby_places:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(NearbyPlaceDTO.model_validate(raw))
        except Exception:  # noqa: BLE001
            continue
    return out
