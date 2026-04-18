# WG-Gesucht — agent playbook

> Notes gathered while building the autonomous room-hunter agent. Keep updated: selectors on wg-gesucht.de break often.

## 1. URL schema

Everything on the site is built around **stable, predictable URLs**. We do not need an API.

### Search (listings) — `/wg-zimmer-in-<City>.<cityId>.<categoryId>.<rentType>.<page>.html`

- `<City>` is the URL-slugified city name (`Muenchen`, `Berlin`, `Hamburg`, `Muenster` …). Umlauts become `ae/oe/ue`.
- `<cityId>` is the integer city id. Confirmed: München=`90`, Berlin=`8`, Hamburg=`55`, Frankfurt=`41`. You can discover more by visiting `https://www.wg-gesucht.de/wg-zimmer.html` and watching the city autocomplete API (`/ajax/staedte.php?query=...`).
- `<categoryId>` — **`0`** (WG room) is the default we use. Other values exist for 1-room flats, apartments, houses, but the challenge is "room".
- `<rentType>` — **`1`** (unlimited), `2` (temporary), `3` (overnight). We default to `1` (`unbefristet`).
- `<page>` — 0-indexed pagination (so page `0` = first page).

Example (München, WG-room, unbefristet, page 0):

```
https://www.wg-gesucht.de/wg-zimmer-in-Muenchen.90.0.1.0.html
```

### Filters are query-string, appended after the URL

Handy parameters confirmed by probing the server:

| Param         | Meaning                                                                                  | Example            |
| ------------- | ---------------------------------------------------------------------------------------- | ------------------ |
| `rMax`        | Maximum total rent in €.                                                                 | `rMax=700`         |
| `rMin`        | Minimum total rent in €.                                                                 | `rMin=300`         |
| `sMin`        | Minimum room size in m².                                                                 | `sMin=12`          |
| `sMax`        | Maximum room size in m².                                                                 | `sMax=40`          |
| `wgSea`       | WG size (flatmates). `2` = "2er WG" and up, up to `7`.                                   | `wgSea=2`          |
| `furnishedSea`| `1` = furnished, `2` = unfurnished.                                                      | `furnishedSea=1`   |
| `dFr`         | Available from (`DD.MM.YYYY`).                                                           | `dFr=01.05.2026`   |
| `dTo`         | Available until (if temporary).                                                          | `dTo=30.09.2026`   |

**⚠️ Gotchas (confirmed 2026-04)**:

- `offer_filter=1` (the param the browser UI appears to use when the "apply filters" button is clicked) triggers a **301 redirect to a malformed URL** (the category segment gets dropped: `/…-Muenchen.90.0.1.0.html` → `/…-Muenchen.90..1.0.html`) which then 404s. **Never send it.**
- `city_id` also appears to cause the same bad redirect in some combinations. Skip it.
- Just use the numeric `rMax`, `rMin`, `sMin`, `sMax`, `wgSea`, `furnishedSea`, `dFr`, `dTo` params — those are honored cleanly.

Because of these quirks, the robust strategy is: pass a **small, safe filter set** in the URL, and then apply the fine-grained match scoring server-side in the scorecard [`evaluator`](../backend/app/wg_agent/evaluator.py) (deterministic components + one narrow `brain.vibe_score` LLM call; see ADR-015).

### Canonical listing URL

Every listing has a **short canonical URL** (visible at the bottom of each search result):

```
https://www.wg-gesucht.de/<listingId>.html          # canonical (redirects)
https://www.wg-gesucht.de/wg-zimmer-in-<City>-<Bezirk>.<listingId>.html   # long form
```

Use the listing id (`13115694`) as the identity key everywhere.

## 2. Search-result DOM (confirmed 2026-04)

Listings render inside a JSON-hydrated React app now, but a server-rendered HTML list still exists for SEO. Relevant selectors:

