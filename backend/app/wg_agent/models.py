"""Pydantic data models for the WG-Gesucht hunter agent."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, EmailStr, Field, HttpUrl


# --- City catalogue -----------------------------------------------------------

# Mapping of a few common German cities to their wg-gesucht ids + URL slugs.
# Extend as needed; unknown cities default to Munich.
CITY_CATALOGUE: dict[str, tuple[int, str]] = {
    "Muenchen": (90, "Muenchen"),
    "München": (90, "Muenchen"),
    "Berlin": (8, "Berlin"),
    "Hamburg": (55, "Hamburg"),
    "Frankfurt": (41, "Frankfurt-am-Main"),
    "Frankfurt am Main": (41, "Frankfurt-am-Main"),
    "Köln": (73, "Koeln"),
    "Koeln": (73, "Koeln"),
    "Stuttgart": (124, "Stuttgart"),
    "Leipzig": (77, "Leipzig"),
}


# --- Input: what the student wants --------------------------------------------

class Gender(str, Enum):
    female = "female"
    male = "male"
    diverse = "diverse"
    prefer_not_to_say = "prefer_not_to_say"


class RentType(int, Enum):
    unlimited = 1
    temporary = 2
    overnight = 3


class PlaceLocation(BaseModel):
    """A user-picked location anchored by a Google Places place_id.

    Produced by the frontend's Places Autocomplete widget and stored as
    JSON in `SearchProfileRow.main_locations`. The lat/lng pair is what
    commute-based scoring consumes; `label` is what we show to the user
    and the LLM. `max_commute_minutes` is an optional per-location
    budget the scorer treats as a soft upper bound.
    """

    label: str
    place_id: str
    lat: float
    lng: float
    max_commute_minutes: Optional[int] = Field(default=None, ge=5, le=240)


class PreferenceWeight(BaseModel):
    """One preference tag plus how important it is to the user.

    `key` matches the UI tile id (e.g. 'gym', 'furnished'). `weight`
    runs 1..5 where 5 is a hard filter and 1 is a mild bonus. Stored
    inside `SearchProfileRow.preferences` as `{key, weight}` objects.
    """

    key: str
    weight: int = Field(default=3, ge=1, le=5)


class NearbyPlace(BaseModel):
    """Nearest real-world place for a place-like preference.

    Produced from the Places API around a listing's coordinates and used
    by the evaluator to score preferences such as `gym`, `park`, or
    `supermarket` from actual distance instead of description keywords.
    `distance_m` is `None` when the lookup succeeded but no matching
    place was found inside the search radius.
    """

    key: str
    label: str
    searched: bool = True
    distance_m: Optional[int] = Field(default=None, ge=0)
    place_name: Optional[str] = None
    category: Optional[str] = None


class SearchProfile(BaseModel):
    """What kind of WG room the student is hunting for."""

    city: str = Field(..., description="City name, e.g. 'München'")
    max_rent_eur: int = Field(..., ge=100, le=3000, description="Max total rent in €/month")
    price_min_eur: int = Field(default=0, ge=0, le=3000)
    price_max_eur: Optional[int] = None
    main_locations: list[PlaceLocation] = Field(default_factory=list)
    has_car: bool = False
    has_bike: bool = False
    mode: Literal["wg", "flat", "both"] = "wg"
    preferences: list[PreferenceWeight] = Field(default_factory=list)
    rescan_interval_minutes: int = Field(default=30, ge=5, le=1440)
    schedule: Literal["one_shot", "periodic"] = "one_shot"
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    min_rent_eur: int = Field(default=0, ge=0, le=3000)
    min_size_m2: int = Field(default=10, ge=5, le=80)
    max_size_m2: int = Field(default=40, ge=5, le=200)
    rent_type: RentType = RentType.unlimited
    move_in_from: Optional[date] = None
    move_in_until: Optional[date] = None
    preferred_districts: list[str] = Field(
        default_factory=list, description="Districts the student prefers, e.g. ['Maxvorstadt', 'Schwabing']"
    )
    avoid_districts: list[str] = Field(default_factory=list)
    languages: list[str] = Field(
        default_factory=lambda: ["Deutsch", "Englisch"],
        description="Languages the student is comfortable in",
    )
    furnished: Optional[bool] = Field(
        default=None, description="None = no preference, True = must be furnished"
    )
    min_wg_size: int = Field(default=2, ge=1, le=12)
    max_wg_size: int = Field(default=8, ge=1, le=20)
    notes: str = Field(
        default="",
        description="Free-form notes: e.g. 'I'm a CS masters student at TUM, I like climbing.'",
    )

    max_listings_to_consider: int = Field(default=30, ge=1, le=100)
    max_messages_to_send: int = Field(default=5, ge=1, le=15)

    # Matcher v2 additions (MATCHER.md §2.1, §5.6, §3.4).
    # `desired_min_months` drives `tenancy_fit`; falls back to `RentType` when
    # left unset (`unlimited→12`, `temporary→3`, `overnight→1`) inside
    # `repo.get_search_profile`. `flatmate_self_*` are read by the LLM vibe
    # prompt to resolve the `wg_gender` / `wg_age_band` soft signals; `None`
    # means the user did not state a preference and the keys resolve to
    # `None` in the preference aggregator.
    desired_min_months: Optional[int] = Field(default=None, ge=1, le=60)
    flatmate_self_gender: Optional[Gender] = Field(default=None)
    flatmate_self_age: Optional[int] = Field(default=None, ge=16, le=99)


class WGCredentials(BaseModel):
    """Credentials for logging into wg-gesucht.de."""

    username: str = Field(..., description="wg-gesucht username or email")
    password: str = Field(..., description="wg-gesucht password")
    storage_state_path: Optional[str] = Field(
        default=None,
        description=(
            "Optional path to a Playwright storage_state.json. If present and valid, we "
            "skip the username/password login (safer against CAPTCHAs)."
        ),
    )


class UserProfile(BaseModel):
    """The local account entity: a unique username + basic demographics."""

    username: str = Field(..., min_length=1, description="Unique, user-chosen handle")
    email: Optional[EmailStr] = None
    age: int = Field(..., ge=16, le=99)
    gender: Gender = Gender.prefer_not_to_say
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ContactInfo(BaseModel):
    """Personal info the agent uses when drafting messages."""

    first_name: str
    last_name: str = ""
    age: int = Field(..., ge=16, le=99)
    gender: Gender = Gender.prefer_not_to_say
    email: EmailStr
    phone: str = ""
    occupation: str = Field(
        default="Student", description="e.g. 'MSc Informatics student at TUM'"
    )
    bio: str = Field(
        default="",
        description="Short paragraph the agent can quote verbatim in intro messages",
    )
    languages: list[str] = Field(default_factory=lambda: ["English", "German"])


# --- Output: what the agent found & did ---------------------------------------

class ComponentScore(BaseModel):
    """One factor in the scorecard evaluator output.

    Produced by `evaluator.py` (deterministic components plus the narrow
    LLM `vibe` component) and persisted on `ListingScoreRow.components`.
    The drawer renders one bar per component using `score`, `evidence`,
    and `missing_data`.
    """

    key: str
    score: float = Field(ge=0, le=1)
    weight: float = Field(ge=0)
    evidence: list[str] = Field(default_factory=list)
    hard_cap: Optional[float] = Field(default=None, ge=0, le=1)
    missing_data: bool = False


class Listing(BaseModel):
    """A single WG-Gesucht listing, normalized."""

    id: str
    url: HttpUrl
    title: str
    kind: Literal["wg", "flat"] = "wg"
    city: Optional[str] = None
    district: Optional[str] = None
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    price_eur: Optional[int] = None
    size_m2: Optional[float] = None
    wg_size: Optional[int] = None
    available_from: Optional[date] = None
    available_to: Optional[date] = None
    description: Optional[str] = None
    languages: list[str] = Field(default_factory=list)
    furnished: Optional[bool] = None
    pets_allowed: Optional[bool] = None
    smoking_ok: Optional[bool] = None
    online_viewing: bool = False
    photo_urls: list[str] = Field(default_factory=list)
    cover_photo_url: Optional[str] = None
    best_commute_minutes: Optional[int] = None
    best_commute_label: Optional[str] = None
    best_commute_mode: Optional[str] = None

    # Matcher v2 additions (MATCHER.md §2.2, §5.1, upfront_cost_fit).
    # `price_basis` records whether `price_eur` is total Warmmiete as-stated
    # ("warm"), uplifted from a Kaltmiete-only source ("kalt_uplift"), or
    # truly unknown ("unknown" — the safe default for legacy rows).
    price_basis: Optional[Literal["warm", "kalt_uplift", "unknown"]] = None
    deposit_months: Optional[float] = Field(default=None, ge=0, le=12)
    furniture_buyout_eur: Optional[int] = Field(default=None, ge=0)

    # Transient: populated by per-source search/detail parsers and consumed by
    # `ScraperAgent._is_fresh_enough` to drop stale listings before persistence.
    # NOT a `ListingRow` column — `repo.upsert_global_listing` ignores it.
    posted_at: Optional[datetime] = None

    # Populated by the evaluator after scoring.
    score: Optional[float] = Field(default=None, ge=0, le=1)
    score_reason: Optional[str] = None
    match_reasons: list[str] = Field(default_factory=list)
    mismatch_reasons: list[str] = Field(default_factory=list)
    components: list[ComponentScore] = Field(default_factory=list)
    veto_reason: Optional[str] = None


class MessageDirection(str, Enum):
    outbound = "outbound"  # from us to landlord
    inbound = "inbound"    # from landlord to us


class Message(BaseModel):
    direction: MessageDirection
    listing_id: str
    text: str
    sent_at: datetime = Field(default_factory=datetime.utcnow)


class ReplyIntent(str, Enum):
    """What the landlord's reply means, as classified by the LLM."""

    viewing_offer = "viewing_offer"      # Landlord proposes a viewing time
    asks_for_info = "asks_for_info"      # Landlord wants more info (age, occupation, …)
    polite_decline = "polite_decline"    # "Already taken", "doesn't fit", …
    already_taken = "already_taken"
    smalltalk = "smalltalk"
    unclear = "unclear"


