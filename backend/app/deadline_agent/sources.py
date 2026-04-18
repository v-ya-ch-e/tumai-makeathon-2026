from datetime import date, timedelta


def _day(offset: int) -> date:
    return date.today() + timedelta(days=offset)


def load_moodle_deadlines() -> list[dict[str, str | date]]:
    return [
        {
            "title": "Machine Learning assignment 2 submission deadline",
            "date": _day(1),
            "source": "Moodle",
            "category": "deadline",
        },
        {
            "title": "Database Systems exercise sheet 5 due",
            "date": _day(4),
            "source": "Moodle",
            "category": "deadline",
        },
        {
            "title": "Human-Centered Design reflection journal upload",
            "date": _day(11),
            "source": "Moodle",
            "category": "deadline",
        },
    ]


def load_tumonline_course_events() -> list[dict[str, str | date]]:
    return [
        {
            "title": "Exam registration deadline for Algorithms and Data Structures",
            "date": _day(2),
            "source": "TUMonline",
            "category": "deadline",
        },
        {
            "title": "Operating Systems lecture: virtualization recap",
            "date": _day(3),
            "source": "TUMonline",
            "category": "course-event",
        },
        {
            "title": "Linear Algebra tutorial: exam Q&A session",
            "date": _day(6),
            "source": "TUMonline",
            "category": "course-event",
        },
        {
            "title": "Exam registration deadline for Introduction to AI",
            "date": _day(9),
            "source": "TUMonline",
            "category": "deadline",
        },
    ]


def load_zhs_registration_events() -> list[dict[str, str | date]]:
    return [
        {
            "title": "ZHS bouldering basics registration opens",
            "date": _day(1),
            "source": "ZHS",
            "category": "sports-registration",
        },
        {
            "title": "ZHS swimming intensive opens registration",
            "date": _day(10),
            "source": "ZHS",
            "category": "sports-registration",
        },
    ]


def load_tum_campus_events() -> list[dict[str, str | date]]:
    return [
        {
            "title": "TUM.ai CV workshop for internships",
            "date": _day(5),
            "source": "TUM Campus",
            "category": "campus-event",
        },
        {
            "title": "Student club onboarding evening at Stammgelande",
            "date": _day(8),
            "source": "TUM Campus",
            "category": "campus-event",
        },
        {
            "title": "Entrepreneurship breakfast and founder Q&A",
            "date": _day(14),
            "source": "TUM Campus",
            "category": "campus-event",
        },
    ]
