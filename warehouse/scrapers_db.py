"""The scrapers' database: PostgreSQL schema `scrapers` (database crawl4ai; tables and views in
warehouse/schema/scrapers.sql). Moved from data/scrapers/scrapers.duckdb on 2026-11-06.

Every file a scraper saves (scrapers/common.save_json / save_csv) is also stored here,
one row per record, so results build up into a history instead of being overwritten.

    scraper_rows             saved_at, scraper (file name without extension), row_no, data (JSON)
    v_news_headlines         scrapers.news headlines per run
    v_products               product listings per run

(Vehicle ads have their own schema, `vehicles`; older vehicle rows from before that are still in scraper_rows.)
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def rows_of(data) -> list:
    """Split a saved document into records: a list -> its items; a report with "ads" -> its ads."""
    if isinstance(data, dict) and isinstance(data.get("ads"), list):
        return [{**ad, "site": data.get("site")} for ad in data["ads"]]
    if isinstance(data, list):
        return data
    return [data]


def record_output(scraper: str, data) -> int:
    """Store one saved output, in one transaction. Never raises: a database problem must not lose the scrape."""
    try:
        from . import postgres
        rows = rows_of(data)
        saved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with postgres.connect("writer", autocommit=True) as con:
            postgres.migrate(con, ("scrapers",))
            if rows:
                with con.transaction():
                    con.cursor().executemany(
                        "insert into scrapers.scraper_rows (saved_at, scraper, row_no, data) values (%s, %s, %s, %s)",
                        [(saved_at, scraper, n, postgres.json_text(r)) for n, r in enumerate(rows, 1)])
        return len(rows)
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not save %s to the database (the file was still saved): %s", scraper, e)
        return 0
