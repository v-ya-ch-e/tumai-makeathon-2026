"""Add travel_minutes JSON column to listingscorerow for commute-aware scoring.

Stores the shortest mode-min per main location as
`{"<place_id>": {"mode": "BICYCLE", "minutes": 18}}`, so the listing
drawer can render commute times without re-calling the Routes API.
Existing rows stay NULL; the UI treats NULL as "no commute data".
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_listing_commute"
down_revision: Union[str, None] = "0003_listing_coords"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("listingscorerow") as batch:
        batch.add_column(sa.Column("travel_minutes", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("listingscorerow") as batch:
        batch.drop_column("travel_minutes")
