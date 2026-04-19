"""Tail the scraper outbox and wake per-user matchers on new listings.

The scraper container (`app/scraper/agent.py`) appends one `ScraperEventRow`
every time it persists a brand-new, fully scraped listing. This watcher runs
inside the backend container and polls that table id-ordered. On any advance
it calls `periodic.wake_all_user_agents()` so every active matcher cuts its
between-pass sleep short and evaluates the new candidate immediately.

The watcher owns an in-memory watermark: on boot it seeds with
`repo.max_scraper_event_id()` so the post-boot initial state is "caught up"
(we don't want to re-trigger every historic event). A backend restart
therefore drops any events appended while it was down — which is fine,
because matchers already discover new listings on their own polled interval
as a backstop.

Polling cadence is controlled by `WG_WATCHER_INTERVAL_SECONDS` (default 30).
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlmodel import Session

from . import db as db_module
from . import periodic, repo

logger = logging.getLogger(__name__)


def _poll_interval_seconds() -> float:
    raw = os.environ.get("WG_WATCHER_INTERVAL_SECONDS", "30")
    try:
        value = float(raw)
    except ValueError:
        value = 30.0
    return max(1.0, value)


class ScraperWatcher:
    """Id-ordered outbox tailer that wakes user matchers on new events."""

    def __init__(self) -> None:
        self._last_id = 0
        self._interval = _poll_interval_seconds()

    def _seed_watermark(self) -> None:
        with Session(db_module.engine) as session:
            self._last_id = repo.max_scraper_event_id(session)
        logger.info(
            "Scraper watcher starting at outbox watermark id=%d (interval=%.1fs)",
            self._last_id,
            self._interval,
        )

    def _poll_once(self) -> int:
        """Drain the outbox once. Returns the number of events consumed."""
        with Session(db_module.engine) as session:
            events = repo.list_scraper_events_after(
                session, after_id=self._last_id, limit=500
            )
        if not events:
            return 0
        self._last_id = max(ev.id or 0 for ev in events)
        n_woken = periodic.wake_all_user_agents()
        logger.info(
            "Scraper watcher: %d new outbox event(s) -> woke %d matcher(s) (watermark=%d)",
            len(events),
            n_woken,
            self._last_id,
        )
        return len(events)

    async def run_forever(self) -> None:
        self._seed_watermark()
        while True:
            try:
                self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Scraper watcher poll failed; continuing")
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                raise


_WATCHER_TASK: asyncio.Task[None] | None = None


def start() -> None:
    """Launch the watcher on the running asyncio loop (idempotent)."""
    global _WATCHER_TASK
    if _WATCHER_TASK is not None and not _WATCHER_TASK.done():
        return
    _WATCHER_TASK = asyncio.create_task(ScraperWatcher().run_forever())


def stop() -> None:
    global _WATCHER_TASK
    if _WATCHER_TASK is None:
        return
    if not _WATCHER_TASK.done():
        _WATCHER_TASK.cancel()
    _WATCHER_TASK = None
