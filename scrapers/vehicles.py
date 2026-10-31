"""Latest vehicle ads from ikman.lk and riyasewana.com (or any site in the config).

Each site in scrapers/config/vehicle_sites.json describes:
  item_selector     CSS selector for one ad on the listing page
  exclude_selector  ads to skip (promoted / "top" ads), optional
  fields            name -> {selector, type, attribute?, contains?}
                    selector:  a CSS selector or a list of fallbacks (first match wins)
                    type:      "text" or "attribute"
                    attribute: attribute name, or a list of fallbacks (e.g. ["data-src", "src"])
                    contains:  only use an element whose text contains this string
  limit             how many ads to keep (default 10)
  sort              optional {"by": "url", "pattern": "-(\\d+)$", "order": "desc"}: sort by a
                    number taken from a field (e.g. the ad id in the URL) before applying limit
  crawl             extra crawl4ai CrawlerRunConfig options (magic, page_timeout, wait_for, ...)

Each site is written to its own JSON file in output/ and printed as a JSON object.
If a site yields no ads, its HTML is saved to output/html_dumps/ so the selectors
can be fixed with `python -m scrapers.inspect_site <dump file> --base "<selector>"`.

Usage:
    python -m scrapers.vehicles                    # all enabled sites
    python -m scrapers.vehicles --site ikman
    python -m scrapers.vehicles --limit 5 --save-html
    python -m scrapers.vehicles --list
"""

import argparse
import asyncio
import json
import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

from .common import load_config, now_str, output_path, save_json, setup_logging

logger = logging.getLogger(__name__)

LINK_FIELDS = ("url", "image")


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_field(item: Tag, spec: dict) -> str | None:
    """Apply one field spec to one ad element. Returns None if nothing matched."""
    contains = spec.get("contains")
    for selector in _as_list(spec.get("selector")):
        elements = [item] if selector in ("", ":scope") else item.select(selector)
        for el in elements:
            text = _clean(el.get_text(" "))
            if contains and contains.lower() not in text.lower():
                continue
            if spec.get("type", "text") == "attribute":
                for attr in _as_list(spec.get("attribute")):
                    value = el.get(attr)
                    if value and not str(value).startswith("data:"):  # skip inline placeholders
                        return str(value).strip()
                continue
            if text:
                return text
    return None


def parse_price(price: str | None) -> int | None:
    """'Rs 4,500,000' -> 4500000. Returns None for 'Negotiable', lakhs/millions text, etc."""
    if not price or re.search(r"lakh|mn|million", price, re.I):
        return None
    match = re.search(r"\d[\d,]*(?:\.\d+)?", price)
    return int(float(match.group().replace(",", ""))) if match else None


def _sort_ads(ads: list[dict], sort: dict) -> list[dict]:
    """Sort by a number pulled out of a field, e.g. the ad id at the end of the URL.

    Pinned/promoted ads are often older, so sorting by ad id gives the truly latest ads.
    """
    pattern = re.compile(sort.get("pattern", r"(\d+)"))
    field = sort.get("by", "url")

    def key(ad: dict) -> int:
        match = pattern.search(ad.get(field) or "")
        return int(match.group(1)) if match else -1

    return sorted(ads, key=key, reverse=sort.get("order", "desc") == "desc")


def parse_ads(html: str, site: dict, limit: int) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    excluded = set(map(id, soup.select(site["exclude_selector"]))) if site.get("exclude_selector") else set()

    ads: list[dict] = []
    seen: set[str] = set()
    for item in soup.select(site["item_selector"]):
        if id(item) in excluded:
            continue
        ad = {name: extract_field(item, spec) for name, spec in site["fields"].items()}
        for key in LINK_FIELDS:
            if ad.get(key):
                ad[key] = urljoin(site["url"], ad[key])
        if not ad.get("title") or not ad.get("url") or ad["url"] in seen:
            continue
        seen.add(ad["url"])
        if "price" in ad:
            ad["price_lkr"] = parse_price(ad["price"])
        ads.append(ad)

    if site.get("sort"):
        ads = _sort_ads(ads, site["sort"])
    return [{"rank": i, **ad} for i, ad in enumerate(ads[:limit], 1)]


def _dump_html(site: dict, html: str) -> str:
    path = output_path(f"html_dumps/{site['name']}.html")
    path.write_text(html, encoding="utf-8")
    return str(path)


async def scrape_site(crawler: AsyncWebCrawler, site: dict, limit: int, save_html: bool) -> dict:
    logger.info("Crawling %s (%s)", site["name"], site["url"])
    run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, **site.get("crawl", {}))
    result = await crawler.arun(url=site["url"], config=run_config)

    report = {
        "site": site["name"],
        "source_url": site["url"],
        "fetched_at": now_str(),
        "success": bool(result.success),
        "count": 0,
        "ads": [],
    }
    if not result.success:
        report["error"] = result.error_message
        logger.error("Failed to crawl %s: %s", site["name"], result.error_message)
        return report

    report["ads"] = parse_ads(result.html, site, limit)
    report["count"] = len(report["ads"])

    if save_html or not report["ads"]:
        dump = _dump_html(site, result.html)
        if not report["ads"]:
            logger.warning(
                "%s: 0 ads matched '%s'. Page saved to %s. Inspect it with:\n"
                "  python -m scrapers.inspect_site %s",
                site["name"], site["item_selector"], dump, dump,
            )
    logger.info("%s: %d ads", site["name"], report["count"])
    return report


async def run(sites: list[dict], limit: int | None, save_html: bool, quiet: bool) -> list[dict]:
    reports = []
    browser = BrowserConfig(headless=True, enable_stealth=True)
    async with AsyncWebCrawler(config=browser) as crawler:
        for site in sites:
            report = await scrape_site(crawler, site, limit or site.get("limit", 10), save_html)
            path = save_json(report, site["output_file"])
            logger.info("Saved %s", path)
            if not quiet:
                print(json.dumps(report, indent=2, ensure_ascii=False))
            reports.append(report)
    return reports


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Scrape the latest vehicle ads.")
    parser.add_argument("--site", action="append", help="Site name to scrape (repeatable).")
    parser.add_argument("--limit", type=int, help="Ads per site (overrides the config's 'limit').")
    parser.add_argument("--save-html", action="store_true", help="Also save each page to output/html_dumps/.")
    parser.add_argument("--quiet", action="store_true", help="Don't print the JSON, only save it.")
    parser.add_argument("--list", action="store_true", help="List configured sites and exit.")
    args = parser.parse_args()

    sites = load_config("vehicle_sites.json")
    if args.list:
        for s in sites:
            print(f"{'on ' if s.get('enabled', True) else 'off'}  {s['name']:<12} {s['url']}")
        return

    if args.site:
        wanted = {n.lower() for n in args.site}
        sites = [s for s in sites if s["name"].lower() in wanted]
        if not sites:
            parser.error("No matching sites. Use --list.")
    else:
        sites = [s for s in sites if s.get("enabled", True)]

    asyncio.run(run(sites, args.limit, args.save_html, args.quiet))


if __name__ == "__main__":
    main()
