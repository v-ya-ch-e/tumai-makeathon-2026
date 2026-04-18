"""JSON request/response DTOs for the WG Hunter API (separate from domain models)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field, model_validator

from .models import (
    AgentAction,
    Hunt,
    Listing,
    PlaceLocation,
    PreferenceWeight,
    SearchProfile,
    UserProfile,
)


class UserDTO(BaseModel):
    username: str
    age: int
    gender: str
    created_at: datetime


class CreateUserBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=40)
    age: int = Field(..., ge=16, le=99)
    gender: str = Field(..., pattern=r"^(female|male|diverse|prefer_not_to_say)$")


class SearchProfileDTO(BaseModel):
    price_min_eur: int
    price_max_eur: Optional[int] = None
    main_locations: list[PlaceLocation]
    has_car: bool
    has_bike: bool
    mode: Literal["wg", "flat", "both"]
    move_in_from: Optional[date] = None
    move_in_until: Optional[date] = None
    preferences: list[PreferenceWeight]
    rescan_interval_minutes: int
    schedule: Literal["one_shot", "periodic"]
    updated_at: datetime


class UpsertSearchProfileBody(BaseModel):
    price_min_eur: int = Field(0, ge=0, le=5000)
    price_max_eur: Optional[int] = Field(None, ge=0, le=5000)
    main_locations: list[PlaceLocation] = Field(default_factory=list)
    has_car: bool = False
    has_bike: bool = False
    mode: Literal["wg", "flat", "both"] = "wg"
    move_in_from: Optional[date] = None
    move_in_until: Optional[date] = None
    preferences: list[PreferenceWeight] = Field(default_factory=list)
    rescan_interval_minutes: int = Field(30, ge=5, le=1440)
    schedule: Literal["one_shot", "periodic"] = "one_shot"


class CredentialsBody(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    storage_state: Optional[dict] = None

    @model_validator(mode="after")
    def _exactly_one_form(self) -> CredentialsBody:
        email_pw = bool(self.email) and bool(self.password)
        storage = self.storage_state is not None
        if email_pw == storage:
            raise ValueError(
                "Provide either {email, password} or {storage_state}, not both and not neither."
            )
        return self


class CredentialsStatusDTO(BaseModel):
    connected: bool
    saved_at: Optional[datetime] = None


class CreateHuntBody(BaseModel):
    schedule: Literal["one_shot", "periodic"] = "one_shot"
    rescan_interval_minutes: Optional[int] = Field(None, ge=5, le=1440)


class ActionDTO(BaseModel):
    at: datetime
    kind: str
    summary: str
    detail: Optional[str] = None
    listing_id: Optional[str] = None


class ListingDTO(BaseModel):
    id: str
    hunt_id: str
    url: str
    title: Optional[str] = None
    district: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    price_eur: Optional[int] = None
    size_m2: Optional[float] = None
    wg_size: Optional[int] = None
    available_from: Optional[date] = None
    available_to: Optional[date] = None
    description: Optional[str] = None
    score: Optional[float] = None
    score_reason: Optional[str] = None
    match_reasons: list[str] = Field(default_factory=list)
    mismatch_reasons: list[str] = Field(default_factory=list)


class HuntDTO(BaseModel):
    id: str
    username: Optional[str] = None
    status: str
    schedule: str
    started_at: datetime
    stopped_at: Optional[datetime] = None
    listings: list[ListingDTO] = Field(default_factory=list)
    actions: list[ActionDTO] = Field(default_factory=list)


class ListingDetailDTO(BaseModel):
    listing: ListingDTO
    photos: list[str]
    score: Optional[float] = None
    travel_minutes_per_location: Optional[dict[str, int]] = None


def user_to_dto(u: UserProfile) -> UserDTO:
    return UserDTO(
        username=u.username,
        age=u.age,
        gender=u.gender.value,
        created_at=u.created_at,
    )


def search_profile_to_dto(sp: SearchProfile) -> SearchProfileDTO:
    return SearchProfileDTO(
        price_min_eur=sp.price_min_eur,
        price_max_eur=sp.price_max_eur,
        main_locations=list(sp.main_locations),
        has_car=sp.has_car,
        has_bike=sp.has_bike,
        mode=sp.mode,
        move_in_from=sp.move_in_from,
        move_in_until=sp.move_in_until,
        preferences=list(sp.preferences),
        rescan_interval_minutes=sp.rescan_interval_minutes,
        schedule=sp.schedule,
        updated_at=sp.updated_at,
    )


def upsert_body_to_search_profile(b: UpsertSearchProfileBody) -> SearchProfile:
    # Transitional defaults so browser.py / brain.py keep working during API migration.
    max_eur = b.price_max_eur if b.price_max_eur is not None else 2000
    main = list(b.main_locations)
    city = main[0].label if main else "München"
    return SearchProfile(
        city=city,
        max_rent_eur=max_eur,
        price_min_eur=b.price_min_eur,
        price_max_eur=b.price_max_eur,
        main_locations=main,
        has_car=b.has_car,
        has_bike=b.has_bike,
        mode=b.mode,
        move_in_from=b.move_in_from,
        move_in_until=b.move_in_until,
        preferences=[PreferenceWeight.model_validate(p) for p in b.preferences],
        rescan_interval_minutes=b.rescan_interval_minutes,
        schedule=b.schedule,
        updated_at=datetime.utcnow(),
        min_rent_eur=b.price_min_eur,
    )


def action_to_dto(a: AgentAction) -> ActionDTO:
    return ActionDTO(
        at=a.at,
        kind=a.kind.value,
        summary=a.summary,
        detail=a.detail,
        listing_id=a.listing_id,
    )


def listing_to_dto(l: Listing, hunt_id: str) -> ListingDTO:
    title = l.title if l.title else None
    return ListingDTO(
        id=l.id,
        hunt_id=hunt_id,
        url=str(l.url),
        title=title,
        district=l.district,
        lat=l.lat,
        lng=l.lng,
        price_eur=l.price_eur,
        size_m2=l.size_m2,
        wg_size=l.wg_size,
        available_from=l.available_from,
        available_to=l.available_to,
        description=l.description,
        score=l.score,
        score_reason=l.score_reason,
        match_reasons=list(l.match_reasons),
        mismatch_reasons=list(l.mismatch_reasons),
    )


def hunt_to_dto(
    h: Hunt,
    *,
    username: Optional[str] = None,
    schedule: Optional[str] = None,
) -> HuntDTO:
    sched = schedule if schedule is not None else h.requirements.schedule
    return HuntDTO(
        id=h.id,
        username=username,
        status=h.status.value,
        schedule=sched,
        started_at=h.started_at,
        stopped_at=h.finished_at,
        listings=[listing_to_dto(l, h.id) for l in h.listings],
        actions=[action_to_dto(a) for a in h.actions],
    )
