"""Live smoke tests for TUM Living (disabled unless SCRAPER_LIVE_TESTS=1)."""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent.parent))

from app.scraper.sources.tum_living import TumLivingSource
from app.wg_agent.models import SearchProfile


async def _collect_first_page(src: TumLivingSource, *, kind: str, profile: SearchProfile) -> list:
    """Drain one page off the search_pages iterator, then close it."""
    pages = src.search_pages(kind=kind, profile=profile)
    try:
        async for batch in pages:
            return batch
    finally:
        aclose = getattr(pages, "aclose", None)
        if aclose is not None:
            await aclose()
    return []


@pytest.mark.skipif(os.environ.get("SCRAPER_LIVE_TESTS") != "1", reason="set SCRAPER_LIVE_TESTS=1 to run")
def test_live_search_round_trip() -> None:
    async def _run() -> None:
        src = TumLivingSource()
        profile = SearchProfile(city="München", max_rent_eur=2000)
        wg_first = await _collect_first_page(src, kind="wg", profile=profile)
        flat_first = await _collect_first_page(src, kind="flat", profile=profile)
        assert len(wg_first) >= 1, "expected at least one WG listing from live API"
        assert len(flat_first) >= 1, "expected at least one flat listing from live API"

    asyncio.run(_run())
