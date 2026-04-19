"""TUM Living (living.tum.de) GraphQL source plugin.

Verified queries and field notes: `docs/SCRAPER.md` § "Source: tum-living".
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from typing import Any, AsyncIterator, Optional

import httpx

from ...wg_agent.models import Listing, SearchProfile
from .base import Kind

logger = logging.getLogger(__name__)

BASE_URL = "https://living.tum.de"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

name = "tum-living"
kind_supported = frozenset({"wg", "flat"})
search_page_delay_seconds = 2.5
detail_delay_seconds = 2.5
refresh_hours = 48

LISTINGS_QUERY = """\
query GetListings($resultLimit: Int, $pageOffset: Int, $orderBy: ListingSortOrder, $filter: ListingFilter) {
    listings(resultLimit: $resultLimit, pageOffset: $pageOffset, orderBy: $orderBy, filter: $filter) {
      id
      uuid
      type
      numberOfRooms
      availableFrom
      availableUntil
      city
      tumLocation
      district
      street
      houseNumber
      postalCode
      totalRent
      deposit
      squareMeter
      tags
      images { id modifiedAt description descriptionEn }
      seekingStudents
      seekingProfessors
      seekingIncomings
      seekingDoctoralStudents
      seekingPostDoctoralStudents
      seekingGuestResearchers
      seekingTumEmployees
      isListingPublic
      publicationDate
      verifiedAt
      createdAt
      isActive
      coordinates { x y }
      previewImage { id description descriptionEn }
    }
  }
"""

DETAIL_QUERY = """\
fragment listingByUUID on Listing {
    id
    uuid
    type
    housingType
    floor
    numberOfRooms
    availableFrom
    availableUntil
    city
    tumLocation
    district
    street
    houseNumber
    postalCode
    rent
    totalRent
    incidentalCosts
    incidentalCostsCustomLabel
    incidentalCostsTypes
    oneTimeCosts
    oneTimeCostsLabel
    parkingSpace
    parkingSpaceCosts
    deposit
    squareMeter
    tags
    images { id listing fileName description descriptionEn isPreview modifiedAt }
    seekingStudents
    seekingProfessors
    seekingIncomings
    seekingDoctoralStudents
    seekingPostDoctoralStudents
    seekingGuestResearchers
    seekingTumEmployees
    furtherEquipment
    furtherEquipmentEn
    isActive
    isListingPublic
    publicationDate
    expirationDate
    verifiedAt
    createdAt
    modifiedAt
    coordinates { x y }
    contactName
    contactPhone
    contactEmail
  }

  query GetListingByUUIDWithoutContactInfo($uuid: String!) {
    listingByUUID(uuid: $uuid) {
      landlord { id uuid contactType }
      ...listingByUUID
    }
  }
