"""IMDb scraper: Top 250 chart and the upcoming-releases calendar.

Reads the structured data embedded in the pages (JSON-LD for the Top 250,
__NEXT_DATA__ for the calendar) and upserts it into output/imdb.json, so
repeated runs update existing entries instead of duplicating them.

Usage:
    python -m scrapers.imdb
"""

import asyncio
import json
import logging

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler

from .common import load_json, save_json, setup_logging

logger = logging.getLogger(__name__)

TOP_250_URL = "https://www.imdb.com/chart/top/"
CALENDAR_URL = "https://www.imdb.com/calendar/"
OUTPUT_FILE = "imdb.json"


async def _fetch_html(crawler: AsyncWebCrawler, url: str) -> str | None:
    result = await crawler.arun(url=url)
    if not result.success:
        logger.error("Failed to fetch %s: %s", url, result.error_message)
        return None
    return result.html


def parse_top_250(html: str) -> list[dict]:
    script = BeautifulSoup(html, "html.parser").find("script", type="application/ld+json")
    if not script:
        logger.warning("No JSON-LD found on the Top 250 page.")
        return []
    try:
        data = json.loads(script.string)
    except (TypeError, json.JSONDecodeError):
        logger.error("Could not parse Top 250 JSON-LD.")
        return []

    items = []
    for entry in data.get("itemListElement", []):
        movie = entry.get("item", {})
        items.append({
            "title": movie.get("name"),
            "url": movie.get("url"),
            "image": movie.get("image"),
            "rating": movie.get("aggregateRating", {}).get("ratingValue"),
            "genre": movie.get("genre"),
            "type": movie.get("@type"),
            "rank": entry.get("position"),
            "website": "IMDb Top 250",
        })
    return items


def parse_calendar(html: str) -> list[dict]:
    script = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
    if not script:
        logger.warning("No __NEXT_DATA__ found on the calendar page.")
        return []
    try:
        data = json.loads(script.string)
    except (TypeError, json.JSONDecodeError):
        logger.error("Could not parse calendar __NEXT_DATA__.")
        return []

    items = []
    for group in data.get("props", {}).get("pageProps", {}).get("groups", []):
        for entry in group.get("entries", []):
            title_id = entry.get("id")
            items.append({
                "title": entry.get("titleText"),
                "release_date": entry.get("releaseDate") or group.get("group"),
                "type": (entry.get("titleType") or {}).get("text"),
                "genres": entry.get("genres", []),
                "year": (entry.get("releaseYear") or {}).get("year"),
                "credits": [c.get("text") for c in entry.get("principalCredits", [])],
                "url": f"https://www.imdb.com/title/{title_id}/" if title_id else None,
                "image": (entry.get("imageModel") or {}).get("url"),
                "website": "IMDb Calendar",
            })
    return items


def _key(item: dict) -> str:
    return item.get("url") or f"{item.get('title')}_{item.get('release_date')}"


async def run() -> None:
    existing = {_key(i): i for i in load_json(OUTPUT_FILE, default=[])}
    logger.info("Loaded %d existing items.", len(existing))

    async with AsyncWebCrawler() as crawler:
        new_items: list[dict] = []
        if html := await _fetch_html(crawler, TOP_250_URL):
            new_items += parse_top_250(html)
        if html := await _fetch_html(crawler, CALENDAR_URL):
            new_items += parse_calendar(html)

    added = sum(1 for i in new_items if _key(i) not in existing)
    for item in new_items:
        existing[_key(item)] = item

    path = save_json(list(existing.values()), OUTPUT_FILE)
    logger.info("Added %d, updated %d. %d items total in %s",
                added, len(new_items) - added, len(existing), path)


def main() -> None:
    setup_logging()
    asyncio.run(run())


if __name__ == "__main__":
    main()