- Each listing card: `div.wgg_card.offer_list_item` (and `article.offer_list_item` as a fallback).
- Card id: `div.wgg_card[data-id="13115694"]` (so we can dedupe).
- Title anchor: `h3 a` — gives both the title text and the long URL.
- Price + size: `div.row.middle .col-xs-3 b` (first is `"995 €"`, second is `"14 m²"`).
- Address: the card's second line `div.col-sm-6` has `"<N>er WG | München Ramersdorf-Perlach | Fritz-Erler-Straße 32"`.
- Availability: `div.row .text-right` contains `"Verfügbar: 01.05.2026"`.
- Short link: bottom `a[href^="https://www.wg-gesucht.de/"]` with the pattern `/<id>.html`.

Because the DOM changes, **we parse defensively with BeautifulSoup**: find every anchor that matches `r"/(\d{5,8})\.html"` and walk up to the nearest `wgg_card` or `article`, then extract numbers via regex.

## 3. Listing page

Canonical URL `https://www.wg-gesucht.de/<id>.html` renders:

- `<h1>` — listing title.
- Address block (`Adresse`): street + postal code + Bezirk.
- Cost table (`## Kosten`): `Miete`, `Nebenkosten`, `Sonstige Kosten`, `Kaution`, `Ablösevereinbarung`.
- Availability table (`## Verfügbarkeit`): `frei ab`, `frei bis`.
- Long description `<div id="ad_description_text">` wrapping ordered freitext tabs (`#freitext_0..3` = Zimmer / Lage / WG-Leben / Sonstiges); bilingual in Munich.
- WG-Details: flatmates, ages, smoking, pets, spoken languages.
- **Contact button** (only when logged-in): green "Nachricht senden" button → links to `/nachricht-senden/<listingId>,<offerType>,<deactivated>.html`.

We scrape the listing page once per new listing to get the real description (the card text is truncated).

### Stable DOM anchors

[`parse_listing_page`](../backend/app/wg_agent/browser.py) prefers these anchors over `get_text` regex — the site ships a very stable pattern:

| Field(s) | Anchor |
| --- | --- |
| `price_eur`, `Nebenkosten`, `Kaution`, etc. | `<h2>Kosten</h2>`, then rows of `span.section_panel_detail` + sibling `span.section_panel_value` inside `div.row` until the next `<h2>`. |
| `available_from`, `available_to` | `<h2>Verfügbarkeit</h2>`, same label/value row shape. |
| `address`, `postal_code`, `city`, `district` | `<h2>Adresse</h2>` → its `col-sm-6` wrapper → first `.section_panel_detail` (two lines: `"Straße Nr"` then `"<PLZ> <City> <District>"`). |
| `languages`, `pets_allowed`, `smoking_ok` | `<h2>WG-Details</h2>` → `panel.panel` → `li` rows, one signal per line. |
| `furnished` | Same WG-Details `<li>`s, plus `div.utility_icons > div.text-center` quick-facts tiles. Negations (`nicht`, `un-`, `teilweise`) are colocated on short lines, so a same-line check is reliable. |
| `lat`, `lng` | The map snippet at the bottom of the page ships `var map_config = { ... markers: [{"lat":48.09,"lng":11.64,...}] }`. A tight regex in `browser._parse_map_lat_lng` reads the first marker; no external API call. |

Every DOM path degrades to the pre-existing full-text regex if an anchor goes missing so the parser never returns `None` for a field the page actually has.

## 4. Login

- URL: `https://www.wg-gesucht.de/mein-wg-gesucht.html`. Not logged in → full marketing page. Logged in → dashboard.
- Login modal is opened via the `#login-link` button or by the path `/login.html`.
- Form: `input[name="login_email_username"]`, `input[name="login_password"]`, submit `button[name="login_submit"]`.
- Sets cookies: `X-Client-Id`, `user_id`, `wgg_sid`, plus several CSRF tokens.
- **Rate / CAPTCHA**: after ~3 failed logins the site shows a CAPTCHA. For the agent we strongly prefer logging in **once manually** and reusing the cookies (`storage_state.json`).

Our driver supports both modes:

1. If `WG_STATE_FILE` exists and is non-empty → launch Playwright with `storage_state=<file>`, verify session by GET-ing `/mein-wg-gesucht.html` and looking for `data-user-id`.
2. Else fall back to filling the form with `WG_USERNAME` / `WG_PASSWORD` and saving the state to `WG_STATE_FILE` for next run.

