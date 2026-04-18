"""Add lat/lng columns to listingrow for server-side geocoded addresses.

Nullable floats populated by `geocoder.geocode` during
`anonymous_scrape_listing`. Existing rows stay NULL; commute-aware
scoring (future work) treats NULL as "no origin available".
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_listing_coords"
down_revision: Union[str, None] = "0002_places_main_locations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("listingrow") as batch:
        batch.add_column(sa.Column("lat", sa.Float(), nullable=True))
        batch.add_column(sa.Column("lng", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("listingrow") as batch:
        batch.drop_column("lng")
        batch.drop_column("lat")
