"""Per-user matcher: one asyncio task per user, keyed by username.

Reads fresh listings from the global `ListingRow` pool (owned by the scraper),
scores them against the user's `SearchProfile`, and persists matches +
action-log entries to `UserListingRow` / `UserActionRow` for the user.
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import Future
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session

from . import commute, evaluator, notifier, places, repo
from . import db as db_module
from .db_models import ListingRow, UserRow
from .models import ActionKind, AgentAction, NearbyPlace, SearchProfile

logger = logging.getLogger(__name__)
FIXED_USER_AGENT_INTERVAL_MINUTES = 30


def _notify_threshold() -> float:
    raw = os.environ.get("WG_NOTIFY_THRESHOLD", "0.9")
    try:
        return float(raw)
    except ValueError:
        return 0.9


def _notify_cooldown() -> timedelta:
    raw = os.environ.get("WG_NOTIFY_COOLDOWN_MINUTES", "5")
    try:
        minutes = float(raw)
    except ValueError:
        minutes = 5.0
    return timedelta(minutes=max(0.0, minutes))


class _NotifyState:
    """Per-user, in-process digest queue + last-send timestamp.

    Kept in memory only: a backend restart resets everyone's cooldown and
    drops their pending digest. That is acceptable for a demo — the matcher
    will rediscover any still-new (`first_seen_at > user.created_at`) listing
    on its next pass and requeue it.
    """

    __slots__ = ("pending", "last_sent_at")

    def __init__(self) -> None:
        self.pending: list[notifier.DigestItem] = []
        self.last_sent_at: Optional[datetime] = None


_NOTIFY_STATE: dict[str, _NotifyState] = {}


def _notify_state(username: str) -> _NotifyState:
    state = _NOTIFY_STATE.get(username)
    if state is None:
        state = _NotifyState()
        _NOTIFY_STATE[username] = state
    return state


def _try_flush_digest(username: str, to_email: Optional[str]) -> int:
    """Send all queued digest items for `username` if the cooldown has elapsed.

    Returns the number of items sent (0 when skipped). Safe to call when no
    email is configured or no items are pending — both are no-ops.
    """
    state = _notify_state(username)
    if not state.pending or not to_email:
        return 0
    cooldown = _notify_cooldown()
    now = datetime.utcnow()
    if state.last_sent_at is not None and now - state.last_sent_at < cooldown:
        logger.info(
            "Holding %d pending matches for %s: cooldown %.1fs remaining",
            len(state.pending),
            username,
            (cooldown - (now - state.last_sent_at)).total_seconds(),
        )
        return 0
    items = list(state.pending)
    sent = notifier.send_digest_email(
        to_email=to_email, items=items, username=username
    )
    if sent:
        state.pending.clear()
        state.last_sent_at = now
        return len(items)
    return 0


def _all_modes_min_per_location(
    travel_times: dict[tuple[str, str], int],
) -> dict[str, dict[str, int]]:
    """Collect all computed travel modes per place_id for drawer display.

    Returns {place_id: {mode_lower: minutes, ...}} so the drawer can show
    transit, bicycle, and drive side-by-side instead of only the fastest.
    """
    out: dict[str, dict[str, int]] = {}
    for (place_id, mode), seconds in travel_times.items():
        out.setdefault(place_id, {})[mode.lower()] = round(seconds / 60)
    return out


def _evaluate_detail(
    travel_minutes: dict[str, dict[str, int | object]],
    main_locations: list,
) -> Optional[str]:
    """Human-readable fastest-mode summary for the evaluate action's `detail`."""
    if not travel_minutes or not main_locations:
        return None
    parts: list[str] = []
    for loc in main_locations:
        entry = travel_minutes.get(loc.place_id)
        if not entry or not isinstance(entry, dict):
            continue
        if "minutes" in entry:
            # old format: {mode: "transit", minutes: 27}
            minutes = entry.get("minutes")
            mode = str(entry.get("mode", "?")).lower()
            if not isinstance(minutes, int):
                continue
            parts.append(f"{loc.label}: {minutes} min ({mode})")
        else:
            # new format: {transit: 27, bicycle: 16, drive: 13}
            int_vals = {k: v for k, v in entry.items() if isinstance(v, int)}
            if not int_vals:
                continue
            best_mode = min(int_vals, key=lambda k: int_vals[k])
            parts.append(f"{loc.label}: {int_vals[best_mode]} min ({best_mode})")
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
# Per-user fan-out: every active SSE connection for a username appends its own
# queue here, and every action is published to *all* of them. An asyncio.Queue
# delivers each item to exactly one waiter, so a single shared queue would
# starve every device but the first one when the same user is open in multiple
# browsers/tabs.
_SUBSCRIBERS: dict[str, list[asyncio.Queue[AgentAction]]] = {}
_RUNTIME_LOOP: asyncio.AbstractEventLoop | None = None


