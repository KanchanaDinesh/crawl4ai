"""Product listing scraper driven by scrapers/config/product_sites.json.

Replaces product_list.py and multi_site_scraper.py (both scraped Damro
with the same schema). Each site uses a crawl4ai JsonCssExtractionStrategy
schema and is saved to its own file in output/.

Usage:
    python -m scrapers.products                  # all enabled sites
    python -m scrapers.products --site Damro
    python -m scrapers.products --list
"""

import argparse
import asyncio
import json
import logging
from urllib.parse import urljoin

from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig, JsonCssExtractionStrategy

from .common import load_config, save_json, setup_logging

logger = logging.getLogger(__name__)

# Fields whose values are links and should be made absolute.
LINK_FIELDS = ("url", "image")


async def scrape_site(crawler: AsyncWebCrawler, site: dict) -> list[dict]:
    logger.info("Crawling %s (%s)", site["name"], site["url"])
    result = await crawler.arun(
        url=site["url"],
        config=CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            extraction_strategy=JsonCssExtractionStrategy(site["schema"]),
        ),
    )
    if not result.success:
        logger.error("Failed to crawl %s: %s", site["name"], result.error_message)
        return []

    items = json.loads(result.extracted_content or "[]")
    for item in items:
        for key in LINK_FIELDS:
            if isinstance(item.get(key), str):
                item[key] = urljoin(site["url"], item[key])

    path = save_json(items, site["output_file"])
    logger.info("Saved %d items from %s to %s", len(items), site["name"], path)
    return items


async def run(sites: list[dict]) -> None:
    async with AsyncWebCrawler() as crawler:
        for site in sites:
            await scrape_site(crawler, site)


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Scrape product listings.")
    parser.add_argument("--site", action="append", help="Site name to scrape (repeatable).")
    parser.add_argument("--list", action="store_true", help="List configured sites and exit.")
    args = parser.parse_args()

    sites = load_config("product_sites.json")
    if args.list:
        for s in sites:
            print(f"{'on ' if s.get('enabled', True) else 'off'}  {s['name']:<22} {s['url']}")
        return

    if args.site:
        wanted = {n.lower() for n in args.site}
        sites = [s for s in sites if s["name"].lower() in wanted]
        if not sites:
            parser.error("No matching sites. Use --list.")
    else:
        sites = [s for s in sites if s.get("enabled", True)]

    asyncio.run(run(sites))


if __name__ == "__main__":
    main()
