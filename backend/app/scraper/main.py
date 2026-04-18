"""Scraper container entrypoint: `python -m app.scraper.main`.

Calls `db.init_db()` (which runs `SQLModel.metadata.create_all` under the
hood) against the shared MySQL instance, then blocks on
`ScraperAgent.run_forever()`.
"""

from __future__ import annotations

import asyncio
import logging

from ..wg_agent import db as db_module
from .agent import ScraperAgent

logger = logging.getLogger(__name__)


async def _main() -> None:
    db_module.init_db()
    logger.info("Scraper database URL: %s", db_module.DATABASE_URL)
    await ScraperAgent().run_forever()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Scraper interrupted, shutting down.")


if __name__ == "__main__":
    main()
