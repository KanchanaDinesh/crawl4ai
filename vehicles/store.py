"""Vehicles database: PostgreSQL schema `vehicles` (database crawl4ai; tables and views in
warehouse/schema/vehicles.sql).

    ads                      one row per ad per run: site, category (car, motorcycle), rank, title, url, price, ...
    runs                     one row per listing per run: site, category, ok?, how many ads, seconds, error
    meta                     notes (e.g. when the history from before vehicles had its own database was copied in)
    v_vehicle_ads            every ad seen, per run
    v_vehicle_ads_latest     one row per ad: latest details, first/last seen, times seen
    v_vehicle_price_changes  ads whose price changed between runs

Moved from DuckDB (data/vehicles/vehicles.duckdb) on 2026-11-06, with all its history (including the older ads it
had copied from data/scrapers/scrapers.duckdb). Each site's run is saved in one short transaction.
"""

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

AD_COLUMNS = ("rank", "title", "url", "price", "price_lkr", "location", "year", "mileage", "posted", "details", "image",
              "category")


def _price(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class VehicleStore:
    def __init__(self):
        from warehouse import postgres
        self.con = postgres.connect("writer", autocommit=True)
        self.con.execute("set timezone = 'UTC'")
        postgres.migrate(self.con, ("vehicles",))

    def save_report(self, report: dict, saved_at: str | None = None) -> int:
        """Store one site's run (its ads and how it went). Returns the number of ads saved."""
        from warehouse import edits
        from warehouse.postgres import clean_text as t, json_text
        saved_at = saved_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        site, category = report.get("site"), report.get("category")
        ads = [dict(a) for a in report.get("ads") or []]   # copies: a person's correction doesn't belong in the site's report
        for a in ads:
            a.setdefault("category", category)             # the listing's category, when the ad doesn't carry its own
        with self.con.transaction():
            cur = self.con.cursor()
            edits.protect_rows(cur, "vehicles.ads", ads, "collector:vehicles")
            if ads:
                cur.executemany(
                    f"insert into vehicles.ads (saved_at, site, {', '.join(AD_COLUMNS)}, extra) "
                    f"values ({', '.join(['%s'] * (len(AD_COLUMNS) + 3))})",
                    [(saved_at, site, *[_price(a.get(c)) if c == "price_lkr" else t(a.get(c)) for c in AD_COLUMNS],
                      json_text({k: v for k, v in a.items() if k not in AD_COLUMNS and k != "site"}))
                     for a in ads])
            cur.execute("insert into vehicles.runs (saved_at, site, source_url, success, ads, seconds, error, category) "
                        "values (%s, %s, %s, %s, %s, %s, %s, %s)",
                        [saved_at, site, report.get("source_url"), bool(report.get("success")), len(ads),
                         report.get("seconds"), t(report.get("error")), t(category)])
        return len(ads)

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
