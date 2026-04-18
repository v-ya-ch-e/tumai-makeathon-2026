# Source: living.tum.de

> GraphQL API scrape of TUM Living housing platform. Serves **both** WG rooms and full apartments for TUM students, staff, and researchers.

## At a glance

- **Site:** `https://living.tum.de/listings?viewMode=list`
- **Transport:** `httpx + JSON` (Apollo-server GraphQL endpoint at `https://living.tum.de/graphql`). **No Playwright needed.**
- **Anonymous-accessible?** Yes, with a one-shot **CSRF mint**: `GET /api/me` returns `{user: null, csrf: "<token>"}` and sets a `csrf-token` cookie. Reuse both (cookie kept by the client jar, token sent back as the `csrf-token` request header) on every GraphQL POST. Without the pair the server replies `{"errors":[{"message":"invalid csrf token","code":"EBADCSRFTOKEN"}],"data":null}`. No login needed for any read query.
- **Listing kinds offered:** **both** — `type=APARTMENT` (whole flat), `type=HOUSE` (rare, treat as flat), and `type=SHARED_APARTMENT` (room in a shared flat). `ListingType` is the WG-vs-flat discriminator. (Don't confuse with the unrelated `housingType` enum, which describes the building/floor: `APARTMENT | ATTIC | BASEMENT | GROUND_FLOOR | MEZZANINE`.)
- **Suggested cadence:** one search every 15 min (conservative, the corpus is small — 167 active listings on 2026-04-18); refresh detail after 48 h (listings turn over much slower than wg-gesucht).

## Recon summary (date: 2026-04-18)

- **Verified via:** real Chrome capture in the cursor-ide-browser MCP (`browser_network_requests`) on `https://living.tum.de/listings?viewMode=list`, plus extraction of the verbatim GraphQL query strings from the React bundle at `https://living.tum.de/static/js/main.affa1ba3.js`, plus anonymous `curl` POSTs to `https://living.tum.de/graphql` whose JSON responses confirmed every field name in the mapping table below. Sample listing `id=691` (uuid `cf76dd26-0bbb-45af-b74d-14f5face8ba0`) and one `SHARED_APARTMENT` (`3ec9cfbb-…`) were used as fixtures.
- **Page structure:** The listings page is a Create-React-App SPA (single bundle `main.affa1ba3.js`) that hydrates from a **GraphQL API** at `https://living.tum.de/graphql`. The initial HTML is a 3.3 KB shell.
- **Network on first paint** (verified): two GraphQL POSTs (`GetListings` and `GetNumberOfListings`), one REST GET (`/api/tags`), then one `/api/image/thumbnail/<id>?ts=<modifiedAt-millis>` GET per visible listing card.
- **Listing detail pages:** UUID-based URLs like `https://living.tum.de/listings/cf76dd26-0bbb-45af-b74d-14f5face8ba0/view`. The detail page POSTs `GetListingByUUIDWithoutContactInfo` (anonymous) or `GetListingByUUID` (logged-in with contact info) to `/graphql`.
- **Anonymous access:** Confirmed end-to-end. `/api/me` returns `404` with body `{"user": null, "csrf": "<token>"}` and sets a `csrf-token` cookie — that pair unlocks every read query. The `404` status is misleading: the body is the intended response.
- **Anonymous landlord visibility (counter-intuitive):** `GetListings` exposes `landlord.email` and `landlord.phone` to anonymous callers (verified in the raw response). `GetListingByUUID*` returns `landlord: null` for anonymous callers — even when `GetListingByUUID` (the with-contact variant) is used, the server silently nulls the landlord rather than erroring. Net: anonymous **list** has contacts, anonymous **detail** does not.
- **Anti-bot:** `robots.txt` allows everything (`User-agent: *, Disallow:`). No captcha, WAF, or Cloudflare interstitial observed. Server is `nginx/1.26.3` fronting `Express` (`X-Powered-By: Express`).
- **RSS feed:** **Does not exist as XML.** `GET /rss`, `/feed`, `/listings.rss` all return the SPA shell (3313 bytes, `Content-Type: text/html`); `/api/rss`, `/api/feed` return 404. Treat the footer link as decorative; use `GetListings(orderBy: MOST_RECENT)` for change detection instead.
- **Photos:** Two URL helpers exist in the bundle:
  - `https://living.tum.de/api/image/thumbnail/<image_id>?ts=<modifiedAt-millis>` — small thumbnail used in the list view (e.g. ~40 KB). Verified by the network log.
  - `https://living.tum.de/api/image/<image_id>[/<size>]` — generic helper with optional resize. Verified sizes: `320` (~13 KB), `640` (~39 KB), `1280` (~120 KB), `2048` (~246 KB, may upscale). Omitting `<size>` (or passing `full` / `thumbnail`) returns the original (~200 KB for a 1920×1280 JPEG). All image URLs are hot-linkable without auth (no cookies, no CSRF).
  - The `?ts=` cache-buster equals `Date(image.modifiedAt).getTime()` — informational only; the URL works without it.
- **Coordinates:** Confirmed in both list and detail responses as `coordinates { x, y }` where **`x` is latitude, `y` is longitude** (sample: `{x: 48.1184617, y: 11.5707928}` for a Munich listing). Not nested under address; coordinates is a sibling field.

## Identifier mapping

- **External id format:** UUID (e.g. `cf76dd26-0bbb-45af-b74d-14f5face8ba0`). Appears as `uuid` on every listing object **and** as the URL path segment in `https://living.tum.de/listings/<uuid>/view`. There is also a separate numeric `id` field (e.g. `"691"`) used as the database primary key, but the **UUID is the public identifier** — every URL and every detail-query input uses the UUID, not the numeric `id`.
- **Mapping to `ListingRow.id`:** `f"tum-living:{uuid}"` (e.g. `"tum-living:cf76dd26-0bbb-45af-b74d-14f5face8ba0"`).
- **Extraction strategy:** read `listing.uuid` straight off the GraphQL response.

## How to list listings (search)

- **HTTP request:** `POST https://living.tum.de/graphql` with headers:
  ```
  Content-Type: application/json
  csrf-token: <token-from-/api/me>
  Cookie: csrf-token=<cookie-from-/api/me>
  User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36
  ```
  The `csrf-token` cookie value and the `csrf-token` header value are **different strings** (standard double-submit pattern: cookie carries the secret, header carries the public token). Both are minted in the same `GET /api/me` round-trip.
- **Request body (verbatim from `main.affa1ba3.js`):**
  ```graphql
  query GetListings($resultLimit: Int, $pageOffset: Int, $orderBy: ListingSortOrder, $filter: ListingFilter) {
    listings(resultLimit: $resultLimit, pageOffset: $pageOffset, orderBy: $orderBy, filter: $filter) {
      id
      uuid
      landlord { companyName firstName lastName email phone }
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
  ```
  Variables (defaults match the live frontend at `resultLimit: 25`):
  ```json
  {
    "resultLimit": 25,
    "pageOffset": 0,
    "orderBy": "MOST_RECENT",
    "filter": {}
  }
  ```
- **Response shape:** `{"data": {"listings": [ {…}, … ]}}`. **Flat array**, no `total` / `items` / `pageInfo` wrapper. Use the sibling `GetNumberOfListings` query (below) when you need a count.
- **Total-count query** (verified):
  ```graphql
  query GetNumberOfListings($filter: ListingFilter) {
    numberOfListings(filter: $filter)
  }
  ```
  Response: `{"data": {"numberOfListings": 167}}`. Issue alongside `GetListings` (the frontend does so on every page load).
- **Pagination:** integer-offset. The frontend defaults to `resultLimit: 25` and exposes 10/25/50 in the per-page selector. Set `pageOffset = page_index * resultLimit` (page-index is 0-based — i.e. `pageOffset` is the absolute item offset, not the page number).
- **Sort:** `orderBy` is a single `ListingSortOrder` **string enum**, not a `{field, direction}` object. Verified members: `LAST_MODIFIED | MOST_RECENT | RENT_ASCENDING | RENT_DESCENDING`.
- **WG-vs-flat filter:** the discriminator is `type` (`ListingType` enum). Verified members: `APARTMENT | HOUSE | SHARED_APARTMENT`. Pass `filter: {"type": "SHARED_APARTMENT"}` for WG rooms or `filter: {"type": "APARTMENT"}` for full flats. Omitting `type` returns both. Note the field name in the filter is `type` (singular); attempting `types` triggers a server error (`Field "types" is not defined by type "ListingFilter". Did you mean "type" or "tags"?`).
- **Pseudocode:**
  ```python
  async with httpx.AsyncClient(base_url="https://living.tum.de", headers={"User-Agent": USER_AGENT}) as client:
      csrf = (await client.get("/api/me")).json()["csrf"]
      client.headers["csrf-token"] = csrf
      page_size = 25
      for page in range(max_pages):
          resp = await client.post(
              "/graphql",
              json={
                  "operationName": "GetListings",
                  "variables": {
                      "resultLimit": page_size,
                      "pageOffset": page * page_size,
                      "orderBy": "MOST_RECENT",
                      "filter": {},  # or {"type": "SHARED_APARTMENT"}
                  },
                  "query": LISTINGS_QUERY,
              },
          )
          for item in resp.json()["data"]["listings"]:
              yield parse_stub(item)
  ```

## How to read one listing (detail)

- **HTTP request:** `POST https://living.tum.de/graphql` (same endpoint, same CSRF cookie+header pair).
- **Request body (verbatim, anonymous variant):**
  ```graphql
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
  ```
  Variables: `{"uuid": "cf76dd26-0bbb-45af-b74d-14f5face8ba0"}`. The fragment can also include `totalRent` (verified extra) — the server returns the warm-rent sum (`rent + incidentalCosts`).
- **Why `WithoutContactInfo`:** the alternate `GetListingByUUID` query also accepts a `landlordContactInfo` fragment, but the server returns `landlord: null` for anonymous callers either way. The `WithoutContactInfo` variant is the one we use because it omits the unused fragment and avoids a non-result.
- **Response shape:** `{"data": {"listingByUUID": { … }}}`. The free-text "description" is stored as **`furtherEquipment`** (German) and **`furtherEquipmentEn`** (English). There is no top-level `description` field — the only `description` / `descriptionEn` fields are per-image captions.
- **Field-by-field mapping into domain `Listing`** (every row verified by inspecting the live response for `id=691` / uuid `cf76dd26-…`):

| `Listing` field       | Source on TUM Living GraphQL response                                                                                       |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `id`                  | `f"tum-living:{listingByUUID.uuid}"`                                                                                        |
| `url`                 | `f"https://living.tum.de/listings/{listingByUUID.uuid}/view"`                                                               |
| `title`               | **not exposed by API** — TUM Living has no listing title field. Synthesise (e.g. `f"{type} · {numberOfRooms}R · {city}"`).  |
| `price_eur`           | `listingByUUID.totalRent` (warm rent = `rent + incidentalCosts`, in €). Add `totalRent` to the fragment to fetch it.        |
| `size_m2`             | `listingByUUID.squareMeter` (number, m²)                                                                                    |
| `wg_size`             | **not exposed by API.** `numberOfRooms` on a `SHARED_APARTMENT` is the rooms-in-the-offered-share, not flatmate count. Leave `None`. |
| `address`             | `listingByUUID.street + " " + listingByUUID.houseNumber` (both top-level; not nested under any `address` object)            |
| `city`                | `listingByUUID.city` (string, e.g. `"München"`, `"Karlsfeld"` — free-text, not the `tumLocation` enum)                      |
| `district`            | `listingByUUID.district` (enum-style, e.g. `"UNTERGIESING_HARLACHING"` or `null`)                                            |
| `lat`                 | `listingByUUID.coordinates.x` (latitude)                                                                                    |
| `lng`                 | `listingByUUID.coordinates.y` (longitude)                                                                                   |
| `available_from`      | `listingByUUID.availableFrom` (ISO 8601 datetime string → `date`)                                                           |
| `available_to`        | `listingByUUID.availableUntil` (ISO 8601 datetime string or `null` for unlimited). Note: the field is `availableUntil`, not `availableTo`. |
| `description`         | `listingByUUID.furtherEquipmentEn` (English) or `listingByUUID.furtherEquipment` (German). Free text, no HTML. **⚠ Storage truncation:** `ListingRow.description` is currently `VARCHAR(255)` (SQLModel default for bare `Optional[str]`); TUM Living `furtherEquipment*` bodies are routinely 1–4 KB and will silently truncate. Fix is step 1 of [`../../../docs/MULTI_SOURCE_SCRAPER_PLAN.md`](../../../docs/MULTI_SOURCE_SCRAPER_PLAN.md) (widen to `TEXT` + force re-scrape) and **must land before** any tum-living writes. |
| `photo_urls`          | `[f"https://living.tum.de/api/image/{img['id']}/1280" for img in listingByUUID.images]` (cap at 12; sort `isPreview` first) |
| `cover_photo_url`     | `f"https://living.tum.de/api/image/{listingByUUID.images[i]['id']}/1280"` for the image where `isPreview is True`, fallback `images[0]` |
| `furnished`           | `"FURNISHED" in listingByUUID.tags` → `True`; `"PARTLY_FURNISHED" in tags` → `True`; else `None` (don't infer `False` from absence) |
| `pets_allowed`        | `"PETS_ALLOWED" in listingByUUID.tags` → `True`; else `None`                                                                |
| `smoking_ok`          | `"SMOKING" in listingByUUID.tags` → `True`; else `None`                                                                     |
| `languages`           | **not exposed by API**; leave `[]`                                                                                          |
| `online_viewing`      | **not exposed by API**; leave `False`                                                                                       |
| `kind` (new field)    | `'wg'` if `listingByUUID.type == "SHARED_APARTMENT"`, else `'flat'` (covers `APARTMENT` and `HOUSE`)                        |

Verified `tags` enum members (a partial list, harvested from the bundle and live samples; treat as denylist-by-absence): `FURNISHED, PARTLY_FURNISHED, BATHTUB, SHOWER, GUEST_TOILET, WASHING_MACHINE, DISHWASHER, TERRACE, BALCONY, GARDEN, CELLAR, LIFT, PETS_ALLOWED, BICYCLE_CELLAR, ATTIC, BARRIER_FREE, FITTED_KITCHEN, FAMILY_FRIENDLY, SMOKING, FLAT_SHARING_POSSIBLE, PARKING_SPACE`.

Additional fields TUM Living **does** provide that wg-gesucht doesn't (verified):

- `rent` (Kaltmiete, base rent without ancillaries)
- `totalRent` (Warmmiete, what we map to `price_eur`)
- `incidentalCosts` (Nebenkosten flat) and `incidentalCostsTypes` (string enum array, e.g. `["CARETAKER", "HEATING_COSTS", "WASTE_COLLECTION", "WATER_COSTS"]`)
- `incidentalCostsCustomLabel` (free text, e.g. `"STROM/GASabschlag"`)
- `oneTimeCosts` + `oneTimeCostsLabel` (Abschlag)
- `deposit` (Kaution, in €)
- `parkingSpace` (bool) + `parkingSpaceCosts` (number)
- `floor` (Stockwerk, number — fractional values like `1.5` for mezzanine are allowed)
- `housingType` (building/floor enum: `APARTMENT | ATTIC | BASEMENT | GROUND_FLOOR | MEZZANINE`) — distinct from the WG-vs-flat `type`
- `tumLocation` (city enum: `MUNICH | GARCHING | FREISING | HEILBRONN | STRAUBING | GARMISCH_PARTENKIRCHEN`)
- Seven `seekingX` booleans for target groups (`seekingStudents`, `seekingProfessors`, `seekingIncomings`, `seekingDoctoralStudents`, `seekingPostDoctoralStudents`, `seekingGuestResearchers`, `seekingTumEmployees`) — TUM Living does **not** ship a `targetGroups` array; it's seven separate booleans.
- `verifiedAt`, `publicationDate`, `expirationDate`, `createdAt`, `modifiedAt` (all ISO 8601 datetime strings)
- `isActive`, `isListingPublic` (booleans)
- `landlord.companyName / firstName / lastName / email / phone` — exposed publicly via `GetListings` (anonymous), nulled by the server on `GetListingByUUID*`. We keep using `WithoutContactInfo` and avoid scraping landlord PII.

These can be persisted in a future `ListingRow.extras` JSON column or ignored if the evaluator doesn't need them.

## Anti-bot posture

- **Cookies / CSRF / auth required?** The CSRF double-submit pair is required (cookie `csrf-token=<secret>` set by `GET /api/me`, plus the header `csrf-token: <token-from-/api/me-body>` on every POST). Without the pair the server returns `{"errors":[{"message":"invalid csrf token","code":"EBADCSRFTOKEN"}],"data":null}`. No login token is required.
- **Pacing recommendation:** 2–3 seconds between requests. The active corpus is small (~167 listings on 2026-04-18) — at 25/page that's ~7 paginated requests per pass; refresh detail every 48 h to keep traffic low.
- **Captcha / WAF observations:** none. `robots.txt` is `User-agent: * / Disallow:` (full allow). Server is `nginx/1.26.3` fronting `Express`; no Cloudflare.
- **Headers to send:**
  ```
  User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36
  Accept: application/json
  Accept-Language: en-US,en;q=0.9
  Content-Type: application/json
  csrf-token: <token-from-/api/me-body>
  Cookie: csrf-token=<cookie-from-/api/me-Set-Cookie>
  ```
  `User-Agent` should be a real browser string (not strictly enforced — `TestAgent` worked in our recon — but a real string is what the frontend sends). `Accept-Language` can be `en-US` or `de-DE`; the API responds bilingually (German + `descriptionEn` / `furtherEquipmentEn` siblings).

## Open questions / TODO

- [ ] **Schema prerequisite: widen `ListingRow.description` to `TEXT`.** TUM Living's `furtherEquipmentEn` is routinely 1–4 KB; current `VARCHAR(255)` will silently truncate on write. Sequenced as step 1 of [`../../../docs/MULTI_SOURCE_SCRAPER_PLAN.md`](../../../docs/MULTI_SOURCE_SCRAPER_PLAN.md). Must land before any tum-living writes.
- [ ] **`wg_size` semantics for cross-source parity.** `numberOfRooms` on a `SHARED_APARTMENT` listing is the rooms-in-the-offered-share (we saw `numberOfRooms=1, squareMeter=55` for a real WG room); flatmate count is not exposed. Decide whether to leave `wg_size=None` for tum-living rows (current plan) or to harvest it from the description text. Same convention question as `kleinanzeigen` — coordinate via the evaluator (`wg_size_fit` in `evaluator.py`).
- [ ] **CSRF lifetime.** We mint a fresh CSRF pair via `GET /api/me` at the start of each `httpx.AsyncClient` lifetime. Whether the same pair survives across many minutes / hours of POSTs has not been measured; if we ever see `EBADCSRFTOKEN` mid-pass, mint a new pair and retry. The `/api/me` round-trip is cheap.
- [ ] **Schema evolution.** TUM Living is an active TUM project; the GraphQL schema can change without notice. Pin the verified queries (this doc) in the scraper module as constants and add a smoke test that asserts the response keys (`uuid`, `type`, `coordinates.x`, …) are present so a schema break fails fast at scrape time rather than at evaluation time.
- [ ] **Landlord PII policy.** `GetListings` returns landlord `email` and `phone` to anonymous callers (verified). We currently never persist landlord contact info, but if a future evaluator wants to use it, we need a policy decision (privacy + GDPR) before storing those fields.

## Verified end-to-end recipe

Anonymous, read-only, no Playwright. Mints a CSRF pair, fetches one page of listings, fetches one listing's detail, and downloads one full-size image. Verified to run as written on 2026-04-18 with `httpx==0.28.1`.

```python
import asyncio

import httpx

BASE_URL = "https://living.tum.de"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

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


async def main() -> None:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=20.0) as client:
        me_resp = await client.get("/api/me")
        csrf_token = me_resp.json()["csrf"]
        client.headers["csrf-token"] = csrf_token

        listings_resp = await client.post(
            "/graphql",
            json={
                "operationName": "GetListings",
                "variables": {
                    "resultLimit": 5,
                    "pageOffset": 0,
                    "orderBy": "MOST_RECENT",
                    "filter": {},
                },
                "query": LISTINGS_QUERY,
            },
        )
        listings_resp.raise_for_status()
        listings = listings_resp.json()["data"]["listings"]
        print(f"Fetched {len(listings)} listings")

        first_uuid = listings[0]["uuid"]
        detail_resp = await client.post(
            "/graphql",
            json={
                "operationName": "GetListingByUUIDWithoutContactInfo",
                "variables": {"uuid": first_uuid},
                "query": DETAIL_QUERY,
            },
        )
        detail_resp.raise_for_status()
        listing = detail_resp.json()["data"]["listingByUUID"]
        print(
            f"Detail uuid={listing['uuid']} type={listing['type']} "
            f"city={listing['city']} totalRent={listing['totalRent']} "
            f"coords=({listing['coordinates']['x']}, {listing['coordinates']['y']}) "
            f"images={len(listing['images'])}"
        )

        if listing["images"]:
            image_id = listing["images"][0]["id"]
            image_resp = await client.get(f"/api/image/{image_id}/1280")
            image_resp.raise_for_status()
            print(
                f"Image id={image_id} bytes={len(image_resp.content)} "
                f"content-type={image_resp.headers.get('content-type')}"
            )


if __name__ == "__main__":
    asyncio.run(main())
```

Sample output (2026-04-18):

```
Fetched 5 listings
Detail uuid=cf76dd26-0bbb-45af-b74d-14f5face8ba0 type=APARTMENT city=München totalRent=1700 coords=(48.1184617, 11.5707928) images=10
Image id=5400 bytes=120528 content-type=image/jpg
```

## See also

- [`./README.md`](./README.md) — multi-source contract (id namespacing + `kind` column).
- [`./SOURCE_WG_GESUCHT.md`](./SOURCE_WG_GESUCHT.md) — sibling source recon (template style).
- [`../../../docs/WG_GESUCHT.md`](../../../docs/WG_GESUCHT.md) — wg-gesucht recon (for comparison: TUM Living is a cleaner GraphQL API but smaller corpus).
- [`../../../context/TUM_SYSTEMS.md`](../../../context/TUM_SYSTEMS.md) — TUM API + scraping notes (may have additional context on TUM Living architecture).
