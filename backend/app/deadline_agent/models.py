from datetime import date
from enum import Enum

from pydantic import BaseModel


class UrgencyLevel(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class TimelineCategory(str, Enum):
    deadline = "deadline"
    course = "course"
    sport = "sport"
    event = "event"


class Deadline(BaseModel):
    title: str
    date: date
    source: str


class TimelineItem(Deadline):
    category: TimelineCategory
    urgency: UrgencyLevel


class TimelineDigest(BaseModel):
    urgent_count: int
    this_week_count: int
    next_important_item: TimelineItem | None
    short_summary: str
