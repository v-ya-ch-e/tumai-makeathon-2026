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


class SearchProfile(BaseModel):
    """What kind of WG room the student is hunting for."""

    city: str = Field(..., description="City name, e.g. 'München'")
    max_rent_eur: int = Field(..., ge=100, le=3000, description="Max total rent in €/month")
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

class Listing(BaseModel):
    """A single WG-Gesucht listing, normalized."""

    id: str
    url: HttpUrl
    title: str
    city: Optional[str] = None
    district: Optional[str] = None
    address: Optional[str] = None
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

    # Populated by the LLM after evaluation.
    score: Optional[float] = Field(default=None, ge=0, le=1)
    score_reason: Optional[str] = None
    match_reasons: list[str] = Field(default_factory=list)
    mismatch_reasons: list[str] = Field(default_factory=list)


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
