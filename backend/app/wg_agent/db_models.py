"""SQLModel table definitions (persistence layer only)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Column, JSON, LargeBinary, Text
from sqlmodel import Field, SQLModel


class UserRow(SQLModel, table=True):
    __tablename__ = "userrow"
    username: str = Field(primary_key=True)
    email: Optional[str] = Field(default=None, index=True, unique=True)
    age: int
    gender: str
    created_at: datetime
    # Per-user cutoff for "new" classification in the dashboard badge and the
    # email digest gate. Initialized to `created_at` on signup, bumped to
    # `utcnow()` every time the user materially changes their search profile
    # so the ensuing silent re-backfill never produces "new" badges or email
    # notifications. Nullable so pre-migration rows keep working; call sites
    # fall back to `created_at` when the column is NULL.
    backfill_baseline_at: Optional[datetime] = None
    # Optional landlord-intro fields. Populated via the "Information for
    # landlord" section in Profile settings; consumed by
    # `brain.draft_message` to craft personalized first messages.
    first_name: Optional[str] = Field(default=None, sa_column=Column("first_name", Text))
    last_name: Optional[str] = Field(default=None, sa_column=Column("last_name", Text))
    phone: Optional[str] = Field(default=None, sa_column=Column("phone", Text))
    occupation: Optional[str] = Field(default=None, sa_column=Column("occupation", Text))
    bio: Optional[str] = Field(default=None, sa_column=Column("bio", Text))
    languages: Optional[list] = Field(default=None, sa_column=Column("languages", JSON))


class WgCredentialsRow(SQLModel, table=True):
    __tablename__ = "wgcredentialsrow"
    username: str = Field(primary_key=True, foreign_key="userrow.username")
    encrypted_payload: bytes = Field(sa_column=Column("encrypted_payload", LargeBinary))
    saved_at: datetime


class SearchProfileRow(SQLModel, table=True):
    __tablename__ = "searchprofilerow"
    username: str = Field(primary_key=True, foreign_key="userrow.username")
    price_min_eur: int = 0
    price_max_eur: Optional[int] = None
    main_locations: list = Field(default_factory=list, sa_column=Column(JSON))
    has_car: bool = False
    has_bike: bool = False
    mode: str = "wg"
    move_in_from: Optional[date] = None
    move_in_until: Optional[date] = None
    preferences: list = Field(default_factory=list, sa_column=Column(JSON))
    rescan_interval_minutes: int = 30
    schedule: str = "one_shot"
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ListingRow(SQLModel, table=True):
    """Global listing pool; the scraper is the sole writer.

    `deleted_at` is a deprecated, no-longer-written column kept on the
    schema only for backward compatibility with rows tombstoned by the
    legacy deletion sweep. New code never sets or reads it.
    """

    __tablename__ = "listingrow"
    id: str = Field(primary_key=True)
    url: str = Field(sa_column=Column(Text, nullable=False))
    title: Optional[str] = Field(default=None, sa_column=Column(Text))
    price_eur: Optional[int] = None
    size_m2: Optional[float] = None
    wg_size: Optional[int] = None
    city: Optional[str] = Field(default=None, sa_column=Column(Text))
    district: Optional[str] = Field(default=None, sa_column=Column(Text))
    address: Optional[str] = Field(default=None, sa_column=Column(Text))
    lat: Optional[float] = None
    lng: Optional[float] = None
    available_from: Optional[date] = None
    available_to: Optional[date] = None
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    furnished: Optional[bool] = None
    pets_allowed: Optional[bool] = None
    smoking_ok: Optional[bool] = None
    languages: Optional[list] = Field(default=None, sa_column=Column(JSON))
    kind: str = Field(default="wg", index=True)
    scrape_status: str = Field(default="stub", index=True)
    scraped_at: Optional[datetime] = Field(default=None, index=True)
    scrape_error: Optional[str] = Field(default=None, sa_column=Column(Text))
    first_seen_at: datetime
    last_seen_at: datetime
    deleted_at: Optional[datetime] = Field(default=None, index=True)


class PhotoRow(SQLModel, table=True):
    __tablename__ = "photorow"
    listing_id: str = Field(primary_key=True, foreign_key="listingrow.id")
    ordinal: int = Field(primary_key=True)
    url: str


class UserListingRow(SQLModel, table=True):
    __tablename__ = "userlistingrow"
    username: str = Field(primary_key=True, foreign_key="userrow.username")
    listing_id: str = Field(primary_key=True, foreign_key="listingrow.id")
    score: float
    reason: Optional[str] = None
    match_reasons: list = Field(default_factory=list, sa_column=Column(JSON))
    mismatch_reasons: list = Field(default_factory=list, sa_column=Column(JSON))
    travel_minutes: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    nearby_places: Optional[list] = Field(default=None, sa_column=Column(JSON))
    components: Optional[list] = Field(default=None, sa_column=Column(JSON))
    veto_reason: Optional[str] = None
    scored_against_scraped_at: Optional[datetime] = None
    scored_at: datetime


class UserActionRow(SQLModel, table=True):
    __tablename__ = "useractionrow"
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(foreign_key="userrow.username", index=True)
    kind: str
    summary: str
    detail: Optional[str] = None
    listing_id: Optional[str] = Field(default=None, foreign_key="listingrow.id")
    at: datetime


class UserAgentStateRow(SQLModel, table=True):
    """Persisted per-user agent lifecycle flag.

    Written only when the user explicitly pauses or resumes their agent
    (`POST /agent/pause` / `/agent/start`). Absence of a row means the
    agent is running (the default for newly-onboarded users). When
    `paused=True`, `resume_user_agents` skips the user on backend boot —
    so a user who pressed "Stop" stays stopped across restarts until they
    press "Resume" in the dashboard.
    """

    __tablename__ = "useragentstaterow"
    username: str = Field(primary_key=True, foreign_key="userrow.username")
    paused: bool = False
    updated_at: datetime


class ScraperEventRow(SQLModel, table=True):
    """Append-only outbox the scraper writes on a newly persisted full listing.

    The backend's `scraper_watcher` tails this table (id-ordered) and wakes
    every per-user matcher so high-scoring matches surface without waiting
    for the next polled rescan. Rows are never deleted; the watermark is
    held in memory by the watcher.
    """

    __tablename__ = "scrapereventrow"
    id: Optional[int] = Field(default=None, primary_key=True)
    listing_id: str = Field(foreign_key="listingrow.id", index=True)
    kind: str = Field(default="new_listing", index=True)
    created_at: datetime
