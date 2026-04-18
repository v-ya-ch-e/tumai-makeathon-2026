"""Per-user matcher: one asyncio task per user, keyed by username.

Reads fresh listings from the global `ListingRow` pool (owned by the scraper),
scores them against the user's `SearchProfile`, and persists matches +
action-log entries to `UserListingRow` / `UserActionRow` for the user.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from sqlmodel import Session

from . import commute, evaluator, places, repo
from . import db as db_module
from .models import ActionKind, AgentAction, NearbyPlace, SearchProfile

logger = logging.getLogger(__name__)


def _shortest_mode_min_per_location(
    travel_times: dict[tuple[str, str], int],
) -> dict[str, dict[str, object]]:
    """Pick the fastest `(mode, minutes)` per place_id for drawer display."""
    best: dict[str, tuple[str, int]] = {}
    for (place_id, mode), seconds in travel_times.items():
        minutes = round(seconds / 60)
        current = best.get(place_id)
        if current is None or minutes < current[1]:
            best[place_id] = (mode, minutes)
    return {pid: {"mode": mode, "minutes": minutes} for pid, (mode, minutes) in best.items()}


def _evaluate_detail(
    travel_minutes: dict[str, dict[str, object]],
    main_locations: list,
) -> Optional[str]:
    """Human-readable fastest-mode summary for the evaluate action's `detail`."""
    if not travel_minutes or not main_locations:
        return None
    parts: list[str] = []
    for loc in main_locations:
        entry = travel_minutes.get(loc.place_id)
        if not entry:
            continue
        mode = str(entry.get("mode", "")).lower() or "?"
        minutes = entry.get("minutes")
        if not isinstance(minutes, int):
            continue
        parts.append(f"{loc.label}: {minutes} min ({mode})")
    return "; ".join(parts) or None


def _nearby_places_detail(
    nearby_places: dict[str, NearbyPlace],
    preferences: list,
) -> Optional[str]:
    if not nearby_places or not preferences:
        return None
    parts: list[str] = []
    seen: set[str] = set()
    for pref in preferences:
        if pref.key in seen:
            continue
        seen.add(pref.key)
        item = nearby_places.get(pref.key)
        if item is None:
            continue
        if not item.searched:
            parts.append(f"{item.label}: lookup unavailable")
            continue
        if item.distance_m is None:
            parts.append(f"{item.label}: >{places.SEARCH_RADIUS_M // 1000} km")
            continue
        parts.append(f"{item.label}: {item.distance_m} m")
    return "; ".join(parts) or None


_ACTIVE_AGENTS: dict[str, asyncio.Task[None]] = {}
_EVENT_QUEUES: dict[str, asyncio.Queue[AgentAction]] = {}


def _safe_put(queue: asyncio.Queue[AgentAction], action: AgentAction) -> None:
    try:
        queue.put_nowait(action)
    except asyncio.QueueFull:
        pass


def _append(session: Session, username: str, action: AgentAction) -> None:
    repo.append_user_action(session, username=username, action=action)


class UserAgent:
    """Per-user match + rank engine.

    Never scrapes — the scraper container is the sole writer of `ListingRow`.
    Logs actions via `repo.append_user_action` (persisted) and onto a shared
    asyncio.Queue for SSE. Does not send messages or poll the inbox.
    """

    def __init__(
        self,
        username: str,
        event_queue: asyncio.Queue[AgentAction],
    ) -> None:
        self._username = username
        self._event_queue = event_queue

    async def run_match_pass(self, *, max_listings: int = 15) -> int:
        with Session(db_module.engine) as session:
            sp = repo.get_search_profile(session, username=self._username)
        if sp is None:
            sp = SearchProfile(city="München", max_rent_eur=2000)

        with Session(db_module.engine) as session:
            candidate_rows = repo.list_scorable_listings_for_user(
                session,
                username=self._username,
                status="full",
                limit=max_listings,
            )

        n_candidates = len(candidate_rows)
        new_count = n_candidates

        search_action = AgentAction(
            kind=ActionKind.search,
            summary=f"Matched {n_candidates} candidates from shared pool.",
        )
        with Session(db_module.engine) as session:
            _append(session, self._username, search_action)
        _safe_put(self._event_queue, search_action)

        for row in candidate_rows:
            listing = repo.row_to_domain_listing(row)
            nl = AgentAction(
                kind=ActionKind.new_listing,
                summary=f"New listing: {listing.title or listing.id}",
                listing_id=listing.id,
            )
            with Session(db_module.engine) as session:
                _append(session, self._username, nl)
            _safe_put(self._event_queue, nl)

            try:
                travel_times: dict[tuple[str, str], int] = {}
                nearby_places: dict[str, NearbyPlace] = {}
                if (
                    listing.lat is not None
                    and listing.lng is not None
                    and sp.main_locations
                ):
                    travel_times = await commute.travel_times(
                        origin=(listing.lat, listing.lng),
                        destinations=sp.main_locations,
                        modes=commute.modes_for(sp),
                    )
                if (
                    listing.lat is not None
                    and listing.lng is not None
                    and sp.preferences
                ):
                    nearby_places = await places.nearby_places(
                        origin=(listing.lat, listing.lng),
                        preferences=sp.preferences,
                    )
                result = await evaluator.evaluate(
                    listing,
                    sp,
                    travel_times=travel_times,
                    nearby_places=nearby_places,
                )
                listing.score = result.score
                listing.score_reason = result.summary
                listing.match_reasons = list(result.match_reasons)
                listing.mismatch_reasons = list(result.mismatch_reasons)
                listing.components = list(result.components)
                listing.veto_reason = result.veto_reason
            except Exception as exc:  # noqa: BLE001
                err = AgentAction(
                    kind=ActionKind.error,
                    summary=f"Score failed for {listing.id}: {exc}",
                    detail=str(exc),
                    listing_id=listing.id,
                )
                with Session(db_module.engine) as session:
                    _append(session, self._username, err)
                _safe_put(self._event_queue, err)
                continue

            travel_minutes = (
                _shortest_mode_min_per_location(travel_times) if travel_times else None
            )
            with Session(db_module.engine) as session:
                repo.save_user_match(
                    session,
                    username=self._username,
                    listing_id=listing.id,
                    score=float(listing.score or 0.0),
                    reason=listing.score_reason,
                    match_reasons=list(listing.match_reasons),
                    mismatch_reasons=list(listing.mismatch_reasons),
                    travel_minutes=travel_minutes,
                    nearby_places=nearby_places,
                    components=list(listing.components),
                    veto_reason=listing.veto_reason,
                    scored_against_scraped_at=row.scraped_at,
                )

            if result.veto_reason is not None:
                ev = AgentAction(
                    kind=ActionKind.evaluate,
                    summary=f"Rejected {listing.id}: {result.veto_reason}",
                    listing_id=listing.id,
                )
            else:
                ev_detail_parts: list[str] = []
                breakdown = evaluator.breakdown_detail(result.components)
                if breakdown:
                    ev_detail_parts.append(breakdown)
                commute_detail = (
                    _evaluate_detail(travel_minutes, list(sp.main_locations))
                    if travel_minutes
                    else None
                )
                if commute_detail:
                    ev_detail_parts.append(commute_detail)
                nearby_detail = _nearby_places_detail(
                    nearby_places,
                    list(sp.preferences),
                )
                if nearby_detail:
                    ev_detail_parts.append(nearby_detail)
                ev = AgentAction(
                    kind=ActionKind.evaluate,
                    summary=f"Scored {listing.id}: {listing.score:.2f}",
                    detail=" | ".join(ev_detail_parts) or None,
                    listing_id=listing.id,
                )
            with Session(db_module.engine) as session:
                _append(session, self._username, ev)
            _safe_put(self._event_queue, ev)

        return new_count


