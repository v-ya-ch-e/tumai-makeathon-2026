"""Initial WG Hunter tables."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "userrow",
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("age", sa.Integer(), nullable=False),
        sa.Column("gender", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("username"),
    )
    op.create_table(
        "wgcredentialsrow",
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("encrypted_payload", sa.LargeBinary(), nullable=False),
        sa.Column("saved_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["username"], ["userrow.username"]),
        sa.PrimaryKeyConstraint("username"),
    )
    op.create_table(
        "searchprofilerow",
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("price_min_eur", sa.Integer(), nullable=False),
        sa.Column("price_max_eur", sa.Integer(), nullable=True),
        sa.Column("main_locations", sa.JSON(), nullable=True),
        sa.Column("has_car", sa.Boolean(), nullable=False),
        sa.Column("has_bike", sa.Boolean(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("move_in_from", sa.Date(), nullable=True),
        sa.Column("move_in_until", sa.Date(), nullable=True),
        sa.Column("preferences", sa.JSON(), nullable=True),
        sa.Column("rescan_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("schedule", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["username"], ["userrow.username"]),
        sa.PrimaryKeyConstraint("username"),
    )
    op.create_table(
        "huntrow",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("schedule", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("stopped_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["username"], ["userrow.username"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_huntrow_username", "huntrow", ["username"], unique=False)
    op.create_table(
        "listingrow",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("hunt_id", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("price_eur", sa.Integer(), nullable=True),
        sa.Column("size_m2", sa.Float(), nullable=True),
        sa.Column("wg_size", sa.Integer(), nullable=True),
        sa.Column("district", sa.String(), nullable=True),
        sa.Column("available_from", sa.Date(), nullable=True),
        sa.Column("available_to", sa.Date(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["hunt_id"], ["huntrow.id"]),
        sa.PrimaryKeyConstraint("id", "hunt_id"),
    )
    op.create_table(
        "photorow",
        sa.Column("listing_id", sa.String(), nullable=False),
        sa.Column("hunt_id", sa.String(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("listing_id", "hunt_id", "ordinal"),
    )
    op.create_table(
        "listingscorerow",
        sa.Column("listing_id", sa.String(), nullable=False),
        sa.Column("hunt_id", sa.String(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("match_reasons", sa.JSON(), nullable=True),
        sa.Column("mismatch_reasons", sa.JSON(), nullable=True),
        sa.Column("scored_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("listing_id", "hunt_id"),
    )
    op.create_table(
        "agentactionrow",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("hunt_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=True),
        sa.Column("listing_id", sa.String(), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["hunt_id"], ["huntrow.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agentactionrow_hunt_id", "agentactionrow", ["hunt_id"], unique=False
    )
    op.create_table(
        "messagerow",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("listing_id", sa.String(), nullable=False),
        sa.Column("hunt_id", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messagerow_listing_id", "messagerow", ["listing_id"], unique=False)
    op.create_index("ix_messagerow_hunt_id", "messagerow", ["hunt_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_messagerow_hunt_id", table_name="messagerow")
    op.drop_index("ix_messagerow_listing_id", table_name="messagerow")
    op.drop_table("messagerow")
    op.drop_index("ix_agentactionrow_hunt_id", table_name="agentactionrow")
    op.drop_table("agentactionrow")
    op.drop_table("listingscorerow")
    op.drop_table("photorow")
    op.drop_table("listingrow")
    op.drop_index("ix_huntrow_username", table_name="huntrow")
    op.drop_table("huntrow")
    op.drop_table("searchprofilerow")
    op.drop_table("wgcredentialsrow")
    op.drop_table("userrow")
