from .models import TimelineCategory, TimelineItem, UrgencyLevel

_CATEGORY_MAP = {
    "deadline": TimelineCategory.deadline,
    "course-event": TimelineCategory.course,
    "sports-registration": TimelineCategory.sport,
    "campus-event": TimelineCategory.event,
}


def normalize_source_items(items: list[dict]) -> list[TimelineItem]:
    timeline: list[TimelineItem] = []
    for item in items:
        timeline.append(
            TimelineItem(
                title=str(item["title"]),
                date=item["date"],
                source=str(item["source"]),
                category=_CATEGORY_MAP[str(item["category"])],
                urgency=UrgencyLevel.low,
            )
        )
    return timeline
