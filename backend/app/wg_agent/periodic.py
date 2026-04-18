"""Periodic hunt runner: find + rank loop with SSE-friendly action queue."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

from sqlmodel import Session

from . import browser, commute, evaluator, repo
from . import db as db_module
from .db_models import HuntRow
from .models import ActionKind, AgentAction, HuntStatus, Listing, SearchProfile

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


_ACTIVE_HUNTERS: dict[str, asyncio.Task[None]] = {}
_EVENT_QUEUES: dict[str, asyncio.Queue[AgentAction]] = {}


def _safe_put(queue: asyncio.Queue[AgentAction], action: AgentAction) -> None:
    try:
        queue.put_nowait(action)
    except asyncio.QueueFull:
        pass


def _append(session: Session, hunt_id: str, action: AgentAction) -> None:
    repo.append_action(session, hunt_id=hunt_id, action=action)


class HuntEngine:
    """Simplified find + rank engine for v1.

    Logs actions via repo.append_action (persisted) and onto a shared
    asyncio.Queue for SSE. Does not send messages or poll the inbox.
    """

    def __init__(
        self,
        hunt_id: str,
        username: str,
        event_queue: asyncio.Queue[AgentAction],
    ) -> None:
        self._hunt_id = hunt_id
        self._username = username
        self._event_queue = event_queue

    async def run_find_only(self, *, max_listings: int = 15) -> int:
        # v1 always uses anonymous search; credentials are stored but unused.
        with Session(db_module.engine) as session:
            sp = repo.get_search_profile(session, username=self._username)
        if sp is None:
            sp = SearchProfile(city="München", max_rent_eur=2000)

        with Session(db_module.engine) as session:
            existing = {l.id for l in repo.list_listings_for_hunt(session, hunt_id=self._hunt_id)}

        try:
            found = await browser.anonymous_search(sp, max_pages=2)
        except Exception as exc:  # noqa: BLE001
            err = AgentAction(
                kind=ActionKind.error,
                summary=f"Search failed: {exc}",
                detail=str(exc),
            )
            with Session(db_module.engine) as session:
                _append(session, self._hunt_id, err)
            _safe_put(self._event_queue, err)
            return 0

        n_found = len(found)
        capped = found[:max_listings]
        new_stubs = [L for L in capped if L.id not in existing]
        new_count = len(new_stubs)

        search_action = AgentAction(
            kind=ActionKind.search,
            summary=f"Anonymous search found {n_found} listings on up to 2 pages.",
        )
        with Session(db_module.engine) as session:
            _append(session, self._hunt_id, search_action)
        _safe_put(self._event_queue, search_action)

        for listing in new_stubs:
            nl = AgentAction(
                kind=ActionKind.new_listing,
                summary=f"New listing: {listing.title or listing.id}",
                listing_id=listing.id,
            )
            with Session(db_module.engine) as session:
                _append(session, self._hunt_id, nl)
            _safe_put(self._event_queue, nl)

            try:
                enriched = await browser.anonymous_scrape_listing(
                    listing, req_city=sp.city
                )
                travel_times: dict[tuple[str, str], int] = {}
                if (
                    enriched.lat is not None
                    and enriched.lng is not None
                    and sp.main_locations
                ):
                    travel_times = await commute.travel_times(
                        origin=(enriched.lat, enriched.lng),
                        destinations=sp.main_locations,
                        modes=commute.modes_for(sp),
                    )
                result = await evaluator.evaluate(
                    enriched, sp, travel_times=travel_times
                )
                enriched.score = result.score
                enriched.score_reason = result.summary
                enriched.match_reasons = list(result.match_reasons)
                enriched.mismatch_reasons = list(result.mismatch_reasons)
                enriched.components = list(result.components)
                enriched.veto_reason = result.veto_reason
            except Exception as exc:  # noqa: BLE001
                err = AgentAction(
                    kind=ActionKind.error,
                    summary=f"Scrape/score failed for {listing.id}: {exc}",
                    detail=str(exc),
                    listing_id=listing.id,
                )
                with Session(db_module.engine) as session:
                    _append(session, self._hunt_id, err)
                _safe_put(self._event_queue, err)
                continue

            travel_minutes = (
                _shortest_mode_min_per_location(travel_times) if travel_times else None
            )
            with Session(db_module.engine) as session:
                repo.upsert_listing(session, hunt_id=self._hunt_id, listing=enriched)
                repo.save_photos(
                    session,
                    hunt_id=self._hunt_id,
                    listing_id=enriched.id,
                    urls=list(enriched.photo_urls),
                )
                repo.save_score(
                    session,
                    hunt_id=self._hunt_id,
                    listing_id=enriched.id,
                    score=float(enriched.score or 0.0),
                    reason=enriched.score_reason,
                    match_reasons=list(enriched.match_reasons),
                    mismatch_reasons=list(enriched.mismatch_reasons),
                    travel_minutes=travel_minutes,
                    components=list(enriched.components),
                    veto_reason=enriched.veto_reason,
                )

            if result.veto_reason is not None:
                ev = AgentAction(
                    kind=ActionKind.evaluate,
                    summary=f"Rejected {enriched.id}: {result.veto_reason}",
                    listing_id=enriched.id,
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
                ev = AgentAction(
                    kind=ActionKind.evaluate,
                    summary=f"Scored {enriched.id}: {enriched.score:.2f}",
                    detail=" | ".join(ev_detail_parts) or None,
                    listing_id=enriched.id,
                )
            with Session(db_module.engine) as session:
                _append(session, self._hunt_id, ev)
            _safe_put(self._event_queue, ev)

        return new_count


class PeriodicHunter:
    """Runs HuntEngine.run_find_only on an interval until cancelled or one_shot."""

    def __init__(
        self,
        hunt_id: str,
        username: str,
        interval_minutes: int,
        event_queue: asyncio.Queue[AgentAction],
        *,
        schedule: str,
    ) -> None:
        self._hunt_id = hunt_id
        self._username = username
        self._event_queue = event_queue
        self._schedule = schedule
        self._interval = interval_minutes
        if schedule == "periodic" and interval_minutes > 0:
            raw = os.environ.get("WG_RESCAN_INTERVAL_MINUTES")
            if raw is not None:
                try:
                    v = int(raw.strip())
                    if v > 0:
                        self._interval = v
                except ValueError:
                    pass
        self._engine = HuntEngine(hunt_id, username, event_queue)

    def cancel(self) -> None:
        return

    def _sleep_seconds(self) -> float:
        return float(self._interval) * 60.0

    async def _emit_rescan(self) -> None:
        act = AgentAction(
            kind=ActionKind.rescan,
            summary="Rescanning listings…",
        )
        with Session(db_module.engine) as session:
            _append(session, self._hunt_id, act)
        _safe_put(self._event_queue, act)

    async def _finalize_done(self) -> None:
        done = AgentAction(kind=ActionKind.done, summary="Hunt finished")
        with Session(db_module.engine) as session:
            repo.update_hunt_status(
                session,
                hunt_id=self._hunt_id,
                status=HuntStatus.done,
                stopped_at=datetime.utcnow(),
            )
            _append(session, self._hunt_id, done)
        _safe_put(self._event_queue, done)

    async def _finalize_failed(self, msg: str) -> None:
        err = AgentAction(kind=ActionKind.error, summary=msg, detail=msg)
        with Session(db_module.engine) as session:
            repo.update_hunt_status(
                session,
                hunt_id=self._hunt_id,
                status=HuntStatus.failed,
                stopped_at=datetime.utcnow(),
            )
            _append(session, self._hunt_id, err)
        _safe_put(self._event_queue, err)

    async def start(self) -> None:
        try:
            while True:
                await self._engine.run_find_only()
                if self._schedule == "one_shot" or self._interval <= 0:
                    break
                try:
                    await asyncio.sleep(self._sleep_seconds())
                except asyncio.CancelledError:
                    raise
                await self._emit_rescan()
            await self._finalize_done()
        except asyncio.CancelledError:
            await self._finalize_done()
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Hunt %s failed: %s", self._hunt_id, exc, exc_info=True)
            await self._finalize_failed(str(exc))


def spawn_hunter(
    hunt_id: str,
    username: str,
    schedule: str,
    rescan_interval_minutes: int,
) -> None:
    if hunt_id in _ACTIVE_HUNTERS and not _ACTIVE_HUNTERS[hunt_id].done():
        return
    queue: asyncio.Queue[AgentAction] = asyncio.Queue()
    _EVENT_QUEUES[hunt_id] = queue
    interval = rescan_interval_minutes if schedule == "periodic" else 0
    hunter = PeriodicHunter(
        hunt_id=hunt_id,
        username=username,
        interval_minutes=interval,
        event_queue=queue,
        schedule=schedule,
    )
    task = asyncio.create_task(hunter.start())
    _ACTIVE_HUNTERS[hunt_id] = task


def cancel_hunter(hunt_id: str) -> bool:
    task = _ACTIVE_HUNTERS.get(hunt_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def event_queue_for(hunt_id: str) -> asyncio.Queue[AgentAction] | None:
    return _EVENT_QUEUES.get(hunt_id)


async def resume_running_hunts() -> None:
    with Session(db_module.engine) as session:
        hunts = repo.list_hunts_by_status(session, status=HuntStatus.running)
    for h in hunts:
        with Session(db_module.engine) as session:
            row = session.get(HuntRow, h.id)
            if row is None:
                continue
            sp = repo.get_search_profile(session, username=row.username)
            if sp is None:
                rescan = 30
            else:
                rescan = sp.rescan_interval_minutes
            spawn_hunter(
                hunt_id=h.id,
                username=row.username,
                schedule=row.schedule,
                rescan_interval_minutes=rescan,
            )
