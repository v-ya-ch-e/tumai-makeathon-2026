"""Hunt orchestrator: runs one autonomous WG-Gesucht hunt end-to-end.

Loop (simplified):
    1. Launch browser (cookies if available, else login).
    2. Search WG-Gesucht → get `Listing` stubs.
    3. For each listing: scrape description → score with LLM → keep top K.
    4. For each top listing: draft message → (unless dry run) send.
    5. Poll inbox every 45s for N minutes, classify every inbound message.
    6. On `viewing_offer` → reply to confirm the slot, record viewing.
    7. On `asks_for_info` → reply with the student's info.
    8. Stop after messages cap or time budget expires.

The orchestrator publishes every step to `Hunt.actions` and to an asyncio.Queue
so the FastAPI SSE endpoint can stream progress to the UI.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from bs4 import BeautifulSoup

from . import brain
from .browser import WGBrowser, launch_browser
from .models import (
    ActionKind,
    AgentAction,
    Hunt,
    HuntStatus,
    Listing,
    Message,
    MessageDirection,
    ReplyIntent,
)

if TYPE_CHECKING:
    from .api import HuntRequest

logger = logging.getLogger(__name__)

# How long between inbox polls.
INBOX_POLL_SECONDS = 45
# How long between outbound messages (anti-rate-limit).
MESSAGE_PACING_SECONDS = 35
# Total wall-clock budget for inbox polling.
INBOX_POLL_BUDGET_SECONDS = 8 * 60  # 8 minutes
# Listings to actually deep-scrape + evaluate.
DEEP_SCRAPE_CAP = 15
# Minimum LLM score for a listing to qualify for messaging.
DEFAULT_MIN_SCORE = 0.55


class HuntOrchestrator:
    """One instance per hunt run, owned by the FastAPI background task."""

    def __init__(self, request: "HuntRequest", run: Hunt) -> None:
        self.request = request
        self.run = run
        self.event_queue: asyncio.Queue[AgentAction] = asyncio.Queue()
        self._browser: Optional[WGBrowser] = None

    # --- Action bookkeeping --------------------------------------------------

    def _log(
        self,
        kind: ActionKind,
        summary: str,
        *,
        detail: Optional[str] = None,
        listing_id: Optional[str] = None,
    ) -> AgentAction:
        action = AgentAction(
            kind=kind, summary=summary, detail=detail, listing_id=listing_id
        )
        self.run.actions.append(action)
        # Broadcast to the SSE queue; never block the run on a full queue.
        try:
            self.event_queue.put_nowait(action)
        except asyncio.QueueFull:
            pass
        logger.info("[%s] %s", kind.value, summary)
        return action

    # --- Entry point ---------------------------------------------------------

    async def run_hunt(self) -> Hunt:
        self.run.status = HuntStatus.running
        self._log(
            ActionKind.boot,
            (
                f"Starting {'DRY-RUN ' if self.request.dry_run else ''}hunt for a WG "
                f"in {self.request.requirements.city} ≤ {self.request.requirements.max_rent_eur}€"
            ),
        )
        try:
            await self._go()
            self.run.status = HuntStatus.done
            self._log(ActionKind.done, "Hunt finished.")
        except Exception as exc:  # noqa: BLE001 — we want to surface every failure
            logger.exception("Hunt failed")
            self.run.status = HuntStatus.failed
            self.run.error = f"{type(exc).__name__}: {exc}"
            self._log(ActionKind.error, f"Hunt failed: {self.run.error}")
        finally:
            self.run.finished_at = datetime.utcnow()
            if self._browser is not None:
                await self._browser.close()
            # Sentinel so SSE clients know we're done.
            try:
                self.event_queue.put_nowait(
                    AgentAction(kind=ActionKind.done, summary="stream-end")
                )
            except asyncio.QueueFull:
                pass
        return self.run

    # --- The actual pipeline -------------------------------------------------

    async def _go(self) -> None:
        # 1. Browser + login
        self._browser = await launch_browser(
            self.request.credentials, headless=self.request.headless
        )
        self._log(ActionKind.login, "Browser launched — checking session …")
        ok = await self._browser.ensure_logged_in()
        if not ok:
            raise RuntimeError(
                "Login to wg-gesucht failed. Check cookies or credentials."
            )
        self._log(ActionKind.login, "Logged in to wg-gesucht.")

        # 2. Search
        stubs = await self._browser.search(self.request.requirements, max_pages=2)
        self._log(
            ActionKind.search,
            f"Found {len(stubs)} listings on the first {min(2, len(stubs))} pages.",
        )
        if not stubs:
            return

        # 3. Deep-scrape + score (on the top-N by price ascending, to favor cheaper ones).
        stubs.sort(key=lambda l: (l.price_eur or 9_999))
        to_evaluate = stubs[:DEEP_SCRAPE_CAP]
        self._log(
            ActionKind.scrape,
            f"Deep-scraping + evaluating {len(to_evaluate)} listings.",
        )
        for listing in to_evaluate:
            try:
                enriched = await self._browser.scrape_listing(listing)
            except Exception as exc:  # noqa: BLE001
                self._log(
                    ActionKind.error,
                    f"Could not scrape listing {listing.id}: {exc}",
                    listing_id=listing.id,
                )
                continue
            try:
                brain.score_listing(enriched, self.request.requirements)
            except Exception as exc:  # noqa: BLE001
                self._log(
                    ActionKind.error,
                    f"LLM scoring failed for {listing.id}: {exc}",
                    listing_id=listing.id,
                )
                enriched.score = 0.0
                enriched.score_reason = "LLM failure"
            self.run.listings.append(enriched)
            self._log(
                ActionKind.evaluate,
                (
                    f"Evaluated '{(enriched.title or '')[:60]}' — "
                    f"score {enriched.score:.2f} ({enriched.score_reason or ''})"
                ),
                listing_id=enriched.id,
            )

        # 4. Pick winners and message them.
        winners = [
            l
            for l in sorted(
                self.run.listings, key=lambda l: l.score or 0, reverse=True
            )
            if (l.score or 0) >= DEFAULT_MIN_SCORE
        ][: self.request.requirements.max_messages_to_send]

        if not winners:
            self._log(
                ActionKind.done,
                "No listings matched the requirements with high enough confidence.",
            )
            return

        self._log(
            ActionKind.draft_message,
            f"Top {len(winners)} listings selected for outreach.",
        )

        for listing in winners:
            await self._contact_listing(listing)
            await asyncio.sleep(MESSAGE_PACING_SECONDS)

        # 5. Poll inbox for replies.
        if not self.request.dry_run and self.run.messages:
            await self._poll_inbox_loop()

    async def _contact_listing(self, listing: Listing) -> None:
        try:
            body = brain.draft_message(listing, self.request.profile)
        except Exception as exc:  # noqa: BLE001
            self._log(
                ActionKind.error,
                f"Draft failed for {listing.id}: {exc}",
                listing_id=listing.id,
            )
            return
        self._log(
            ActionKind.draft_message,
            f"Drafted message for '{(listing.title or '')[:60]}'.",
            detail=body,
            listing_id=listing.id,
        )

        msg = Message(
            direction=MessageDirection.outbound,
            listing_id=listing.id,
            text=body,
        )
        if self.request.dry_run:
            self._log(
                ActionKind.dry_run_skip,
                f"DRY RUN — not actually sending to listing {listing.id}.",
                listing_id=listing.id,
            )
            self.run.messages.append(msg)
            return

        assert self._browser is not None
        ok, detail = await self._browser.send_message(listing, body)
        if ok:
            self.run.messages.append(msg)
            self._log(
                ActionKind.send_message,
                f"Sent message to listing {listing.id}: {detail}",
                detail=body,
                listing_id=listing.id,
            )
        else:
            self._log(
                ActionKind.error,
                f"Could not send to listing {listing.id}: {detail}",
                listing_id=listing.id,
            )

    # --- Inbox polling -------------------------------------------------------

    async def _poll_inbox_loop(self) -> None:
        assert self._browser is not None
        deadline = time.time() + INBOX_POLL_BUDGET_SECONDS
        seen_message_signatures: set[str] = set()
        self._log(
            ActionKind.poll_inbox,
            f"Watching inbox for up to {INBOX_POLL_BUDGET_SECONDS // 60} min.",
        )

        while time.time() < deadline:
            try:
                html = await self._browser.fetch_inbox()
            except Exception as exc:  # noqa: BLE001
                self._log(ActionKind.error, f"Inbox fetch failed: {exc}")
                await asyncio.sleep(INBOX_POLL_SECONDS)
                continue
            new_replies = _extract_inbox_replies(html)
            for reply in new_replies:
                sig = f"{reply['listing_id']}:{reply['hash']}"
                if sig in seen_message_signatures:
                    continue
                seen_message_signatures.add(sig)
                await self._handle_reply(reply)
            await asyncio.sleep(INBOX_POLL_SECONDS)

    async def _handle_reply(self, reply: dict) -> None:
        listing = next((l for l in self.run.listings if l.id == reply["listing_id"]), None)
        if listing is None:
            return
        self._log(
            ActionKind.classify_reply,
            f"Analyzing reply on listing {listing.id}.",
            detail=reply["text"][:2000],
            listing_id=listing.id,
        )
        try:
            analysis = brain.classify_reply(reply["text"])
        except Exception as exc:  # noqa: BLE001
            self._log(
                ActionKind.error,
                f"Reply classification failed: {exc}",
                listing_id=listing.id,
            )
            return

        self.run.messages.append(
            Message(
                direction=MessageDirection.inbound,
                listing_id=listing.id,
                text=reply["text"],
            )
        )
        self._log(
            ActionKind.classify_reply,
            f"Reply intent = {analysis.intent.value}; next action = {analysis.next_action}.",
            detail=analysis.summary,
            listing_id=listing.id,
        )

        if analysis.next_action == "drop":
            return
        if analysis.next_action == "wait":
            return

        mode = (
            "accept_viewing"
            if analysis.next_action == "accept_viewing"
            else "answer_questions"
        )
        try:
            reply_body = brain.reply_to_landlord(
                reply["text"], listing, self.request.profile, mode=mode
            )
        except Exception as exc:  # noqa: BLE001
            self._log(
                ActionKind.error,
                f"Reply draft failed: {exc}",
                listing_id=listing.id,
            )
            return

        if self.request.dry_run:
            self._log(
                ActionKind.dry_run_skip,
                f"DRY RUN — not sending follow-up to {listing.id}.",
                detail=reply_body,
                listing_id=listing.id,
            )
            if analysis.intent == ReplyIntent.viewing_offer:
                self.run.viewings.append(
                    f"[DRY RUN] Would confirm viewing for {listing.id} "
                    f"at one of: {', '.join(analysis.proposed_times) or 'landlord-proposed slot'}"
                )
            return

        assert self._browser is not None
        ok, detail = await self._browser.send_message(listing, reply_body)
        if not ok:
            self._log(
                ActionKind.error,
                f"Follow-up send failed for {listing.id}: {detail}",
                listing_id=listing.id,
            )
            return
        self.run.messages.append(
            Message(
                direction=MessageDirection.outbound,
                listing_id=listing.id,
                text=reply_body,
            )
        )
        if analysis.intent == ReplyIntent.viewing_offer:
            summary = (
                f"Confirmed viewing for listing {listing.id}: "
                f"{', '.join(analysis.proposed_times) or 'time tbd'}"
            )
            self.run.viewings.append(summary)
            self._log(
                ActionKind.propose_viewing,
                summary,
                detail=reply_body,
                listing_id=listing.id,
            )
        else:
            self._log(
                ActionKind.send_message,
                f"Answered landlord questions on listing {listing.id}.",
                detail=reply_body,
                listing_id=listing.id,
            )


# -----------------------------------------------------------------------------
# Inbox parsing (kept small & tolerant — wg-gesucht renames classes often)
# -----------------------------------------------------------------------------

_CONV_ID_RE = re.compile(r"conv_id=(\d+)")
_LISTING_ID_IN_URL_RE = re.compile(r"/(\d{5,9})\.html")


def _extract_inbox_replies(html: str) -> list[dict]:
    """Return a list of {listing_id, hash, text} dicts for every unread inbound message."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []

    # The unread-thread anchors carry both the conversation id and (usually) the
    # linked listing id.
    for a in soup.select("a[href*='conv_id'], a.mailbox_thread, a.mailbox_thread_unread"):
        href = a.get("href", "")
        conv_match = _CONV_ID_RE.search(href)
        if not conv_match:
            continue
        listing_match = _LISTING_ID_IN_URL_RE.search(href)
        listing_id = listing_match.group(1) if listing_match else ""
        # We take the visible text of the thread as a coarse approximation of the
        # latest message (enough for the LLM to classify).
        snippet = _clean_text(a.get_text(" "))
        if not snippet or not listing_id:
            continue
        out.append(
            {
                "listing_id": listing_id,
                "hash": str(hash(snippet)),
                "text": snippet,
            }
        )
    return out


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()