class ReplyAnalysis(BaseModel):
    intent: ReplyIntent
    summary: str
    proposed_times: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    next_action: Literal[
        "accept_viewing",
        "answer_questions",
        "drop",
        "wait",
    ] = "wait"


class ActionKind(str, Enum):
    boot = "boot"
    login = "login"
    search = "search"
    scrape = "scrape"
    evaluate = "evaluate"
    draft_message = "draft_message"
    send_message = "send_message"
    dry_run_skip = "dry_run_skip"
    poll_inbox = "poll_inbox"
    classify_reply = "classify_reply"
    propose_viewing = "propose_viewing"
    rate_limit = "rate_limit"
    error = "error"
    done = "done"
    new_listing = "new_listing"
    rescan = "rescan"


class AgentAction(BaseModel):
    """One entry in the human-readable action log the agent streams to the UI."""

    at: datetime = Field(default_factory=datetime.utcnow)
    kind: ActionKind
    summary: str
    detail: Optional[str] = None
    listing_id: Optional[str] = None


class HuntStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class Hunt(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    status: HuntStatus = HuntStatus.pending
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    requirements: SearchProfile
    dry_run: bool = True
    listings: list[Listing] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    actions: list[AgentAction] = Field(default_factory=list)
    viewings: list[str] = Field(
        default_factory=list,
        description="Human-readable summaries of every viewing slot we successfully confirmed.",
    )
    error: Optional[str] = None
