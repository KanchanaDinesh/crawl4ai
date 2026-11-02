"""The scrapers' database: data/scrapers/scrapers.duckdb.

Every file a scraper saves (scrapers/common.save_json / save_csv) is also stored here,
one row per record, so results build up into a history instead of being overwritten.

    scraper_rows             saved_at, scraper (file name without extension), row_no, data (JSON)
    (vehicle ads have their own database now: data/vehicles/vehicles.duckdb, see vehicles/store.py;
     older vehicle rows stay in scraper_rows and were copied there automatically)
    v_news_headlines         scrapers.news headlines per run
    v_products               product listings per run
"""

import json
import logging
from datetime import datetime, timezone

from . import DB_FILES, connect

logger = logging.getLogger(__name__)

SCHEMA = """
create sequence if not exists scraper_row_seq;
create table if not exists scraper_rows (
    row_id bigint primary key default nextval('scraper_row_seq'),
    saved_at timestamptz, scraper varchar, row_no integer, data json);

drop view if exists v_vehicle_price_changes;
drop view if exists v_vehicle_ads_latest;
drop view if exists v_vehicle_ads;

create or replace view v_news_headlines as
    select saved_at, data->>'name' as site, data->>'title' as title, data->>'url' as url,
           data->>'country' as country, data->>'category' as category
    from scraper_rows where scraper = 'news';

create or replace view v_products as
    select saved_at, scraper, data->>'title' as title, data->>'url' as url, data->>'price' as price, data->>'image' as image
    from scraper_rows where scraper like 'products_%';
"""


def rows_of(data) -> list:
    """Split a saved document into records: a list -> its items; a report with "ads" -> its ads."""
    if isinstance(data, dict) and isinstance(data.get("ads"), list):
        return [{**ad, "site": data.get("site")} for ad in data["ads"]]
    if isinstance(data, list):
        return data
    return [data]


def init(con) -> None:
    con.execute(SCHEMA)


def record_output(scraper: str, data) -> int:
    """Store one saved output. Never raises: a database problem must not lose the scrape."""
    try:
        rows = rows_of(data)
        saved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        con = connect(DB_FILES["scrapers"])
        try:
            init(con)
            if rows:
                con.executemany("insert into scraper_rows (saved_at, scraper, row_no, data) values (?, ?, ?, ?)",
                                [(saved_at, scraper, n, json.dumps(r, ensure_ascii=False, default=str))
                                 for n, r in enumerate(rows, 1)])
        finally:
            con.close()
        return len(rows)
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not save %s to the database (the file was still saved): %s", scraper, e)
        return 0
