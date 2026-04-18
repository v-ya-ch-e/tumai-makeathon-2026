"""Add nearby_places JSON column to listingscorerow.

Stores the nearest resolved place for place-like user preferences
(`gym`, `park`, `supermarket`, ...) so the drawer and the scorer can
reuse the same neighborhood context without re-calling the Places API.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_nearby_places"
down_revision: Union[str, None] = "0006_scorecard_components"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("listingscorerow") as batch:
        batch.add_column(sa.Column("nearby_places", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("listingscorerow") as batch:
        batch.drop_column("nearby_places")
