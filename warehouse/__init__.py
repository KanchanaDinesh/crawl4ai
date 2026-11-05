"""DuckDB databases for all the tools, kept inside the project folder.

    data/worldnews/worldnews.duckdb    headlines, runs, sources (worldnews)
    data/techintel/techintel.duckdb    candidates, qualifications, profiles, changes, ... (techintel)
    data/scrapers/scrapers.duckdb      every scraper output (Sri Lankan news, products, CSE, IMDb)
    data/vehicles/vehicles.duckdb      vehicle ads over time, price changes (vehicles)
    data/markets/markets.duckdb        stock, index, metal, FX and crypto prices over time (markets)
    data/companies/companies.duckdb    company directory: names, websites, countries, website facts (companies)

One file per tool, because DuckDB allows one writer per file: the tools can run at
the same time without blocking each other. `python -m warehouse sql` opens them all
together (read-only) so you can query across them.
"""

import os
import time
from pathlib import Path

import duckdb

import project_paths

project_paths.apply_local_env()

DB_FILES = {
    "news": Path(os.environ.get("WORLDNEWS_DATA_DIR", project_paths.DATA_DIR / "worldnews")) / "worldnews.duckdb",
    "tech": Path(os.environ.get("TECHINTEL_DATA_DIR", project_paths.DATA_DIR / "techintel")) / "techintel.duckdb",
    "scrapers": project_paths.DATA_DIR / "scrapers" / "scrapers.duckdb",
    "markets": Path(os.environ.get("MARKETS_DATA_DIR", project_paths.DATA_DIR / "markets")) / "markets.duckdb",
    "vehicles": Path(os.environ.get("VEHICLES_DATA_DIR", project_paths.DATA_DIR / "vehicles")) / "vehicles.duckdb",
    "companies": Path(os.environ.get("COMPANIES_DATA_DIR", project_paths.DATA_DIR / "companies")) / "companies.duckdb",
}

# Tools whose data has moved to PostgreSQL (docs/postgres-migration.md, step 3): their DuckDB file above is only
# the old copy (kept until step 4, used by `copy-from-duckdb`); the tool, the dashboard and `warehouse pg` use
# PostgreSQL, and `warehouse sql` / `info` no longer open their DuckDB file.
PG_TOOLS = {"companies", "news", "markets"}


def connect(path, read_only: bool = False, wait_seconds: float = 60) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB file. If another process is writing to it, wait up to `wait_seconds`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            return duckdb.connect(str(path), read_only=read_only)
        except duckdb.IOException as e:
            if "lock" not in str(e).lower():
                raise
            if time.monotonic() > deadline:
                raise RuntimeError(f"{path.name} is in use by another program (another run still going?). {e}") from e
            time.sleep(1)
