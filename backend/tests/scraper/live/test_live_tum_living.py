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


@pytest.mark.skipif(os.environ.get("SCRAPER_LIVE_TESTS") != "1", reason="set SCRAPER_LIVE_TESTS=1 to run")
def test_live_search_round_trip() -> None:
    async def _run() -> None:
        src = TumLivingSource()
        profile = SearchProfile(city="München", max_rent_eur=2000)
        wg_list = await src.search(kind="wg", profile=profile)
        flat_list = await src.search(kind="flat", profile=profile)
        assert len(wg_list) >= 1, "expected at least one WG listing from live API"
        assert len(flat_list) >= 1, "expected at least one flat listing from live API"

    asyncio.run(_run())
