from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.wg_agent.dto import UpsertSearchProfileBody, upsert_body_to_search_profile


def test_upsert_body_keeps_munich_as_search_city_for_commute_anchors() -> None:
    body = UpsertSearchProfileBody(
        price_min_eur=400,
        price_max_eur=900,
        main_locations=[
            {
                "label": "Technical University of Munich",
                "place_id": "ChIJtum",
                "lat": 48.148,
                "lng": 11.567,
            }
        ],
    )

    profile = upsert_body_to_search_profile(body)

    assert profile.city == "München"
