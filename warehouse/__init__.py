"""The project's database: PostgreSQL, database `crawl4ai`, one schema per tool (see postgres.py).

    news        world news headlines, runs, sources (worldnews)
    tech        candidates, qualifications, profiles, changes, companies, ... (techintel)
    markets     stock, index, metal, FX and crypto prices over time (markets)
    vehicles    vehicle ads over time, price changes (vehicles)
    companies   company directory: names, websites, countries, website facts (companies)
    scrapers    every output of the older scrapers (Sri Lankan news, products, CSE, IMDb)

Until 2026-11-06 each tool had its own DuckDB file in data/<tool>/, which let only one program use it at a time;
those files are kept in data/archive/duckdb-2026-11/ (docs/postgres-migration.md).

DATA_DIRS are the folders for the files a tool keeps next to the database (run snapshots, raw page text,
downloads waiting to be imported).
"""

import os
from pathlib import Path

import project_paths

project_paths.apply_local_env()

DATA_DIRS = {
    "news": Path(os.environ.get("WORLDNEWS_DATA_DIR", project_paths.DATA_DIR / "worldnews")),
    "tech": Path(os.environ.get("TECHINTEL_DATA_DIR", project_paths.DATA_DIR / "techintel")),
    "scrapers": project_paths.DATA_DIR / "scrapers",
    "markets": Path(os.environ.get("MARKETS_DATA_DIR", project_paths.DATA_DIR / "markets")),
    "vehicles": Path(os.environ.get("VEHICLES_DATA_DIR", project_paths.DATA_DIR / "vehicles")),
    "companies": Path(os.environ.get("COMPANIES_DATA_DIR", project_paths.DATA_DIR / "companies")),
}