## 5. Sending a message to the landlord

The "Nachricht senden" button on the listing page opens a full page (not a modal, as of April 2026):

```
/nachricht-senden/<listingId>,<offerType>,<deactivated>.html
```

The form fields (confirmed by DOM inspection after login):

- `textarea[name="message"]` — the message body.
- `input[name="user_salutation"]` — salutation (optional, defaults to profile).
- `input[name="anrede"]` — gender (`0` female, `1` male, `2` divers).
- `input[name="verfuegbar_ab"]`, `input[name="verfuegbar_bis"]` — date range (dd.mm.yyyy).
- `input[type="submit"][name="send_message_offer"]` — submit.

On success the page redirects to `/nachrichten-senden.html?...` with a green "Nachricht erfolgreich versendet" banner. On failure the page re-renders with a red alert containing `class="alert alert-danger"`.

Important: wg-gesucht **rate-limits messages**. Free accounts can send only ~3–5 messages/day before a soft block ("zu viele Nachrichten") appears. The agent **paces itself** to one message every 30-60s and stops after N sends (configurable, default 5).

## 6. Reading replies (inbox)

Inbox URL: `https://www.wg-gesucht.de/nachrichten-lesen.html` (requires login).

- Each thread is `a.mailbox_thread_unread` or `a.mailbox_thread_read` with a `data-conversation-id`.
- Clicking opens `/nachrichten-lesen.html?conv_id=<convId>` which renders the conversation.
- Messages are `<div class="message-container">` with `.message-sender-name`, `.message-body`, `.message-time`.

Polling strategy: every 45s GET the inbox, diff with last-seen conversation ids + message timestamps, feed new messages to the classifier agent.

## 7. Anti-bot: what to avoid, what works

- **User agent**: use a real Chrome UA (Playwright's default already is one).
- **Headless detection**: wg-gesucht runs Cloudflare Turnstile on some endpoints. Launching with `headless=False` works reliably for demos; `headless=True` with `playwright-stealth` works for most search pages but sometimes fails on login.
- **Rate**: `page.goto(url, wait_until="domcontentloaded")` + random 2–4s sleeps between actions.
- **Navigation pattern**: always land on the homepage once per session before navigating to a listing. Going straight to `/nachricht-senden/...` from a cold session triggers a CAPTCHA more often.
- **Cookies first**: once logged in, dump `context.storage_state()` and reuse it for all subsequent runs. A fresh login on every run is what gets us soft-blocked.

## 8. Data we extract per listing

```text
id           -> "13115694"
url          -> "https://www.wg-gesucht.de/13115694.html"
title        -> "AVAILABLE ROOM in internationaler 8er WG..."
city         -> "München"
district     -> "Ramersdorf-Perlach"
address      -> "Fritz-Erler-Straße 32"
price_eur    -> 995
size_m2      -> 14.0
wg_size      -> 8
available_from -> 2026-05-01
available_to   -> 2026-10-31   (or None if unlimited)
description  -> "<full bilingual text>"
languages    -> ["Deutsch", "Englisch"]
furnished    -> true
pets_allowed -> false
smoking_ok   -> false
```

All of the above are scraped with BeautifulSoup from the listing HTML, using tolerant regex + label lookups so that missing fields gracefully degrade to `None`.

## 9. Legal / ethical guardrails

wg-gesucht's ToS forbid automated scraping, so this agent is strictly a **hackathon demo**. We:

- Default to **≤1 message/30s** pacing.
- Default to a **hard cap of 5 messages per run** (`WG_MAX_MESSAGES`).
- Never auto-accept legally binding rental offers — the agent only goes as far as "proposing a viewing appointment" (a non-binding, conventional step).
- Surface **every action** to the operator via the action log (SSE), so the demo judge always knows exactly what the agent just did.

*Originally at `backend/app/wg_agent/WG_GESUCHT.md`. Moved here as part of docs consolidation.*
