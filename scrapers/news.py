"""News headline scraper driven by scrapers/config/news_sites.json.

Replaces the old news.py, news_scraper.py, "news_scraper copy.py" and
sunday_times_scraper.py, which were copies of one script with different
sites commented in or out. Sites are now switched on and off with the
"enabled" flag in the config file instead.

Usage:
    python -m scrapers.news                      # all enabled sites
    python -m scrapers.news --site "Daily Mirror" --site CBSL
    python -m scrapers.news --all                # include disabled sites
    python -m scrapers.news --list
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass, fields
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig

from .common import load_config, now_str, save_json, setup_logging

logger = logging.getLogger(__name__)


@dataclass
class NewsSite:
    name: str
    url: str
    domain: str
    base_selector: str
    title_selector: str
    url_selector: str
    country: str = "LK"
    category: str = "tech"
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "NewsSite":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def load_sites() -> list[NewsSite]:
    return [NewsSite.from_dict(d) for d in load_config("news_sites.json")]


def parse_headlines(html: str, site: NewsSite) -> list[dict]:
    """Pull (title, absolute url) pairs out of a page using the site's selectors."""
    soup = BeautifulSoup(html, "html.parser")
    headlines = []
    for block in soup.select(site.base_selector):
        title_el = block.select_one(site.title_selector)
        url_el = block.select_one(site.url_selector)
        if not (title_el and url_el and url_el.get("href")):
            continue
        title = title_el.get_text(strip=True)
        if title:
            headlines.append({"title": title, "url": urljoin(site.url, url_el["href"])})
    return headlines


async def scrape_site(crawler: AsyncWebCrawler, site: NewsSite) -> list[dict]:
    logger.info("Scraping %s (%s)", site.name, site.url)
    result = await crawler.arun(url=site.url, config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS))
    if not result.success:
        logger.error("Failed to crawl %s: %s", site.name, result.error_message)
        return []
    headlines = parse_headlines(result.html, site)
    logger.info("Extracted %d items from %s", len(headlines), site.name)
    return headlines


async def run(sites: list[NewsSite], output_file: str) -> list[dict]:
    items: list[dict] = []
    seen_urls: set[str] = set()
    async with AsyncWebCrawler() as crawler:
        for site in sites:
            fetched_at = now_str()
            for headline in await scrape_site(crawler, site):
                if headline["url"] in seen_urls:
                    continue
                seen_urls.add(headline["url"])
                items.append({
                    **headline,
                    "name": site.name,
                    "source_url": site.url,
                    "domain": site.domain,
                    "fetch_date": fetched_at,
                    "score": 0,
                    "item_number": len(items) + 1,
                    "country": site.country,
                    "category": site.category,
                })

    if items:
        path = save_json(items, output_file)
        logger.info("Saved %d items to %s", len(items), path)
    else:
        logger.warning("No news items extracted.")
    return items


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Scrape news headlines.")
    parser.add_argument("--site", action="append", help="Site name to scrape (repeatable).")
    parser.add_argument("--all", action="store_true", help="Include sites with enabled=false.")
    parser.add_argument("--list", action="store_true", help="List configured sites and exit.")
    parser.add_argument("--output", default="news.json", help="Output filename in output/.")
    args = parser.parse_args()

    sites = load_sites()
    if args.list:
        for s in sites:
            print(f"{'on ' if s.enabled else 'off'}  {s.name:<15} {s.url}")
        return

    if args.site:
        wanted = {n.lower() for n in args.site}
        sites = [s for s in sites if s.name.lower() in wanted]
        missing = wanted - {s.name.lower() for s in sites}
        if missing:
            parser.error(f"Unknown site(s): {', '.join(sorted(missing))}. Use --list.")
    elif not args.all:
        sites = [s for s in sites if s.enabled]

    asyncio.run(run(sites, args.output))


if __name__ == "__main__":
    main()