class PeriodicUserMatcher:
    """Runs `UserAgent.run_match_pass` in a continuous loop per user."""

    def __init__(
        self,
        username: str,
        event_queue: asyncio.Queue[AgentAction],
        *,
        interval_minutes: int,
    ) -> None:
        self._username = username
        self._event_queue = event_queue
        self._interval = interval_minutes
        raw = os.environ.get("WG_RESCAN_INTERVAL_MINUTES")
        if raw is not None:
            try:
                v = int(raw.strip())
                if v > 0:
                    self._interval = v
            except ValueError:
                pass
        self._agent = UserAgent(username, event_queue)

    def _sleep_seconds(self) -> float:
        return float(max(self._interval, 1)) * 60.0

    async def _emit_rescan(self) -> None:
        act = AgentAction(
            kind=ActionKind.rescan,
            summary="Rescanning listings…",
        )
        with Session(db_module.engine) as session:
            _append(session, self._username, act)
        _safe_put(self._event_queue, act)

    async def start(self) -> None:
        while True:
            try:
                await self._agent.run_match_pass()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "User agent %s pass failed: %s",
                    self._username,
                    exc,
                    exc_info=True,
                )
                err = AgentAction(
                    kind=ActionKind.error,
                    summary=f"Match pass failed: {exc}",
                    detail=str(exc),
                )
                try:
                    with Session(db_module.engine) as session:
                        _append(session, self._username, err)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to persist error action for %s", self._username
                    )
                _safe_put(self._event_queue, err)

            try:
                await asyncio.sleep(self._sleep_seconds())
            except asyncio.CancelledError:
                raise
            await self._emit_rescan()


def spawn_user_agent(username: str, *, interval_minutes: int = 30) -> None:
    existing = _ACTIVE_AGENTS.get(username)
    if existing is not None and not existing.done():
        return
    queue: asyncio.Queue[AgentAction] = asyncio.Queue()
    _EVENT_QUEUES[username] = queue
    boot = AgentAction(
        kind=ActionKind.boot,
        summary=f"Agent started for {username}",
    )
    try:
        with Session(db_module.engine) as session:
            _append(session, username, boot)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to persist boot action for %s", username)
    _safe_put(queue, boot)
    matcher = PeriodicUserMatcher(
        username=username,
        event_queue=queue,
        interval_minutes=interval_minutes,
    )
    task = asyncio.create_task(matcher.start())
    _ACTIVE_AGENTS[username] = task


def cancel_user_agent(username: str) -> bool:
    task = _ACTIVE_AGENTS.get(username)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def event_queue_for(username: str) -> asyncio.Queue[AgentAction] | None:
    return _EVENT_QUEUES.get(username)


def is_agent_running(username: str) -> bool:
    task = _ACTIVE_AGENTS.get(username)
    return task is not None and not task.done()


async def resume_user_agents() -> None:
    with Session(db_module.engine) as session:
        usernames = repo.list_usernames_with_search_profile(session)
    for username in usernames:
        with Session(db_module.engine) as session:
            sp = repo.get_search_profile(session, username=username)
        rescan = sp.rescan_interval_minutes if sp is not None else 30
        spawn_user_agent(username, interval_minutes=rescan)
