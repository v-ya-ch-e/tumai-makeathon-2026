"""Add components + veto_reason columns to listingscorerow.

Supports the scorecard evaluator (ADR-015):

  * `components` (JSON, nullable): serialized `ComponentScore` list, one
    entry per evaluator component (price, size, commute, preferences,
    availability, wg_size, vibe). Rendered as per-component bars in the
    listing drawer.
  * `veto_reason` (String, nullable): set when `hard_filter` short-
    circuited evaluation (over budget, wrong city, avoid-district, …).
    Mutually exclusive with a scored breakdown.

Existing rows keep both columns NULL; `_listing_from_row` falls back to
`reason` / `match_reasons` / `mismatch_reasons` for pre-migration data
so old hunts still render.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_scorecard_components"
down_revision: Union[str, None] = "0005_weighted_prefs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("listingscorerow") as batch:
        batch.add_column(sa.Column("components", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("veto_reason", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("listingscorerow") as batch:
        batch.drop_column("veto_reason")
        batch.drop_column("components")
