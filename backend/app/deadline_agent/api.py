from fastapi import APIRouter

from .models import TimelineCategory, TimelineDigest, TimelineItem
from .service import get_timeline_digest, get_timeline_items, get_timeline_summary

router = APIRouter(prefix="/api/deadline", tags=["deadline"])


@router.get("/summary", response_model=list[TimelineItem])
def deadline_summary() -> list[TimelineItem]:
    return get_timeline_summary()


@router.get("/timeline", response_model=list[TimelineItem])
def deadline_timeline(
    category: TimelineCategory | None = None,
) -> list[TimelineItem]:
    return get_timeline_items(category=category)


@router.get("/digest", response_model=TimelineDigest)
def deadline_digest() -> TimelineDigest:
    return get_timeline_digest()
