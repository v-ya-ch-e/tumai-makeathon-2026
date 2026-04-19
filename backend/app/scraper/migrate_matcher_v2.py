"""One-shot DB migration for the Matcher v2 rollout.

Adds the columns the v2 evaluator expects (`docs/MATCHER.md` §2.1, §2.2,
§5.6, §3.4, upfront_cost_fit) without dropping any rows. Idempotent:
every step inspects `information_schema` first and skips work that's
already done, so the script is safe to re-run after a partial failure
or against an environment where part of the work has already landed.

Three steps:

  1. ALTER `listingrow` — add `price_basis VARCHAR(16)`, `deposit_months
     DOUBLE`, `furniture_buyout_eur INT`. All nullable, no default; the
     engine reads `None` as "unknown" and never penalises listings just
     for missing the new fields.
  2. ALTER `searchprofilerow` — add `desired_min_months INT`,
     `flatmate_self_gender VARCHAR(32)`, `flatmate_self_age INT`. All
     nullable; the wizard does not yet expose these (see ROADMAP) and
     the engine degrades gracefully when they are NULL.
  3. Backfill `listingrow.price_basis = 'unknown'` for legacy rows
     (`WHERE price_basis IS NULL`). Engine treats NULL and 'unknown'
     identically, but the explicit value lets the drawer decide whether
     to render the "+20% Kalt uplift" badge with confidence (only when
     `price_basis = 'kalt_uplift'`).

The backend container MUST be stopped while this runs (so it doesn't
race the ALTER on `searchprofilerow` while a matcher pass is reading
it). The scraper can keep running — it never writes to
`searchprofilerow`, and the `listingrow` adds are pure additions.

Usage:

    # The same DB_* env vars that backend/app/wg_agent/db.py reads.
    DB_HOST=... DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \\
      backend/venv/bin/python -m app.scraper.migrate_matcher_v2

Add `--dry-run` to print the planned ALTER/UPDATE statements without
executing them.
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import text
from sqlmodel import Session

from ..wg_agent import db as db_module

logger = logging.getLogger(__name__)


# Per-table ADD COLUMN plan. Each tuple is `(column, sql_type)` matching
# the Pydantic / SQLModel field's storage type. `information_schema`
# gates each ADD so re-runs are safe.
_LISTINGROW_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("price_basis", "VARCHAR(16) NULL"),
    ("deposit_months", "DOUBLE NULL"),
    ("furniture_buyout_eur", "INT NULL"),
)

_SEARCHPROFILEROW_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("desired_min_months", "INT NULL"),
    ("flatmate_self_gender", "VARCHAR(32) NULL"),
    ("flatmate_self_age", "INT NULL"),
)


def _first_cell(row):
    if row is None:
        return None
    if hasattr(row, "_mapping"):
        return row[0]
    if isinstance(row, (tuple, list)):
        return row[0]
    return row


def _column_exists(session: Session, table: str, column: str) -> bool:
    row = session.exec(
        text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t "
            "AND column_name = :c"
        ).bindparams(t=table, c=column)
    ).first()
    return int(_first_cell(row) or 0) > 0


def _scalar(session: Session, sql: str, **params) -> int:
    row = session.exec(text(sql).bindparams(**params)).first()
    return int(_first_cell(row) or 0)


def _exec(session: Session, sql: str, *, dry_run: bool) -> None:
    if dry_run:
        logger.info("(dry-run) %s", sql)
        return
    logger.info("EXEC %s", sql)
    session.exec(text(sql))


def _add_columns(
    session: Session,
    *,
    table: str,
    columns: tuple[tuple[str, str], ...],
    dry_run: bool,
) -> None:
    for column, sql_type in columns:
        if _column_exists(session, table, column):
            logger.info("%s.%s: column already present, skip", table, column)
            continue
        _exec(
            session,
            f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}",
            dry_run=dry_run,
        )


def step_1_extend_listingrow(session: Session, *, dry_run: bool) -> None:
    logger.info("=== Step 1: extend listingrow with v2 columns ===")
    _add_columns(
        session,
        table="listingrow",
        columns=_LISTINGROW_NEW_COLUMNS,
        dry_run=dry_run,
    )


def step_2_extend_searchprofilerow(session: Session, *, dry_run: bool) -> None:
    logger.info("=== Step 2: extend searchprofilerow with v2 columns ===")
    _add_columns(
        session,
        table="searchprofilerow",
        columns=_SEARCHPROFILEROW_NEW_COLUMNS,
        dry_run=dry_run,
    )


def step_3_backfill_price_basis(session: Session, *, dry_run: bool) -> None:
    """Set `price_basis = 'unknown'` on every legacy row so the drawer can
    distinguish "unknown" (legacy) from "warm" / "kalt_uplift" (newly
    enriched) without conditional joins. Idempotent: only NULL rows are
    touched."""
    logger.info("=== Step 3: backfill listingrow.price_basis = 'unknown' ===")

    if not _column_exists(session, "listingrow", "price_basis"):
        logger.warning("listingrow.price_basis missing; step 1 must run first")
        return

    null_rows = _scalar(
        session,
        "SELECT COUNT(*) FROM listingrow WHERE price_basis IS NULL",
    )
    if null_rows == 0:
        logger.info("No NULL price_basis rows in listingrow, nothing to backfill")
        return

    logger.info("Will backfill %d listingrow rows", null_rows)
    _exec(
        session,
        "UPDATE listingrow SET price_basis = 'unknown' WHERE price_basis IS NULL",
        dry_run=dry_run,
    )


def verify(session: Session) -> None:
    logger.info("=== Verification ===")
    for column, _ in _LISTINGROW_NEW_COLUMNS:
        present = _column_exists(session, "listingrow", column)
        logger.info("listingrow.%s present: %s", column, present)
    for column, _ in _SEARCHPROFILEROW_NEW_COLUMNS:
        present = _column_exists(session, "searchprofilerow", column)
        logger.info("searchprofilerow.%s present: %s", column, present)

    null_basis = _scalar(
        session,
        "SELECT COUNT(*) FROM listingrow WHERE price_basis IS NULL",
    )
    logger.info(
        "listingrow rows with price_basis still NULL: %d (should be 0)",
        null_basis,
    )

    total_listings = _scalar(session, "SELECT COUNT(*) FROM listingrow")
    total_profiles = _scalar(session, "SELECT COUNT(*) FROM searchprofilerow")
    logger.info(
        "Untouched: %d listingrow rows + %d searchprofilerow rows",
        total_listings,
        total_profiles,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print every planned ALTER/UPDATE without executing it.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    logger.info("Database: %s", db_module.describe_database())
    if args.dry_run:
        logger.info("Running in DRY-RUN mode: nothing will be written")

    with Session(db_module.engine) as session:
        try:
            step_1_extend_listingrow(session, dry_run=args.dry_run)
            step_2_extend_searchprofilerow(session, dry_run=args.dry_run)
            step_3_backfill_price_basis(session, dry_run=args.dry_run)

            if not args.dry_run:
                session.commit()

            verify(session)
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.exception("Migration failed; rolled back")
            sys.exit(1)

    logger.info("Migration complete.")


if __name__ == "__main__":
    main()
