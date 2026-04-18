"""Switch preferences to weighted objects + add per-location commute budgets.

Two JSON shape changes inside `searchprofilerow`, both columns typed `JSON`
so no column schema change is needed:

  * `preferences`: elements change from plain strings (e.g. `"gym"`) to
    `{"key": "gym", "weight": 3}`. `weight` is a 1..5 importance slider
    the scorer consumes; 3 is the neutral default used for legacy rows.
  * `main_locations`: elements gain an optional `max_commute_minutes`
    integer. Existing rows keep their labels/coordinates but get
    `max_commute_minutes: null` so the scorer can distinguish "no budget"
    from "missed migration".

Because the repo is still pre-demo, this migration resets both JSON
columns to empty lists rather than hand-rolling a mapping for stale
rows, mirroring `0002_places_main_locations.py`.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0005_weighted_prefs"
down_revision: Union[str, None] = "0004_listing_commute"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE searchprofilerow SET preferences = '[]'")
    op.execute("UPDATE searchprofilerow SET main_locations = '[]'")


def downgrade() -> None:
    op.execute("UPDATE searchprofilerow SET preferences = '[]'")
    op.execute("UPDATE searchprofilerow SET main_locations = '[]'")
