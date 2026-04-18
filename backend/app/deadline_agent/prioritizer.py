from datetime import date

from .models import TimelineItem, UrgencyLevel

_URGENCY_ORDER = {
    UrgencyLevel.high: 0,
    UrgencyLevel.medium: 1,
    UrgencyLevel.low: 2,
}


def compute_timeline_item_urgency(item: TimelineItem) -> UrgencyLevel:
    days_until = (item.date - date.today()).days
    if item.category.value == "deadline" and days_until <= 2:
        return UrgencyLevel.high
    if _is_registration_open(item) and days_until <= 1:
        return UrgencyLevel.high
    if days_until <= 7:
        return UrgencyLevel.medium
    return UrgencyLevel.low


def apply_timeline_item_urgency(items: list[TimelineItem]) -> list[TimelineItem]:
    return [
        item.model_copy(update={"urgency": compute_timeline_item_urgency(item)})
        for item in items
    ]


def prioritize_timeline_items(items: list[TimelineItem]) -> list[TimelineItem]:
    return sorted(items, key=lambda item: (_URGENCY_ORDER[item.urgency], item.date, item.title))


def _is_registration_open(item: TimelineItem) -> bool:
    title = item.title.lower()
    return "registration opens" in title or "opens registration" in title
