"""SQLModel table definitions (persistence layer only)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Column, JSON, LargeBinary
from sqlmodel import Field, SQLModel


class UserRow(SQLModel, table=True):
    __tablename__ = "userrow"
    username: str = Field(primary_key=True)
    email: Optional[str] = Field(default=None, index=True, unique=True)
    age: int
    gender: str
    created_at: datetime


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
    """Global wg-gesucht listing; the scraper is the sole writer.

    `deleted_at` marks listings no longer visible on wg-gesucht.
    """

    __tablename__ = "listingrow"
    id: str = Field(primary_key=True)
    url: str
    title: Optional[str] = None
    price_eur: Optional[int] = None
    size_m2: Optional[float] = None
    wg_size: Optional[int] = None
    city: Optional[str] = None
    district: Optional[str] = None
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    available_from: Optional[date] = None
    available_to: Optional[date] = None
    description: Optional[str] = None
    furnished: Optional[bool] = None
    pets_allowed: Optional[bool] = None
    smoking_ok: Optional[bool] = None
    languages: Optional[list] = Field(default=None, sa_column=Column(JSON))
    scrape_status: str = Field(default="stub", index=True)
    scraped_at: Optional[datetime] = Field(default=None, index=True)
    scrape_error: Optional[str] = None
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
