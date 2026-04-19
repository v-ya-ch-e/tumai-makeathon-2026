"""District-level price percentile for the matcher's `price_fit` evidence.

MATCHER.md §5.1 promises a market-percentile evidence string of the
form `"€650 at the 22nd percentile for wg in Schwabing ~18 m² [engine]"`.
Computing it cheaply requires the global `ListingRow` pool the scraper
already maintains: filter by `kind`, district (case+umlaut-folded),
size within ±20 %, and `scrape_status='full'`. If we have at least 6
peers, return `(percentile, peer_count)`; otherwise return `None` and
the evaluator emits no percentile evidence.

In v2 the percentile is purely an evidence string — it does not move
`price_fit.score` (MATCHER.md §5.1, §12). We expose just enough so v3
can lift it into a tiny score adjustment without changing the API.

The function is read-only and side-effect-free; it takes a `Session`
the caller already owns. Per match pass the matcher caches the result
in a small dict keyed by `(district_norm, kind, size_bucket)` so we
don't redo the same SQL for every listing in the same district.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from .db_models import ListingRow

logger = logging.getLogger(__name__)


# A listing is treated as a peer when its `size_m2` is within ±20 % of
# the target listing's size. Smaller windows starve the percentile;
# larger ones mix studios with 2-bedroom flats.
_SIZE_TOLERANCE = 0.20

# We need at least this many peers to compute a percentile that's not
# noise. Fewer peers → return `None` and skip the evidence string.
MIN_PEERS = 6


@dataclass(frozen=True)
class MarketContext:
    """A snapshot of how the listing's price compares to its peer group.

    `percentile` is in `[0, 100]` — lower means cheaper relative to peers.
    `peer_count` is the number of listings the percentile was computed
    over (always ≥ `MIN_PEERS`). `district_label` is the human-readable
    district from the listing itself, used in evidence strings.
    """

    percentile: int
    peer_count: int
    district_label: str
    median_price_eur: int


def _normalize_district(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    lowered = value.strip().lower()
    return (
        lowered.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
        .replace("-", " ")
    )


def market_context(
    session: Session,
    *,
    listing_id: str,
    district: Optional[str],
    kind: str,
    size_m2: Optional[float],
    price_eur: Optional[int],
) -> Optional[MarketContext]:
    """Compute the market-percentile evidence for `listing` against its peers.

    Returns `None` when any of the following holds:
      * `district`, `size_m2`, or `price_eur` is missing on the listing
        (we cannot define a peer group),
      * fewer than `MIN_PEERS` peers match the filter (percentile would
        be too noisy to print),
      * the SQL query fails (we never want a transient DB error to
        bubble up into the matcher loop).

    The function is read-only and idempotent. Logs at WARNING on the
    DB-error fallback path so the matcher can still surface it via the
    drawer's error log if it becomes frequent.
    """
    norm_district = _normalize_district(district)
    if norm_district is None or size_m2 is None or price_eur is None:
        return None
    lo = float(size_m2) * (1.0 - _SIZE_TOLERANCE)
    hi = float(size_m2) * (1.0 + _SIZE_TOLERANCE)

    try:
        rows = list(
            session.exec(
                select(ListingRow.id, ListingRow.price_eur, ListingRow.district)
                .where(ListingRow.scrape_status == "full")
                .where(ListingRow.kind == kind)
                .where(ListingRow.price_eur.is_not(None))  # type: ignore[union-attr]
                .where(ListingRow.size_m2.is_not(None))    # type: ignore[union-attr]
                .where(ListingRow.size_m2 >= lo)
                .where(ListingRow.size_m2 <= hi)
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("market_context: query failed: %s", exc)
        return None

    peer_prices: list[int] = []
    for row_id, peer_price, peer_district in rows:
        if row_id == listing_id:
            continue
        if _normalize_district(peer_district) != norm_district:
            continue
        if peer_price is None:
            continue
        peer_prices.append(int(peer_price))

    if len(peer_prices) < MIN_PEERS:
        return None

    sorted_prices = sorted(peer_prices)
    cheaper_or_equal = sum(1 for p in sorted_prices if p <= int(price_eur))
    percentile = int(round(100.0 * cheaper_or_equal / max(1, len(sorted_prices))))
    median = sorted_prices[len(sorted_prices) // 2]

    return MarketContext(
        percentile=max(0, min(100, percentile)),
        peer_count=len(sorted_prices),
        district_label=district or "",
        median_price_eur=median,
    )


__all__ = ["MarketContext", "MIN_PEERS", "market_context"]
