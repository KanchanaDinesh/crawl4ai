"""DuckDB databases for all three tools, kept inside the project folder.

    data/worldnews/worldnews.duckdb    headlines, runs, sources (worldnews)
    data/techintel/techintel.duckdb    candidates, qualifications, profiles, changes, ... (techintel)
    data/scrapers/scrapers.duckdb      every scraper output (vehicles, news, products, CSE, IMDb)

One file per tool, because DuckDB allows one writer per file: the tools can run at
the same time without blocking each other. `python -m warehouse sql` opens all three
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
}


def connect(path, read_only: bool = False, wait_seconds: float = 60) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB file. If another process is writing to it, wait up to `wait_seconds`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            return duckdb.connect(str(path), read_only=read_only)
        except duckdb.IOException as e:
            if "lock" not in str(e).lower() or time.monotonic() > deadline:
                raise RuntimeError(f"{path.name} is in use by another program (another run still going?). {e}") from e
            time.sleep(1)
