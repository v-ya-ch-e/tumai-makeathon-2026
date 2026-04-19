"""One-shot DB migration for the multi-source scraper rollout.

Run before the new scraper image starts writing namespaced ids. This
script is **idempotent** — every step inspects the current schema /
data state first and skips work that's already done, so it's safe to
re-run after a partial failure.

Four steps, ordered per the multi-source rollout (cross-source contract
in `docs/SCRAPER.md`; ADR-020 + ADR-021 in `docs/DECISIONS.md`):

  1. Widen `listingrow` text columns (`url`, `title`, `city`, `district`,
     `address`, `description`, `scrape_error`) from `VARCHAR(255)` to
     `TEXT`, and add the `kind VARCHAR(255) NOT NULL DEFAULT 'wg'` column
     plus its index. (`SQLModel.metadata.create_all` does not alter
     existing tables — see ADR-019 — so this step uses hand-coded
     `ALTER TABLE`.)
  2. Namespace existing rows: `UPDATE listingrow SET id = CONCAT('wg-gesucht:', id)`
     plus the same on the three FK columns (`photorow.listing_id`,
     `userlistingrow.listing_id`, `useractionrow.listing_id`). Run in
     one transaction (FKs declare no `ON UPDATE CASCADE`, so children
     are updated first while parents are still unique, then parent).
  3. Force a one-cycle rescrape of every previously-full row by
     flipping `scrape_status = 'stub' WHERE scrape_status = 'full'`.
     The scraper's existing `_needs_scrape` re-fetches anything not
     `'full'`, so the next pass overwrites the silently-truncated
     descriptions with the now-untruncated ones.
  4. Wipe the global listing pool so the relaunched (laptop) scraper
     repopulates it from scratch, while preserving user identity, search
     profiles, and the action log: `UPDATE useractionrow SET listing_id = NULL`,
     then `DELETE FROM userlistingrow / photorow / listingrow`. One
     transaction (children before parents). Idempotent — skipped when
     `listingrow` is already empty.

The backend container MUST be stopped while this runs (so it doesn't
race the namespacing UPDATE on `listingrow.id`). The scraper is no
longer a cloud service (see `docs/SCRAPER.md` § "Local scraper run")
— just confirm no laptop pass is running, then restart the backend
after the script returns.

Usage:

    # The same DB_* env vars that backend/app/wg_agent/db.py reads.
    DB_HOST=... DB_PORT=3306 DB_USER=... DB_PASSWORD=... DB_NAME=... \\
      backend/venv/bin/python -m app.scraper.migrate_multi_source

Add `--dry-run` to print the planned actions without executing them.
Add `--skip-rescrape` to skip step 3 (useful if you want to widen the
columns and namespace ids, but not force a full rescrape).
Add `--skip-wipe` to skip step 4 (useful when you want a soft re-fetch
via step 3 without losing the existing rows).
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import text
from sqlmodel import Session

from ..wg_agent import db as db_module

logger = logging.getLogger(__name__)


_TEXT_COLUMNS_TO_WIDEN = (
    ("url", "TEXT NOT NULL"),
    ("title", "TEXT"),
    ("city", "TEXT"),
    ("district", "TEXT"),
    ("address", "TEXT"),
    ("description", "TEXT"),
    ("scrape_error", "TEXT"),
)


def _first_cell(row):
    """Return the first cell of a SQLAlchemy Row / tuple, or the value itself."""
    if row is None:
        return None
    if hasattr(row, "_mapping"):
        # SQLAlchemy Row supports tuple-style indexing.
        return row[0]
    if isinstance(row, (tuple, list)):
        return row[0]
    return row


def _column_type(session: Session, table: str, column: str) -> str | None:
    """Return the lowercase MySQL column type, or None if the column is absent."""
    row = session.exec(
        text(
            "SELECT LOWER(DATA_TYPE) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ).bindparams(t=table, c=column)
    ).first()
    val = _first_cell(row)
    return str(val) if val is not None else None


def _scalar(session: Session, sql: str, **params) -> int:
    row = session.exec(text(sql).bindparams(**params)).first()
    val = _first_cell(row)
    return int(val or 0)


def _has_index_on_kind(session: Session) -> bool:
    return _scalar(
        session,
        "SELECT COUNT(*) FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = 'listingrow' "
        "AND column_name = 'kind'",
    ) > 0


def _exec(session: Session, sql: str, *, dry_run: bool) -> None:
    if dry_run:
        logger.info("(dry-run) %s", sql)
        return
    logger.info("EXEC %s", sql)
    session.exec(text(sql))


def step_1_widen_and_add_kind(session: Session, *, dry_run: bool) -> None:
    """Widen text columns to TEXT and add the kind column.

    Each ALTER is gated on a fresh information_schema check so re-runs
    are idempotent (already-widened columns and the already-present
    kind column are skipped).
    """
    logger.info("=== Step 1: widen text columns + add kind column ===")

    for column, sql_type in _TEXT_COLUMNS_TO_WIDEN:
        current = _column_type(session, "listingrow", column)
        if current is None:
            logger.warning(
                "listingrow.%s: column not present, skipping (schema mismatch?)",
                column,
            )
            continue
        if current == "text":
            logger.info("listingrow.%s: already TEXT, skip", column)
            continue
        _exec(
            session,
            f"ALTER TABLE listingrow MODIFY {column} {sql_type}",
            dry_run=dry_run,
        )

    if _column_type(session, "listingrow", "kind") is None:
        _exec(
            session,
            "ALTER TABLE listingrow "
            "ADD COLUMN kind VARCHAR(255) NOT NULL DEFAULT 'wg'",
            dry_run=dry_run,
        )
    else:
        logger.info("listingrow.kind: column already present, skip")

    if _has_index_on_kind(session):
        logger.info("listingrow(kind): index already present, skip")
    else:
        _exec(
            session,
            "CREATE INDEX ix_listingrow_kind ON listingrow (kind)",
            dry_run=dry_run,
        )


def step_2_namespace_ids(session: Session, *, dry_run: bool) -> None:
    """Prefix every bare wg-gesucht id with `wg-gesucht:`.

    Children first, parent last, all in one transaction so no FK
    constraint is violated mid-update. Idempotent: only rows whose id
    does not already contain `:` are touched.
    """
    logger.info("=== Step 2: namespace existing wg-gesucht ids ===")

    bare_parents = _scalar(
        session,
        "SELECT COUNT(*) FROM listingrow WHERE id NOT LIKE '%:%'",
    )
    if bare_parents == 0:
        logger.info("No bare-id rows in listingrow, skipping namespacing")
        return

    logger.info(
        "Will namespace %d listingrow rows + matching FK children", bare_parents
    )

    if dry_run:
        bare_photo = _scalar(
            session,
            "SELECT COUNT(*) FROM photorow WHERE listing_id NOT LIKE '%:%'",
        )
        bare_user_listing = _scalar(
            session,
            "SELECT COUNT(*) FROM userlistingrow WHERE listing_id NOT LIKE '%:%'",
        )
        bare_user_action = _scalar(
            session,
            "SELECT COUNT(*) FROM useractionrow WHERE listing_id IS NOT NULL "
            "AND listing_id NOT LIKE '%:%'",
        )
        logger.info(
            "(dry-run) photorow rows to update: %d, userlistingrow: %d, useractionrow: %d",
            bare_photo,
            bare_user_listing,
            bare_user_action,
        )
        logger.info("(dry-run) would run UPDATEs inside one transaction")
        return

    # One transaction for the four UPDATEs (parent FKs have no ON UPDATE
    # CASCADE, so order matters: children first, parent last).
    _exec(
        session,
        "UPDATE photorow SET listing_id = CONCAT('wg-gesucht:', listing_id) "
        "WHERE listing_id NOT LIKE '%:%'",
        dry_run=False,
    )
    _exec(
        session,
        "UPDATE userlistingrow SET listing_id = CONCAT('wg-gesucht:', listing_id) "
        "WHERE listing_id NOT LIKE '%:%'",
        dry_run=False,
    )
    _exec(
        session,
        "UPDATE useractionrow SET listing_id = CONCAT('wg-gesucht:', listing_id) "
        "WHERE listing_id IS NOT NULL AND listing_id NOT LIKE '%:%'",
        dry_run=False,
    )
    _exec(
        session,
        "UPDATE listingrow SET id = CONCAT('wg-gesucht:', id) "
        "WHERE id NOT LIKE '%:%'",
        dry_run=False,
    )


def step_3_force_rescrape(session: Session, *, dry_run: bool) -> None:
    """Flip every full row to 'stub' so the next scraper pass re-fetches it.

    The existing wg-gesucht parser runs through the now-wider description
    column, overwriting the previously-truncated 255-char strings with
    the full 800-5000 char body. Cycle time at default cadence:
    ~25 passes (~2 hours) for a Munich pool of 1500 listings.
    """
    logger.info("=== Step 3: force rescrape ===")

    full_rows = _scalar(
        session,
        "SELECT COUNT(*) FROM listingrow WHERE scrape_status = 'full'",
    )
    if full_rows == 0:
        logger.info("No 'full' rows in listingrow, nothing to rescrape")
        return

    logger.info("Will mark %d 'full' rows as 'stub' for rescrape", full_rows)
    _exec(
        session,
        "UPDATE listingrow SET scrape_status = 'stub' "
        "WHERE scrape_status = 'full'",
        dry_run=dry_run,
    )


def step_4_wipe_listings(session: Session, *, dry_run: bool) -> None:
    """Wipe the global listing pool so the relaunched scraper reseeds it.

    Goal: zero rows in `listingrow` / `photorow` / `userlistingrow` and
    every `useractionrow.listing_id` set to NULL (rows preserved). Children
    nulled / deleted before the parent so no FK constraint is violated
    mid-transaction. Idempotent: a no-op when `listingrow` is already
    empty.
    """
    logger.info("=== Step 4: wipe global listing pool ===")

    listing_count = _scalar(session, "SELECT COUNT(*) FROM listingrow")
    if listing_count == 0:
        logger.info("listingrow already empty; nothing to wipe")
        return

    photo_count = _scalar(session, "SELECT COUNT(*) FROM photorow")
    user_listing_count = _scalar(session, "SELECT COUNT(*) FROM userlistingrow")
    action_fk_count = _scalar(
        session,
        "SELECT COUNT(*) FROM useractionrow WHERE listing_id IS NOT NULL",
    )
    logger.info(
        "Will wipe %d listingrow + %d photorow + %d userlistingrow rows; "
        "null %d useractionrow.listing_id values",
        listing_count,
        photo_count,
        user_listing_count,
        action_fk_count,
    )

    if dry_run:
        logger.info("(dry-run) would null useractionrow.listing_id, then DELETE userlistingrow / photorow / listingrow inside one transaction")
        return

    # One transaction; children first to keep FKs valid throughout.
    _exec(
        session,
        "UPDATE useractionrow SET listing_id = NULL WHERE listing_id IS NOT NULL",
        dry_run=False,
    )
    _exec(session, "DELETE FROM userlistingrow", dry_run=False)
    _exec(session, "DELETE FROM photorow", dry_run=False)
    _exec(session, "DELETE FROM listingrow", dry_run=False)


def verify(session: Session) -> None:
    """Print the post-migration state per plan G1, G2, G9, G_WIPE."""
    logger.info("=== Verification ===")

    bare = _scalar(
        session,
        "SELECT COUNT(*) FROM listingrow WHERE id NOT LIKE '%:%'",
    )
    logger.info("G1: %d listingrow rows still without a namespace prefix", bare)

    bad_kind = _scalar(
        session,
        "SELECT COUNT(*) FROM listingrow "
        "WHERE kind NOT IN ('wg', 'flat') OR kind IS NULL",
    )
    logger.info("G2: %d listingrow rows with kind NOT IN ('wg','flat')", bad_kind)

    desc_type = _column_type(session, "listingrow", "description")
    logger.info("G9: listingrow.description column type = %s", desc_type)

    truncated = _scalar(
        session,
        "SELECT COUNT(*) FROM listingrow "
        "WHERE scrape_status = 'full' AND CHAR_LENGTH(description) = 255",
    )
    logger.info(
        "G9: %d full rows whose description is exactly 255 chars "
        "(should reach 0 after ~2 hours of scraper cycling)",
        truncated,
    )

    listing_count = _scalar(session, "SELECT COUNT(*) FROM listingrow")
    photo_count = _scalar(session, "SELECT COUNT(*) FROM photorow")
    user_listing_count = _scalar(session, "SELECT COUNT(*) FROM userlistingrow")
    action_fk_count = _scalar(
        session,
        "SELECT COUNT(*) FROM useractionrow WHERE listing_id IS NOT NULL",
    )
    logger.info(
        "G_WIPE: %d rows in listingrow, %d in photorow, %d in userlistingrow, "
        "%d useractionrow rows with listing_id IS NOT NULL",
        listing_count,
        photo_count,
        user_listing_count,
        action_fk_count,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print every planned ALTER/UPDATE without executing it.",
    )
    parser.add_argument(
        "--skip-rescrape",
        action="store_true",
        help="Skip step 3 (the rescrape-trigger UPDATE).",
    )
    parser.add_argument(
        "--skip-wipe",
        action="store_true",
        help=(
            "Skip step 4 (the global-listing-pool wipe). Use when you only "
            "want the schema + namespacing + rescrape-trigger work."
        ),
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
            step_1_widen_and_add_kind(session, dry_run=args.dry_run)
            step_2_namespace_ids(session, dry_run=args.dry_run)
            if not args.skip_rescrape:
                step_3_force_rescrape(session, dry_run=args.dry_run)
            else:
                logger.info("Skipping step 3 (rescrape) per --skip-rescrape")
            if not args.skip_wipe:
                step_4_wipe_listings(session, dry_run=args.dry_run)
            else:
                logger.info("Skipping step 4 (wipe) per --skip-wipe")

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
