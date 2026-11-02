"""Vehicles database: data/vehicles/vehicles.duckdb (like markets, worldnews and techintel have their own).

    ads                      one row per ad per run: site, rank, title, url, price, price_lkr, location, year, ...
    runs                     one row per site per run: ok?, how many ads, seconds, error
    v_vehicle_ads            every ad seen, per run
    v_vehicle_ads_latest     one row per ad: latest details, first/last seen, times seen
    v_vehicle_price_changes  ads whose price changed between runs

Older history (from before vehicles had its own database) is copied in once, automatically, from
data/scrapers/scrapers.duckdb. The old rows are left there untouched.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

AD_COLUMNS = ("rank", "title", "url", "price", "price_lkr", "location", "year", "mileage", "posted", "details", "image")

SCHEMA = """
create table if not exists ads (
    saved_at timestamptz, site varchar, rank integer, title varchar, url varchar, price varchar, price_lkr bigint,
    location varchar, year varchar, mileage varchar, posted varchar, details varchar, image varchar, extra json);
create table if not exists runs (
    saved_at timestamptz, site varchar, source_url varchar, success boolean, ads integer, seconds double, error varchar);
create table if not exists meta (key varchar primary key, value varchar);

create or replace view v_vehicle_ads as
    select saved_at, site, title, url, price, price_lkr, location, year, mileage, posted, image, rank, details from ads;

create or replace view v_vehicle_ads_latest as
    select *, min(saved_at) over (partition by url) as first_seen, count(*) over (partition by url) as times_seen
    from v_vehicle_ads qualify row_number() over (partition by url order by saved_at desc) = 1;

create or replace view v_vehicle_price_changes as
    select url, site, title, saved_at, price_lkr,
           lag(price_lkr) over (partition by url order by saved_at) as previous_price_lkr
    from v_vehicle_ads
    qualify previous_price_lkr is not null and previous_price_lkr <> price_lkr;
"""

MIGRATED = "imported_from_scrapers_db"


def db_path() -> Path:
    from warehouse import DB_FILES
    return DB_FILES["vehicles"]


def _price(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class VehicleStore:
    def __init__(self, path: Path | None = None):
        from warehouse import connect
        self.path = Path(path or db_path())
        self.con = connect(self.path)
        self.con.execute(SCHEMA)
        self._import_old_history()

    def save_report(self, report: dict, saved_at: str | None = None) -> int:
        """Store one site's run (its ads and how it went). Returns the number of ads saved."""
        saved_at = saved_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        site = report.get("site")
        ads = report.get("ads") or []
        if ads:      # (DuckDB rejects an empty batch)
            self.con.executemany(
                f"insert into ads (saved_at, site, {', '.join(AD_COLUMNS)}, extra) "
                f"values ({', '.join(['?'] * (len(AD_COLUMNS) + 3))})",
                [(saved_at, site, *[_price(a.get(c)) if c == "price_lkr" else a.get(c) for c in AD_COLUMNS],
                  json.dumps({k: v for k, v in a.items() if k not in AD_COLUMNS and k != "site"}, default=str))
                 for a in ads])
        self.con.execute("insert into runs values (?, ?, ?, ?, ?, ?, ?)",
                         [saved_at, site, report.get("source_url"), bool(report.get("success")), len(ads),
                          report.get("seconds"), report.get("error")])
        return len(ads)

    def _import_old_history(self) -> None:
        """One time: copy the vehicle ads saved in data/scrapers/scrapers.duckdb before this database existed."""
        if self.con.execute("select 1 from meta where key = ?", [MIGRATED]).fetchone():
            return
        from warehouse import DB_FILES
        old = DB_FILES["scrapers"]
        copied = 0
        if old.exists() and old.resolve() != self.path.resolve():
            try:
                self.con.execute(f"attach '{old}' as old_scrapers (read_only)")
            except Exception as e:  # noqa: BLE001 - e.g. a scraper is writing to it right now: try again next time
                logger.info("Old vehicle history not imported yet (%s); will try again on the next run", e)
                return
            try:
                has_table = self.con.execute(
                    "select count(*) from information_schema.tables where table_catalog = 'old_scrapers' "
                    "and table_name = 'scraper_rows'").fetchone()[0]
                if has_table:
                    before = self.con.execute("select count(*) from ads").fetchone()[0]
                    self.con.execute(f"""
                        insert into ads
                        select saved_at, replace(scraper, 'vehicles_', ''), try_cast(data->>'rank' as integer),
                               data->>'title', data->>'url', data->>'price', try_cast(data->>'price_lkr' as bigint),
                               data->>'location', data->>'year', data->>'mileage', data->>'posted', data->>'details',
                               data->>'image', '{{}}'::json
                        from old_scrapers.scraper_rows where scraper like 'vehicles_%'""")
                    self.con.execute("""
                        insert into runs
                        select saved_at, replace(scraper, 'vehicles_', ''), null, true, count(*), null, null
                        from old_scrapers.scraper_rows where scraper like 'vehicles_%' group by all""")
                    copied = self.con.execute("select count(*) from ads").fetchone()[0] - before
            finally:
                self.con.execute("detach old_scrapers")
        self.con.execute("insert or replace into meta values (?, ?)",
                         [MIGRATED, f"{copied} ads on {datetime.now(timezone.utc):%Y-%m-%d %H:%M}"])
        if copied:
            logger.info("Copied %d older vehicle ads from %s into %s", copied, old.name, self.path.name)

    def close(self) -> None:
        self.con.close()


def save_report(report: dict) -> int:
    """Save one site's report. Never raises: a database problem must not lose the scrape (the JSON is saved too)."""
    try:
        store = VehicleStore()
        try:
            return store.save_report(report)
        finally:
            store.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not save %s to the vehicles database (the JSON file was still saved): %s",
                       report.get("site"), e)
        return 0
