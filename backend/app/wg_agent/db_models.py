"""SQLModel table definitions (persistence layer only)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Column, JSON, LargeBinary
from sqlmodel import Field, SQLModel


class UserRow(SQLModel, table=True):
    __tablename__ = "userrow"
    username: str = Field(primary_key=True)
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


class HuntRow(SQLModel, table=True):
    __tablename__ = "huntrow"
    id: str = Field(primary_key=True)
    username: str = Field(foreign_key="userrow.username", index=True)
    status: str
    schedule: str
    started_at: datetime
    stopped_at: Optional[datetime] = None


class ListingRow(SQLModel, table=True):
    __tablename__ = "listingrow"
    id: str = Field(primary_key=True)
    hunt_id: str = Field(primary_key=True, foreign_key="huntrow.id")
    url: str
    title: Optional[str] = None
    price_eur: Optional[int] = None
    size_m2: Optional[float] = None
    wg_size: Optional[int] = None
    district: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    available_from: Optional[date] = None
    available_to: Optional[date] = None
    description: Optional[str] = None
    first_seen_at: datetime
    last_seen_at: datetime


class PhotoRow(SQLModel, table=True):
    __tablename__ = "photorow"
    listing_id: str = Field(primary_key=True)
    hunt_id: str = Field(primary_key=True)
    ordinal: int = Field(primary_key=True)
    url: str


class ListingScoreRow(SQLModel, table=True):
    __tablename__ = "listingscorerow"
    listing_id: str = Field(primary_key=True)
    hunt_id: str = Field(primary_key=True)
    score: float
    reason: Optional[str] = None
    match_reasons: list = Field(default_factory=list, sa_column=Column(JSON))
    mismatch_reasons: list = Field(default_factory=list, sa_column=Column(JSON))
    travel_minutes: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    components: Optional[list] = Field(default=None, sa_column=Column(JSON))
    veto_reason: Optional[str] = None
    scored_at: datetime


class AgentActionRow(SQLModel, table=True):
    __tablename__ = "agentactionrow"
    id: Optional[int] = Field(default=None, primary_key=True)
    hunt_id: str = Field(foreign_key="huntrow.id", index=True)
    kind: str
    summary: str
    detail: Optional[str] = None
    listing_id: Optional[str] = None
    at: datetime


class MessageRow(SQLModel, table=True):
    __tablename__ = "messagerow"
    id: Optional[int] = Field(default=None, primary_key=True)
    listing_id: str = Field(index=True)
    hunt_id: str = Field(index=True)
    direction: str
    text: str
    sent_at: datetime
