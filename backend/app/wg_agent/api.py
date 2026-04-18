"""FastAPI router: v1 JSON + SSE endpoints for the WG hunter agent."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from . import periodic, repo
from .db import engine, get_session
from .db_models import HuntRow, ListingRow, ListingScoreRow, PhotoRow
from .dto import (
    ComponentDTO,
    CreateHuntBody,
    CreateUserBody,
    CredentialsBody,
    CredentialsStatusDTO,
    HuntDTO,
    ListingDetailDTO,
    ListingDTO,
    SearchProfileDTO,
    UpsertSearchProfileBody,
    UserDTO,
    action_to_dto,
    hunt_to_dto,
    search_profile_to_dto,
    upsert_body_to_search_profile,
    user_to_dto,
)
from .models import (
    ActionKind,
    AgentAction,
    ContactInfo,
    Gender,
    HuntStatus,
    SearchProfile,
    WGCredentials,
    UserProfile,
)

router = APIRouter(prefix="/api", tags=["wg-hunter"])


class HuntRequest(BaseModel):
    """Top-level POST body that kicks off a hunt run."""

    requirements: SearchProfile
    credentials: WGCredentials
    profile: ContactInfo
    dry_run: bool = Field(
        default=True,
        description=(
            "If True, the agent searches + scores + drafts messages but never actually "
            "sends anything on wg-gesucht. Great for demos and safe by default."
        ),
    )
    headless: bool = Field(
        default=False,
        description="If False, Playwright browser is visible — best for demos.",
    )


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _get_listing_detail(
    session: Session, *, listing_id: str, hunt_id: str
) -> Optional[ListingDetailDTO]:
    row = session.get(ListingRow, (listing_id, hunt_id))
    if row is None:
        return None
    score_row = session.get(ListingScoreRow, (listing_id, hunt_id))
    photo_rows = session.exec(
        select(PhotoRow)
        .where(PhotoRow.listing_id == listing_id, PhotoRow.hunt_id == hunt_id)
        .order_by(PhotoRow.ordinal)
    ).all()
    photos = [p.url for p in photo_rows]
    score_val = score_row.score if score_row else None
    reason = score_row.reason if score_row else None
    match_reasons = list(score_row.match_reasons or []) if score_row else []
    mismatch_reasons = list(score_row.mismatch_reasons or []) if score_row else []
    components_dto = _components_dto_from_row(score_row)
    veto_reason = score_row.veto_reason if score_row else None
    listing_dto = ListingDTO(
        id=row.id,
        hunt_id=hunt_id,
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
        score=score_val,
        score_reason=reason,
        match_reasons=match_reasons,
        mismatch_reasons=mismatch_reasons,
        components=components_dto,
        veto_reason=veto_reason,
    )
    travel_minutes_per_location = _travel_minutes_by_label(
        session, hunt_id=hunt_id, score_row=score_row
    )
    return ListingDetailDTO(
        listing=listing_dto,
        photos=photos,
        score=score_val,
        travel_minutes_per_location=travel_minutes_per_location,
    )


def _components_dto_from_row(
    score_row: Optional[ListingScoreRow],
) -> list[ComponentDTO]:
    """Rehydrate `components` JSON into DTOs for the listing drawer.

    Pre-migration rows (no `components`) return []; the drawer then
    falls back to the `score_reason` block.
    """
    if score_row is None or not score_row.components:
        return []
    out: list[ComponentDTO] = []
    for raw in score_row.components:
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
    hunt_id: str,
    score_row: Optional[ListingScoreRow],
) -> Optional[dict[str, int]]:
    """Convert the persisted `{place_id: {mode, minutes}}` blob into
    `{label: minutes}` by looking up each place_id in the hunt's
    SearchProfile.main_locations."""
    if score_row is None or not score_row.travel_minutes:
        return None
    hunt_row = session.get(HuntRow, hunt_id)
    if hunt_row is None:
        return None
    sp = repo.get_search_profile(session, username=hunt_row.username)
    if sp is None or not sp.main_locations:
        return None
    label_by_pid = {loc.place_id: loc.label for loc in sp.main_locations}
    out: dict[str, int] = {}
    for place_id, entry in score_row.travel_minutes.items():
        if not isinstance(entry, dict):
            continue
        minutes = entry.get("minutes")
        if not isinstance(minutes, int):
            continue
        label = label_by_pid.get(place_id)
        if label:
            out[label] = minutes
    return out or None


@router.post("/users", status_code=201, response_model=UserDTO)
def create_user(
    body: CreateUserBody,
    session: Session = Depends(get_session),
) -> UserDTO:
    if repo.get_user(session, username=body.username) is not None:
        raise HTTPException(status_code=409, detail="Username already taken")
    profile = UserProfile(
        username=body.username,
        age=body.age,
        gender=Gender(body.gender),
    )
    repo.create_user(session, profile=profile)
    return user_to_dto(profile)


@router.get("/users/{username}", response_model=UserDTO)
def get_user(username: str, session: Session = Depends(get_session)) -> UserDTO:
    u = repo.get_user(session, username=username)
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user_to_dto(u)


@router.put("/users/{username}/search-profile", response_model=SearchProfileDTO)
def put_search_profile(
    username: str,
    body: UpsertSearchProfileBody,
    session: Session = Depends(get_session),
) -> SearchProfileDTO:
    if repo.get_user(session, username=username) is None:
        raise HTTPException(status_code=404, detail="User not found")
    sp = upsert_body_to_search_profile(body)
    out = repo.upsert_search_profile(session, username=username, sp=sp)
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


@router.post("/users/{username}/hunts", status_code=201, response_model=HuntDTO)
async def create_hunt(
    username: str,
    body: CreateHuntBody,
    session: Session = Depends(get_session),
) -> HuntDTO:
    user = repo.get_user(session, username=username)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    sp = repo.get_search_profile(session, username=username)
    if sp is None:
        raise HTTPException(status_code=400, detail="User has no search profile yet")

    hunt = repo.create_hunt(session, username=username, schedule=body.schedule)
    repo.update_hunt_status(
        session, hunt_id=hunt.id, status=HuntStatus.running
    )
    rescan = (
        body.rescan_interval_minutes
        if body.rescan_interval_minutes is not None
        else sp.rescan_interval_minutes
    )
    boot = AgentAction(
        kind=ActionKind.boot,
        summary=f"Hunt queued ({body.schedule}).",
    )
    repo.append_action(session, hunt_id=hunt.id, action=boot)
    periodic.spawn_hunter(
        hunt.id,
        username,
        body.schedule,
        rescan,
    )
    q = periodic.event_queue_for(hunt.id)
    if q is not None:
        try:
            q.put_nowait(boot)
        except asyncio.QueueFull:
            pass
    fresh = repo.get_hunt(session, hunt_id=hunt.id)
    assert fresh is not None
    return hunt_to_dto(fresh, username=username, schedule=body.schedule)


@router.post("/hunts/{hunt_id}/stop", response_model=HuntDTO)
async def stop_hunt(hunt_id: str, session: Session = Depends(get_session)) -> HuntDTO:
    row = session.get(HuntRow, hunt_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Hunt not found")
    periodic.cancel_hunter(hunt_id)
    repo.update_hunt_status(
        session,
        hunt_id=hunt_id,
        status=HuntStatus.done,
        stopped_at=datetime.utcnow(),
    )
    repo.append_action(
        session,
        hunt_id=hunt_id,
        action=AgentAction(kind=ActionKind.done, summary="Stopped by user"),
    )
    fresh = repo.get_hunt(session, hunt_id=hunt_id)
    assert fresh is not None
    return hunt_to_dto(fresh, username=row.username, schedule=row.schedule)


@router.get("/hunts/{hunt_id}", response_model=HuntDTO)
def get_hunt_by_id(hunt_id: str, session: Session = Depends(get_session)) -> HuntDTO:
    row = session.get(HuntRow, hunt_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Hunt not found")
    h = repo.get_hunt(session, hunt_id=hunt_id)
    if h is None:
        raise HTTPException(status_code=404, detail="Hunt not found")
    return hunt_to_dto(h, username=row.username, schedule=row.schedule)


@router.get("/hunts/{hunt_id}/stream")
async def stream_hunt(
    hunt_id: str, session: Session = Depends(get_session)
) -> StreamingResponse:
    hunt = repo.get_hunt(session, hunt_id=hunt_id)
    if hunt is None:
        raise HTTPException(status_code=404, detail="Hunt not found")

    async def event_source():
        seen: set[tuple[datetime, str, str]] = set()
        for a in hunt.actions:
            seen.add((a.at, a.kind.value, a.summary))
            yield _sse(action_to_dto(a).model_dump(mode="json"))

        while True:
            action: AgentAction | None = None
            queue = periodic.event_queue_for(hunt_id)
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
                fresh = repo.get_hunt(s, hunt_id=hunt_id)
            if fresh is None:
                return
            for a in fresh.actions:
                key = (a.at, a.kind.value, a.summary)
                if key not in seen:
                    seen.add(key)
                    yield _sse(action_to_dto(a).model_dump(mode="json"))
            if fresh.status in (HuntStatus.done, HuntStatus.failed):
                yield _sse(
                    {
                        "kind": "stream-end",
                        "status": fresh.status.value,
                        "summary": "done",
                        "at": datetime.utcnow().isoformat(),
                    }
                )
                return
            yield ": keep-alive\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


@router.get("/listings/{listing_id}", response_model=ListingDetailDTO)
def get_listing_detail(
    listing_id: str,
    hunt_id: str = Query(..., description="Hunt scope for composite listing key"),
    session: Session = Depends(get_session),
) -> ListingDetailDTO:
    detail = _get_listing_detail(session, listing_id=listing_id, hunt_id=hunt_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return detail