def _publish(username: str, action: AgentAction) -> None:
    for queue in _SUBSCRIBERS.get(username, ()):
        try:
            queue.put_nowait(action)
        except asyncio.QueueFull:
            pass


def set_runtime_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    global _RUNTIME_LOOP
    _RUNTIME_LOOP = loop


def _create_task(coro: object) -> asyncio.Task[None]:
    try:
        loop = asyncio.get_running_loop()
        return loop.create_task(coro)  # type: ignore[arg-type]
    except RuntimeError:
        if _RUNTIME_LOOP is None:
            raise RuntimeError("Periodic matcher loop is not initialized")

        future: Future[asyncio.Task[None]] = Future()

        def _schedule() -> None:
            task = _RUNTIME_LOOP.create_task(coro)  # type: ignore[arg-type]
            future.set_result(task)

        _RUNTIME_LOOP.call_soon_threadsafe(_schedule)
        return future.result()


def _append(session: Session, username: str, action: AgentAction) -> None:
    repo.append_user_action(session, username=username, action=action)


class UserAgent:
    """Per-user match + rank engine.

    Never scrapes — the scraper container is the sole writer of `ListingRow`.
    Logs actions via `repo.append_user_action` (persisted) and broadcasts them
    to every active SSE subscriber for this user via `_publish`. Does not send
    messages or poll the inbox.
    """

    def __init__(self, username: str) -> None:
        self._username = username

    async def run_match_pass(self, *, max_listings: int = 15) -> int:
        with Session(db_module.engine) as session:
            sp = repo.get_search_profile(session, username=self._username)
            user_row = session.get(UserRow, self._username)
        if sp is None:
            sp = SearchProfile(city="München", max_rent_eur=2000)
        user_email = user_row.email if user_row is not None else None
        user_created_at = user_row.created_at if user_row is not None else None

        with Session(db_module.engine) as session:
            candidate_rows = repo.list_scorable_listings_for_user(
                session,
                username=self._username,
                status="full",
                limit=max_listings,
                mode=sp.mode,
            )

        n_candidates = len(candidate_rows)
        new_count = n_candidates

        search_action = AgentAction(
            kind=ActionKind.search,
            summary=f"Matched {n_candidates} candidates from shared pool.",
        )
        with Session(db_module.engine) as session:
            _append(session, self._username, search_action)
        _publish(self._username, search_action)

        for row in candidate_rows:
            listing = repo.row_to_domain_listing(row)
            nl = AgentAction(
                kind=ActionKind.new_listing,
                summary=f"New listing: {listing.title or listing.id}",
                listing_id=listing.id,
            )
            with Session(db_module.engine) as session:
                _append(session, self._username, nl)
            _publish(self._username, nl)

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
                _publish(self._username, err)
                continue

            travel_minutes = (
                _all_modes_min_per_location(travel_times) if travel_times else None
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

            self._maybe_queue_digest_item(
                row=row,
                listing=listing,
                user_email=user_email,
                user_created_at=user_created_at,
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
                    summary=f"Scored {listing.id}: {round((listing.score or 0.0) * 100)}%",
                    detail=" | ".join(ev_detail_parts) or None,
                    listing_id=listing.id,
                )
            with Session(db_module.engine) as session:
                _append(session, self._username, ev)
            _publish(self._username, ev)

        _try_flush_digest(self._username, user_email)
        return new_count

    def _maybe_queue_digest_item(
        self,
        *,
        row: ListingRow,
        listing,
        user_email: Optional[str],
        user_created_at: Optional[datetime],
    ) -> None:
        """Queue a listing for the next digest flush, applying all gates.

        A listing is only queued when:
        * the user has a notification email configured,
        * its score passes `WG_NOTIFY_THRESHOLD`,
        * the scraper first-saw it *after* the user's account was created —
          this is what excludes the initial-evaluation backlog and is the
          single source of truth for "is this a new listing?" regardless of
          whether the scraper runs on the server or on a developer laptop.
        """
        if not user_email:
            return
        score = float(listing.score or 0.0)
        if score < _notify_threshold():
            return
        if user_created_at is None or row.first_seen_at is None:
            return
        if row.first_seen_at <= user_created_at:
            return
        state = _notify_state(self._username)
        state.pending.append(
            notifier.DigestItem(
                listing_title=listing.title or "",
                listing_url=str(listing.url),
                score=score,
                match_reasons=list(listing.match_reasons),
            )
        )


class PeriodicUserMatcher:
    """Runs `UserAgent.run_match_pass` in a continuous loop per user."""

    def __init__(
        self,
        username: str,
        *,
        interval_minutes: int,
    ) -> None:
        self._username = username
        self._interval = interval_minutes
        raw = os.environ.get("WG_RESCAN_INTERVAL_MINUTES")
        if raw is not None:
            try:
                v = int(raw.strip())
                if v > 0:
                    self._interval = v
            except ValueError:
                pass
        self._agent = UserAgent(username)

    def _sleep_seconds(self) -> float:
        return float(max(self._interval, 1)) * 60.0

    async def _emit_rescan(self) -> None:
        act = AgentAction(
            kind=ActionKind.rescan,
            summary="Rescanning listings…",
        )
        with Session(db_module.engine) as session:
            _append(session, self._username, act)
        _publish(self._username, act)

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
                _publish(self._username, err)

            try:
                await asyncio.sleep(self._sleep_seconds())
            except asyncio.CancelledError:
                raise
            await self._emit_rescan()


def spawn_user_agent(username: str, *, interval_minutes: int = 30) -> None:
    existing = _ACTIVE_AGENTS.get(username)
    if existing is not None and not existing.done():
        return
    boot = AgentAction(
        kind=ActionKind.boot,
        summary=f"Agent started for {username}",
    )
    try:
        with Session(db_module.engine) as session:
            _append(session, username, boot)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to persist boot action for %s", username)
    _publish(username, boot)
    matcher = PeriodicUserMatcher(
        username=username,
        interval_minutes=FIXED_USER_AGENT_INTERVAL_MINUTES,
    )
    task = _create_task(matcher.start())
    _ACTIVE_AGENTS[username] = task


def cancel_user_agent(username: str) -> bool:
    task = _ACTIVE_AGENTS.get(username)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def subscribe(username: str) -> asyncio.Queue[AgentAction]:
    """Register a new SSE subscriber for `username` and return its private queue.

    Every published action for `username` is fanned out to every subscriber's
    queue, so opening the same account on two devices results in both seeing
    every event.
    """
    queue: asyncio.Queue[AgentAction] = asyncio.Queue()
    _SUBSCRIBERS.setdefault(username, []).append(queue)
    return queue


def unsubscribe(username: str, queue: asyncio.Queue[AgentAction]) -> None:
    subs = _SUBSCRIBERS.get(username)
    if not subs:
        return
    try:
        subs.remove(queue)
    except ValueError:
        pass
    if not subs:
        _SUBSCRIBERS.pop(username, None)


def is_agent_running(username: str) -> bool:
    task = _ACTIVE_AGENTS.get(username)
    return task is not None and not task.done()


async def resume_user_agents() -> None:
    with Session(db_module.engine) as session:
        usernames = repo.list_usernames_with_search_profile(session)
    for username in usernames:
        spawn_user_agent(
            username, interval_minutes=FIXED_USER_AGENT_INTERVAL_MINUTES
        )
