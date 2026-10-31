"""Shared helpers for the scrapers: paths, config loading, saving output."""

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
CONFIG_DIR = PACKAGE_DIR / "config"
# Everything a scraper writes goes here (git-ignored). Override with SCRAPERS_OUTPUT_DIR.
OUTPUT_DIR = Path(os.environ.get("SCRAPERS_OUTPUT_DIR", REPO_ROOT / "output"))

# A desktop browser User-Agent for plain HTTP calls (aiohttp).
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s - %(levelname)s - %(message)s")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_config(name: str) -> Any:
    """Load a JSON file from scrapers/config/."""
    with open(CONFIG_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


def output_path(filename: str) -> Path:
    path = OUTPUT_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_json(filename: str, default: Any = None) -> Any:
    """Load a JSON file from the output directory, or return `default`."""
    try:
        with open(OUTPUT_DIR / filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(data: Any, filename: str) -> Path:
    path = output_path(filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def save_csv(rows: Iterable[dict], filename: str) -> Path:
    rows = list(rows)
    path = output_path(filename)
    fieldnames: list[str] = []
    for row in rows:  # union of keys, in first-seen order
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path
