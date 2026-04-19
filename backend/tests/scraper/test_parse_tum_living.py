"""Offline parser tests for TUM Living GraphQL listing mapping."""

from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from app.scraper.sources import tum_living
from app.scraper.sources.tum_living import TumLivingSource
from app.wg_agent.models import Listing

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "tum_living"


def _load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_listings_response() -> None:
    body = _load_json("get_listings.json")
    stubs = tum_living._parse_listings_response(body)
    assert stubs, "expected at least one stub from get_listings.json"
    uuid_re = re.compile(r"^tum-living:[0-9a-f-]{36}$")
    for stub in stubs:
        assert uuid_re.match(stub.id), stub.id
        assert stub.kind in {"wg", "flat"}
        assert str(stub.url).startswith("https://living.tum.de/listings/")
        assert str(stub.url).endswith("/view")
        assert stub.price_eur is not None and stub.price_eur > 0
    # Per docs/SCRAPER.md § "Source: tum-living" (recon summary):
    # individual listings can lack `coordinates`; we leave `lat=lng=None` and
    # let `commute_fit` short-circuit. Most listings *should* have them, so
    # assert at least one stub has both.
    with_coords = [s for s in stubs if s.lat is not None and s.lng is not None]
    assert with_coords, "expected at least one stub with coordinates"
    # The fixture should cover both verticals (it's an unfiltered fetch).
    kinds = {s.kind for s in stubs}
    assert "wg" in kinds or "flat" in kinds, kinds


def test_parse_listing_detail_apply() -> None:
    paths = sorted(FIXTURES.glob("get_listing_*.json"))
    assert paths, "expected at least one detail fixture"
    body = json.loads(paths[0].read_text(encoding="utf-8"))
    item = tum_living._parse_detail_item(body)
    assert item is not None
    uuid = item["uuid"]
    stub = Listing(
        id=f"tum-living:{uuid}",
        url=f"https://living.tum.de/listings/{uuid}/view",
        title="stub",
        kind="wg",
    )
    tum_living._apply_detail_to_stub(stub, item)
    # Description comes from `furtherEquipmentEn` / `furtherEquipment` per the
    # SOURCE doc; a real listing should have a non-trivial body.
    assert stub.description and len(stub.description) > 10
    assert len(stub.photo_urls) >= 1
    for url in stub.photo_urls:
        assert url.startswith("https://living.tum.de/api/image/"), url
    assert stub.cover_photo_url
    assert stub.cover_photo_url.startswith("https://living.tum.de/api/image/")


def test_looks_like_block_page_csrf_and_healthy() -> None:
    src = TumLivingSource()
    bad = '{"errors":[{"message":"invalid csrf token","code":"EBADCSRFTOKEN"}],"data":null}'
    assert src.looks_like_block_page(bad, 200) is True
    healthy = json.dumps(_load_json("get_listings.json"))
    assert src.looks_like_block_page(healthy, 200) is False


def test_looks_like_block_page_graphql_errors_only() -> None:
    src = TumLivingSource()
    body = '{"errors":[{"message":"something broke"}],"data":null}'
    assert src.looks_like_block_page(body, 200) is True


def test_looks_like_block_page_status_500() -> None:
    assert TumLivingSource().looks_like_block_page("{}", 503) is True
