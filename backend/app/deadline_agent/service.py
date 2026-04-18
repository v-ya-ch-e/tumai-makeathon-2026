from datetime import date

from .models import TimelineCategory, TimelineDigest, TimelineItem, UrgencyLevel
from .normalizer import normalize_source_items
from .prioritizer import apply_timeline_item_urgency, prioritize_timeline_items
from .sources import (
    load_moodle_deadlines,
    load_tum_campus_events,
    load_tumonline_course_events,
    load_zhs_registration_events,
)


def get_timeline_items(
    category: TimelineCategory | None = None,
) -> list[TimelineItem]:
    raw_items = [
        *load_moodle_deadlines(),
        *load_tumonline_course_events(),
        *load_zhs_registration_events(),
        *load_tum_campus_events(),
    ]
    timeline = normalize_source_items(raw_items)
    timeline = apply_timeline_item_urgency(timeline)
    if category is not None:
        timeline = [item for item in timeline if item.category == category]
    return prioritize_timeline_items(timeline)


def get_timeline_summary() -> list[TimelineItem]:
    return get_timeline_items()


def get_deadline_summary() -> list[TimelineItem]:
    return get_timeline_items()


def get_timeline_digest() -> TimelineDigest:
    timeline = get_timeline_items()
    today = date.today()
    this_week_items = [
        item for item in timeline if 0 <= (item.date - today).days <= 7
    ]
    urgent_count = sum(1 for item in timeline if item.urgency == UrgencyLevel.high)
    sport_this_week_count = sum(
        1 for item in this_week_items if item.category == TimelineCategory.sport
    )
    next_important_item = next(
        (item for item in timeline if item.urgency in (UrgencyLevel.high, UrgencyLevel.medium)),
        None,
    )
    return TimelineDigest(
        urgent_count=urgent_count,
        this_week_count=len(this_week_items),
        next_important_item=next_important_item,
        short_summary=_build_short_summary(
            urgent_count=urgent_count,
            sport_this_week_count=sport_this_week_count,
        ),
    )


def _build_short_summary(*, urgent_count: int, sport_this_week_count: int) -> str:
    urgent_label = "deadline" if urgent_count == 1 else "deadlines"
    sport_label = (
        "sports registration" if sport_this_week_count == 1 else "sports registrations"
    )
    return (
        f"You have {urgent_count} urgent {urgent_label} and "
        f"{sport_this_week_count} {sport_label} this week."
    )