"""


def _parse_iso_to_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    s = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def _parse_iso_to_datetime(value: Optional[str]) -> Optional[datetime]:
    """Same as `_parse_iso_to_date`, but returns the full naive UTC datetime.

    Used by the freshness filter on `publicationDate` / `createdAt`. Falls
    back to `None` on malformed input rather than raising; the agent treats
    a missing `posted_at` as "fresh" so a parser regression never silently
    halts the pass.
    """
    if not value:
        return None
    s = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        # Normalize to naive UTC so the cutoff comparison stays in
        # `datetime.utcnow()` space (the convention used everywhere else
        # in the scraper, e.g. `ScraperAgent._needs_scrape`).
        dt = dt.astimezone(tz=timezone.utc).replace(tzinfo=None)
    return dt


def _tags_bool(tags: Optional[list[str]], needle: str) -> Optional[bool]:
    t = tags or []
    return True if needle in t else None


def _furnished_from_tags(tags: Optional[list[str]]) -> Optional[bool]:
    t = tags or []
    if "FURNISHED" in t or "PARTLY_FURNISHED" in t:
        return True
    return None


def _stub_from_listings_item(item: dict[str, Any]) -> Listing:
    uuid = item["uuid"]
    tags = item.get("tags")
    coords = item.get("coordinates") or {}
    street = item.get("street")
    house_number = item.get("houseNumber")
    if street is not None and house_number is not None:
        st = str(street).strip()
        hn = str(house_number).strip()
        address = f"{st} {hn}".strip() if st and hn else None
    else:
        address = None
    listing_type = item["type"]
    kind: Kind = "wg" if listing_type == "SHARED_APARTMENT" else "flat"
    preview = item.get("previewImage") or {}
    preview_id = preview.get("id")
    cover = (
        f"https://living.tum.de/api/image/{preview_id}/1280"
        if preview_id
        else None
    )
    stub = Listing(
        id=f"tum-living:{uuid}",
        url=f"https://living.tum.de/listings/{uuid}/view",
        title=f"{listing_type} · {item['numberOfRooms']}R · {item['city']}",
        kind=kind,
        city=item.get("city"),
        district=item.get("district"),
        address=address,
        lat=coords.get("x"),
        lng=coords.get("y"),
        price_eur=item.get("totalRent"),
        size_m2=item.get("squareMeter"),
        wg_size=None,
        available_from=_parse_iso_to_date(item.get("availableFrom")),
        available_to=_parse_iso_to_date(item.get("availableUntil")),
        furnished=_furnished_from_tags(tags),
        pets_allowed=_tags_bool(tags, "PETS_ALLOWED"),
        smoking_ok=_tags_bool(tags, "SMOKING"),
        online_viewing=False,
        languages=[],
        cover_photo_url=cover,
    )
    stub.posted_at = _parse_iso_to_datetime(
        item.get("publicationDate") or item.get("createdAt")
    )
    return stub


def _sorted_image_urls(item: dict[str, Any], *, limit: int = 12) -> list[str]:
    images = list(item.get("images") or [])
    images.sort(key=lambda im: (0 if im.get("isPreview") else 1))
    out: list[str] = []
    for im in images[:limit]:
        iid = im.get("id")
        if iid is not None:
            out.append(f"https://living.tum.de/api/image/{iid}/1280")
    return out


def _cover_from_detail(item: dict[str, Any], photo_urls: list[str]) -> Optional[str]:
    prev = item.get("previewImage") or {}
    pid = prev.get("id")
    if pid is not None:
        return f"https://living.tum.de/api/image/{pid}/1280"
    for im in item.get("images") or []:
        if im.get("isPreview") and im.get("id") is not None:
            return f"https://living.tum.de/api/image/{im['id']}/1280"
    return photo_urls[0] if photo_urls else None


def _apply_detail_to_stub(stub: Listing, item: dict[str, Any]) -> Listing:
    desc = item.get("furtherEquipmentEn") or item.get("furtherEquipment")
    if desc:
        stub.description = desc

    coords = item.get("coordinates") or {}
    if stub.lat is None and coords.get("x") is not None:
        stub.lat = coords["x"]
    if stub.lng is None and coords.get("y") is not None:
        stub.lng = coords["y"]

    tags = item.get("tags")
    if stub.furnished is None:
        stub.furnished = _furnished_from_tags(tags)
    if stub.pets_allowed is None:
        stub.pets_allowed = _tags_bool(tags, "PETS_ALLOWED")
    if stub.smoking_ok is None:
        stub.smoking_ok = _tags_bool(tags, "SMOKING")

    if not stub.address:
        street = item.get("street")
        house_number = item.get("houseNumber")
        if street is not None and house_number is not None:
            st = str(street).strip()
            hn = str(house_number).strip()
            if st and hn:
                stub.address = f"{st} {hn}".strip()

    if stub.available_from is None:
        stub.available_from = _parse_iso_to_date(item.get("availableFrom"))
    if stub.available_to is None:
        stub.available_to = _parse_iso_to_date(item.get("availableUntil"))

    photo_urls = _sorted_image_urls(item, limit=12)
    stub.photo_urls = photo_urls
    stub.cover_photo_url = _cover_from_detail(item, photo_urls)

    return stub


def _parse_listings_response(body: dict[str, Any]) -> list[Listing]:
    data = body.get("data") or {}
    raw = data.get("listings") or []
    return [_stub_from_listings_item(it) for it in raw]


def _parse_detail_item(body: dict[str, Any]) -> Optional[dict[str, Any]]:
    if body.get("errors"):
        return None
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    item = data.get("listingByUUID")
    if not isinstance(item, dict):
        return None
    return item


class TumLivingSource:
    """GraphQL + CSRF source for living.tum.de."""

    name = name
    kind_supported = kind_supported
    search_page_delay_seconds = search_page_delay_seconds
    detail_delay_seconds = detail_delay_seconds
    refresh_hours = refresh_hours

    def looks_like_block_page(self, text: str, status: int) -> bool:
        if status >= 500:
            return True
        if "EBADCSRFTOKEN" in text:
            return True
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return False
        errors = obj.get("errors")
        data = obj.get("data")
        if errors and data is None:
            return True
        return False

    async def _mint_csrf(self, client: httpx.AsyncClient) -> None:
        me = await client.get("/api/me")
        me.raise_for_status()
        token = me.json()["csrf"]
        client.headers["csrf-token"] = token

    async def _graphql_post(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        *,
        allow_csrf_retry: bool,
    ) -> httpx.Response:
        resp = await client.post("/graphql", json=payload)
        if allow_csrf_retry and "EBADCSRFTOKEN" in resp.text:
            logger.warning("tum-living GraphQL returned EBADCSRFTOKEN; reminting CSRF and retrying once")
            await self._mint_csrf(client)
            resp = await client.post("/graphql", json=payload)
        resp.raise_for_status()
        return resp

    async def search_pages(
        self, *, kind: Kind, profile: SearchProfile
    ) -> AsyncIterator[list[Listing]]:
        del profile  # search filter is kind-only for this source
        if kind not in self.kind_supported:
            return
        if kind == "wg":
            filter_obj: dict[str, str] = {"type": "SHARED_APARTMENT"}
        else:
            filter_obj = {"type": "APARTMENT"}

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(
            base_url=BASE_URL,
            headers=headers,
            timeout=20.0,
        ) as client:
            await self._mint_csrf(client)
            page = 0
            while True:
                payload = {
                    "operationName": "GetListings",
                    "variables": {
                        "resultLimit": 25,
                        "pageOffset": page * 25,
                        "orderBy": "MOST_RECENT",
                        "filter": filter_obj,
                    },
                    "query": LISTINGS_QUERY,
                }
                try:
                    resp = await self._graphql_post(
                        client,
                        payload,
                        allow_csrf_retry=True,
                    )
                except httpx.HTTPError:
                    if page == 0:
                        raise
                    return
                body = resp.json()
                batch = _parse_listings_response(body)
                if not batch:
                    return
                yield batch
                if len(batch) < 25:
                    return
                await asyncio.sleep(self.search_page_delay_seconds)
                page += 1

    async def scrape_detail(self, stub: Listing) -> Listing:
        if not stub.id.startswith("tum-living:"):
            return stub
        uuid = stub.id.split(":", 1)[1]
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
        }
        payload = {
            "operationName": "GetListingByUUIDWithoutContactInfo",
            "variables": {"uuid": uuid},
            "query": DETAIL_QUERY,
        }
        async with httpx.AsyncClient(
            base_url=BASE_URL,
            headers=headers,
            timeout=20.0,
        ) as client:
            await self._mint_csrf(client)
            resp = await self._graphql_post(client, payload, allow_csrf_retry=True)
            body = resp.json()
            item = _parse_detail_item(body)
            if item is None:
                return stub
            _apply_detail_to_stub(stub, item)
        return stub
