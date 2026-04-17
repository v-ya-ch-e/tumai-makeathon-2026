# TUM Systems and Access Methods

> Mirror of [`tum_systems.md`](https://github.com/DataReply/makeathon/blob/main/tum_systems.md) from `DataReply/makeathon`. Authoritative reference for interacting with TUM's digital ecosystem from your agent.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Moodle (moodle.tum.de)](#moodle-moodletumde)
- [Collab Wiki (collab.dvb.bayern)](#collab-wiki-collabdvbbayern)
- [Mensa and StuCafé Menus](#mensa-and-stucafé-menus)
- [TUMonline (Courses, Schedules, Exams)](#tumonline-courses-schedules-exams)
- [Matrix (Chat & Messaging)](#matrix-chat--messaging)
- [Navigatum (Campus Navigation & Maps)](#navigatum-campus-navigation--maps)
- [Public Transport (MVV / MVG)](#public-transport-mvv--mvg)
- [General Web Scraping Tools](#general-web-scraping-tools)
- [Community & Resources](#community--resources)

## Code of Conduct

When building applications or scripts that interact with university-provided systems, you **must act responsibly**:

- **Do No Harm:** Do not perform actions that could overwhelm or DDoS university infrastructure.
- **Rate Limits:** Always rigorously rate-limit your API requests and web scraping scripts.
- **Data Privacy:** Never expose your personal setup, credentials, Access Tokens, or private student data in public Git repositories.

## Moodle (moodle.tum.de)

Moodle is the primary learning management system for course materials, assignments, and announcements.

**Access Method:**

- **API Availability:** Currently, there is no official, publicly accessible API available for students to access TUM's Moodle.
- **Workaround:** Browser automation tools are often used. They automate the TUM Single Sign-On (SSO) login, navigate the platform, and extract course data or materials from the DOM. See [General Web Scraping Tools](#general-web-scraping-tools) (Playwright, Selenium).

## Collab Wiki (collab.dvb.bayern)

The Collab Wiki is a Confluence-based wiki system used for collaborative documentation and projects across Bavarian universities.

**Access Method:**

- **API Availability:** Based on Atlassian Confluence, it exposes the standard Confluence REST API.
- **Authentication (Personal Access Token):** Prefer PAT over password.
  - Login → **Profile picture → Settings → Personal Access Tokens**, or directly: [Create Token](https://collab.dvb.bayern/plugins/personalaccesstokens/usertokens.action).
- **Python Integration:** Recommended library is `atlassian-python-api`.

  ```bash
  pip install atlassian-python-api
  ```

  ```python
  from atlassian import Confluence

  confluence = Confluence(
      url='https://collab.dvb.bayern',
      token='your_personal_access_token'
  )
  ```

  Docs: [atlassian-python-api ReadTheDocs](https://atlassian-python-api.readthedocs.io/) · GitHub: [atlassian-api/atlassian-python-api](https://github.com/atlassian-api/atlassian-python-api).

## Mensa and StuCafé Menus

Daily menus from the Munich Student Union canteens (Mensa, StuCafé, StuBistro).

- **TUM-Dev "Eat API":** Community-driven static JSON API for TUM locations.
  - Endpoint format: `https://tum-dev.github.io/eat-api/<location>/<year>/<week>.json` (e.g., `.../mensa-garching/2023/45.json`).
  - Docs: [TUM-Dev Eat API](https://tum-dev.github.io/eat-api/docs/) · GitHub: [TUM-Dev/eat-api](https://github.com/TUM-Dev/eat-api).
- **OpenMensa API:** Centralized cross-Germany canteen aggregator.
  - Docs: [OpenMensa API v2](https://openmensa.org/api/v2/).

## TUMonline (Courses, Schedules, Exams)

TUMonline is the overarching campus management system for course registration, schedules, and exams.

- **API Availability:** The TUM School of Natural Sciences maintains a public API covering courses, schedules, people, and rooms.
  - Swagger/OpenAPI: [TUM NAT API](https://api.srv.nat.tum.de/docs).
- **Alternative:** Browser automation / scraping (Playwright, Scrapy, BeautifulSoup) for data the API doesn't expose.
- **IMPORTANT — Use the Demo Environment:** When writing/testing scripts, **DO NOT** use live TUMonline. Use the shadow copy at `demo.campus.tum.de` so automated actions (e.g., registering/deregistering) won't affect your real student data.

## Matrix (Chat & Messaging)

TUM provides a Matrix server for secure, federated chat. Highly suitable for integrations and chatbots.

- **API & Integration:** Standard Matrix APIs. Python libraries: `matrix-nio`, `simplematrixbotlib`.
- **Setup:** [TUM Matrix Service Setup Guide](https://wiki.ito.cit.tum.de/bin/view/CIT/ITO/Docs/Services/Matrix/Einrichtung/).

### Getting a Matrix Access Token

**WARNING:** Do **NOT** use tokens from a standard client (like Element) — those are short-lived and support `refresh_token: true`. They will expire.

To get a **long-lived token**, call the login API directly (adjust server URL + username/password):

```bash
curl --header "Content-Type: application/json" \
     --request POST \
     --data '{"password": "YOUR_PASSWORD", "type": "m.login.password", "identifier": {"type": "m.id.user", "user": "YOUR_USERNAME"}}' \
     https://matrix.org/_matrix/client/v3/login
```

Response:

```json
{
  "user_id": "@YOUR_USERNAME:matrix.org",
  "access_token": "...",
  "home_server": "matrix.org",
  "device_id": "???",
  "well_known": {
    "m.homeserver": { "base_url": "https://matrix-client.matrix.org/" }
  }
}
```

## Navigatum (Campus Navigation & Maps)

Navigatum (`nav.tum.de`) is the official interactive map system for TUM campuses — routing, room lookups, buildings.

- **API Availability:** Open REST API (Swagger).
  - [Navigatum Locations API](https://nav.tum.de/api#tag/locations/operation/get_handler).

## Public Transport (MVV / MVG)

Live departure times, transit schedules, and routing around TUM campuses (Munich, Garching, Weihenstephan).

- **API Availability:** MVV (Münchner Verkehrs- und Tarifverbund) offers access via the standard TRIAS interface.
  - [MVV TRIAS Interface for Developers](https://www.mvv-muenchen.de/fahrplanauskunft/fuer-entwickler/trias-schnittstelle/index.html).
- **Python (recommended):** `mvg` wraps the MVG/MVV API.

  ```bash
  pip install mvg
  ```

  PyPI: [`mvg`](https://pypi.org/project/mvg/).

## General Web Scraping Tools

If an API is missing or incomplete, build your own scraper — while respecting the [Code of Conduct](#code-of-conduct).

- **[BeautifulSoup (Python)](https://www.crummy.com/software/BeautifulSoup/bs4/doc/):** Standard for static HTML. Pair with `requests`.
- **[Playwright (Python / Node.js)](https://playwright.dev/):** Modern browser automation — best for SSO login walls and JS-heavy apps.
- **[Selenium (Python / Node.js)](https://www.selenium.dev/):** Robust industry alternative to Playwright.
- **[Scrapy (Python)](https://scrapy.org/):** Best when crawling hundreds/thousands of pages. Configure `DOWNLOAD_DELAY` to respect servers.

## Community & Resources

- **[TUM-Dev](https://www.tum.dev/):** Active community of developers building software for students. Maintains core ecosystem apps (including the Eat API). Great for help during hackathons.
- **[tum.sexy](https://tum.sexy/):** Community-maintained link directory pointing to many useful, open-source, or hidden TUM platforms and services. (Disclaimer: many linked projects are CS/Informatics-focused.) Excellent for discovering existing APIs and projects.
