"""Multi-source scraper plugins.

Each module implements one `Source` (see `base.py`). `ScraperAgent` builds
its registry by reading `SCRAPER_ENABLED_SOURCES` (comma-separated source
names; default `wg-gesucht`) and instantiating the matching classes.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from .base import Source
from .kleinanzeigen import KleinanzeigenSource
from .tum_living import TumLivingSource
from .wg_gesucht import WgGesuchtSource

logger = logging.getLogger(__name__)


_REGISTRY: dict[str, type] = {
    WgGesuchtSource.name: WgGesuchtSource,
    TumLivingSource.name: TumLivingSource,
    KleinanzeigenSource.name: KleinanzeigenSource,
}


def build_sources(enabled: Optional[str] = None) -> list[Source]:
    """Construct a list of `Source` instances for the active loop.

    `enabled` is a comma-separated list of source names; falls back to
    `$SCRAPER_ENABLED_SOURCES`, then to `wg-gesucht` so today's
    deployment runs the existing single-source loop unless explicitly
    opted in.
    """
    raw = enabled if enabled is not None else os.environ.get("SCRAPER_ENABLED_SOURCES", "wg-gesucht")
    names = [part.strip() for part in raw.split(",") if part.strip()]
    out: list[Source] = []
    for name in names:
        cls = _REGISTRY.get(name)
        if cls is None:
            logger.warning("Unknown SCRAPER_ENABLED_SOURCES entry %r, skipping", name)
            continue
        out.append(cls())
    if not out:
        logger.warning(
            "No valid sources resolved from %r; falling back to wg-gesucht only",
            raw,
        )
        out.append(WgGesuchtSource())
    return out


__all__ = [
    "Source",
    "WgGesuchtSource",
    "TumLivingSource",
    "KleinanzeigenSource",
    "build_sources",
]
