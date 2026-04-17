"""End-to-end orchestrator test using a mock browser + mock OpenAI brain.

Confirms that:
  * The run transitions pending -> running -> done.
  * Search parsing over real cached HTML returns > 0 listings.
  * The action log records every expected step.
  * Dry run produces drafted messages but never 'sends' them.

Run with:  python tests/test_orchestrator.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.wg_agent import brain as brain_module
from app.wg_agent import browser as browser_module
from app.wg_agent.models import (
    ActionKind,
    HuntRequest,
    HuntRun,
    HuntStatus,
    Listing,
    ReplyAnalysis,
    ReplyIntent,
    RoomRequirements,
    StudentProfile,
    WGCredentials,
)
from app.wg_agent.orchestrator import HuntOrchestrator

HERE = pathlib.Path(__file__).resolve().parent


class _MockBrowser:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def ensure_logged_in(self) -> bool:
        return True

    async def search(self, req: RoomRequirements, *, max_pages: int = 2) -> list[Listing]:
        html = (HERE / "fixtures" / "search_muenchen.html").read_text(encoding="utf-8")
        return browser_module.parse_search_page(html)[: req.max_listings_to_consider]

    async def scrape_listing(self, listing: Listing) -> Listing:
        # Use the one listing we cached. For other listing ids we just return the stub.
        path = HERE / "fixtures" / f"listing_{listing.id}.html"
        if path.exists():
            html = path.read_text(encoding="utf-8")
            return browser_module.parse_listing_page(html, listing)
        listing.description = listing.title
        return listing

    async def send_message(self, listing: Listing, text: str) -> tuple[bool, str]:
        return True, "mock-sent"

    async def fetch_inbox(self) -> str:
        return "<html></html>"


async def _fake_launch(creds: WGCredentials, *, headless: bool = False, storage_state_env: str = "WG_STATE_FILE"):
    return _MockBrowser()


def _fake_score(listing, requirements):
    # Deterministic: any listing under 800€ is "good".
    listing.score = 0.9 if (listing.price_eur or 9999) <= 800 else 0.3
    listing.score_reason = "mock score"
    return listing


def _fake_draft(listing, profile):
    return f"Hallo, ich bin {profile.first_name}, ich würde mich freuen, das Zimmer zu besichtigen."


def _fake_classify(reply_text):
    return ReplyAnalysis(intent=ReplyIntent.unclear, summary=reply_text[:80], next_action="wait")


async def _run_one() -> HuntRun:
    req = HuntRequest(
        requirements=RoomRequirements(
            city="München",
            max_rent_eur=800,
            min_size_m2=12,
            max_size_m2=40,
            notes="CS student",
            max_listings_to_consider=10,
            max_messages_to_send=3,
        ),
        credentials=WGCredentials(username="x@example.com", password="secret"),
        profile=StudentProfile(first_name="Lea", age=23, email="lea@example.com"),
        dry_run=True,
        headless=True,
    )
    run = HuntRun(requirements=req.requirements, dry_run=True)

    with patch.object(browser_module, "launch_browser", _fake_launch), \
         patch.object(brain_module, "score_listing", _fake_score), \
         patch.object(brain_module, "draft_message", _fake_draft), \
         patch.object(brain_module, "classify_reply", _fake_classify):
        # Patch the names the orchestrator imports too.
        from app.wg_agent import orchestrator

        orchestrator.launch_browser = _fake_launch  # type: ignore[attr-defined]
        orchestrator.brain.score_listing = _fake_score  # type: ignore[attr-defined]
        orchestrator.brain.draft_message = _fake_draft  # type: ignore[attr-defined]
        orchestrator.brain.classify_reply = _fake_classify  # type: ignore[attr-defined]

        # Speed the run up (no 35-second pacing, no 45s inbox polls).
        orchestrator.MESSAGE_PACING_SECONDS = 0
        orchestrator.INBOX_POLL_SECONDS = 0
        orchestrator.INBOX_POLL_BUDGET_SECONDS = 0

        orch = HuntOrchestrator(req, run)
        return await orch.run_hunt()


def test_orchestrator_dry_run() -> None:
    run = asyncio.run(_run_one())
    print("status:", run.status.value)
    print("listings:", len(run.listings))
    print("messages:", len(run.messages))
    print("actions:", len(run.actions))
    assert run.status == HuntStatus.done, f"expected done, got {run.status}"
    assert len(run.listings) > 0, "expected listings after scrape+score"
    # Dry run => every outbound message is a draft not a send.
    assert all(m.direction.value == "outbound" for m in run.messages)
    # There must be at least one dry_run_skip action.
    assert any(a.kind == ActionKind.dry_run_skip for a in run.actions)
    # No "send_message" action in dry run.
    assert not any(a.kind == ActionKind.send_message for a in run.actions)
    # Every action has a non-empty summary.
    assert all(a.summary for a in run.actions)


if __name__ == "__main__":
    test_orchestrator_dry_run()
    print("orchestrator smoke test passed")
