"""Switch `searchprofilerow.main_locations` JSON shape to PlaceLocation objects.

The column type is already `JSON`; what changes is the shape of each list
element: previously a plain string (e.g. `"Maxvorstadt"`), now a dict
`{"label": "...", "place_id": "...", "lat": 48.1, "lng": 11.5}` produced
by the Google Places Autocomplete widget in the onboarding wizard.

Because the repo is still pre-demo, this migration discards any existing
values rather than attempting a half-baked geocode fallback.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002_places_main_locations"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE searchprofilerow SET main_locations = '[]'")


def downgrade() -> None:
    op.execute("UPDATE searchprofilerow SET main_locations = '[]'")
