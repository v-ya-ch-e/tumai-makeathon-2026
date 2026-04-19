"""Quick sanity-check script — calls the real Google Maps API.

Usage (from backend/):
    python check_gmaps.py

Reads GOOGLE_MAPS_SERVER_KEY from ../.env automatically.
Uses Grasmaierstraße 25d, 80805 München as the listing origin.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

# Load ../.env so we get GOOGLE_MAPS_SERVER_KEY without a running container
env_file = pathlib.Path(__file__).parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from app.wg_agent import places, commute  # noqa: E402
from app.wg_agent.models import PlaceLocation, PreferenceWeight  # noqa: E402

# Grasmaierstraße 25d, 80805 München
ORIGIN = (48.1827817, 11.61066)

ALL_PREFS = [
    PreferenceWeight(key="supermarket", weight=5),
    PreferenceWeight(key="gym", weight=5),
    PreferenceWeight(key="park", weight=4),
    PreferenceWeight(key="cafe", weight=3),
    PreferenceWeight(key="bars", weight=3),
    PreferenceWeight(key="public_transport", weight=5),
    PreferenceWeight(key="green_space", weight=3),
    PreferenceWeight(key="library", weight=2),
    PreferenceWeight(key="nightlife", weight=2),
    PreferenceWeight(key="coworking", weight=2),
]

# Destinations to check commute to (edit as needed)
DESTINATIONS = [
    PlaceLocation(label="TUM Hauptcampus", place_id="ChIJi4WMoxenthQRMBbHFWKBGMY",
                  lat=48.14948, lng=11.56797, max_commute_minutes=30),
    PlaceLocation(label="Marienplatz", place_id="ChIJ2V-Mo_l1nkcRfZixfUscJkE",
                  lat=48.13743, lng=11.57549, max_commute_minutes=25),
]


def _fmt_distance(m: int | None) -> str:
    if m is None:
        return "?"
    if m < 1000:
        return f"{m} m"
    return f"{m / 1000:.1f} km"


def _fmt_minutes(s: int) -> str:
    return f"{s // 60} min"


async def main() -> None:
    key = os.environ.get("GOOGLE_MAPS_SERVER_KEY")
    if not key:
        print("ERROR: GOOGLE_MAPS_SERVER_KEY not set")
        sys.exit(1)

    print(f"\nOrigin: Grasmaierstraße 25d, 80805 München  ({ORIGIN[0]}, {ORIGIN[1]})\n")

    # --- Nearby places ---
    print("=== Nearby Places ===")
    result = await places.nearby_places(origin=ORIGIN, preferences=ALL_PREFS)
    for pref in ALL_PREFS:
        key_name = pref.key
        place = result.get(key_name)
        if place is None:
            print(f"  {key_name:<20} — not supported")
        elif not place.searched:
            print(f"  {key_name:<20} — API error / no key")
        elif place.distance_m is None:
            print(f"  {key_name:<20} — nothing within 2 km")
        else:
            dist = _fmt_distance(place.distance_m)
            name = place.place_name or "(no name)"
            print(f"  {key_name:<20} {name}  —  {dist}")

    # --- Commute times ---
    print("\n=== Commute Times (next 9 AM weekday) ===")
    times = await commute.travel_times(
        origin=ORIGIN,
        destinations=DESTINATIONS,
        modes=["TRANSIT", "BICYCLE", "DRIVE"],
    )
    for dest in DESTINATIONS:
        print(f"\n  → {dest.label}")
        for mode in ("TRANSIT", "BICYCLE", "DRIVE"):
            secs = times.get((dest.place_id, mode))
            if secs is not None:
                print(f"      {mode:<10} {_fmt_minutes(secs)}")
            else:
                print(f"      {mode:<10} —")


if __name__ == "__main__":
    asyncio.run(main())
