"""Scrape the latest vehicle ads from each site in vehicles/config/sites.json (ikman.lk, riyasewana.com, ...).

Each site in the config describes:
  item_selector     CSS selector for one ad on the listing page
  exclude_selector  ads to skip (promoted / "top" ads), optional
  fields            name -> {selector, type, attribute?, contains?}
                    selector:  a CSS selector or a list of fallbacks (first match wins)
                    type:      "text" or "attribute"
                    attribute: attribute name, or a list of fallbacks (e.g. ["data-src", "src"])
                    contains:  only use an element whose text contains this string
  limit             how many ads to keep (default 10)
  max_seconds       give up on this site after this long (default 120)
  sort              optional {"by": "url", "pattern": "-(\\d+)$", "order": "desc"}: sort by a
                    number taken from a field (e.g. the ad id in the URL) before applying limit
  image_by_slug     optional URL prefix of the site's image server (ikman: "https://i.ikman-st.com/").
                    Photos load only when scrolled into view, but their URLs are already in the page and
                    contain the ad's slug (the last part of its URL), so each ad gets its photo from there.
  crawl             extra crawl4ai CrawlerRunConfig options (page_timeout, wait_for, delay_before_return_html, ...)
  enabled           false to skip the site in normal runs

The browser is a plain headless Chromium: no stealth settings and no "magic" mode (both are made to
hide automation from bot detection, which this project doesn't do). Both sites' robots.txt allow
these pages.

Each site is written to output/vehicles_<site>.json and kept in data/vehicles/vehicles.duckdb
(history, price changes: see store.py). If a site yields no ads, its HTML is saved to output/html_dumps/ so the
selectors can be fixed with `python -m scrapers.inspect_site <dump file> --base "<selector>"`.
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

import project_paths
from scrapers.common import now_str, output_path

from . import store

logger = logging.getLogger(__name__)

LINK_FIELDS = ("url", "image")
SITES_FILE = Path(__file__).resolve().parent / "config" / "sites.json"


def load_sites(path: Path | str | None = None) -> list[dict]:
    with open(path or SITES_FILE, encoding="utf-8") as f:
        return json.load(f)


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


def images_by_slug(html: str, prefix: str) -> dict[str, str]:
    """{ad slug: image URL} for image URLs like <prefix><slug>/<id>/142/107/cropped.jpg in the page."""
    out: dict[str, str] = {}
    for url in re.findall(re.escape(prefix) + r"""[^"'\s\\<>]+""", html):
        slug = url[len(prefix):].split("/", 1)[0]
        out.setdefault(slug, url)
    return out


def parse_ads(html: str, site: dict, limit: int) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    photos = images_by_slug(html, site["image_by_slug"]) if site.get("image_by_slug") else {}
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
        if photos and not ad.get("image"):
            ad["image"] = photos.get(ad["url"].rstrip("/").rsplit("/", 1)[-1])
        ads.append(ad)

    if site.get("sort"):
        ads = _sort_ads(ads, site["sort"])
    return [{"rank": i, **ad} for i, ad in enumerate(ads[:limit], 1)]


def save_output(report: dict, filename: str) -> Path:
    """The latest run as a JSON file in output/ (the history is in the vehicles database)."""
    path = output_path(filename)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


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
    return report


SITE_SECONDS = 120   # give up on one site after this long, whatever the page is doing
START_SECONDS = 60   # the browser must start within this long
CLOSE_SECONDS = 15


def _failed(site: dict, error: str) -> dict:
    return {"site": site["name"], "source_url": site["url"], "fetched_at": now_str(), "success": False,
            "count": 0, "ads": [], "error": error[:400]}


async def _open(browser: BrowserConfig) -> AsyncWebCrawler:
    crawler = AsyncWebCrawler(config=browser)
    try:
        await asyncio.wait_for(crawler.__aenter__(), START_SECONDS)
    except asyncio.TimeoutError:
        await _close(crawler)
        raise RuntimeError(f"the browser didn't start within {START_SECONDS}s (run: uv run python doctor.py)") from None
    except Exception as e:  # noqa: BLE001 - e.g. browser missing or can't run from this drive
        await _close(crawler)
        first = next((ln.strip() for ln in str(e).splitlines() if ln.strip()), type(e).__name__)
        raise RuntimeError(f"the browser could not start: {first[:300]} (run: uv run python doctor.py)") from None
    return crawler


async def _close(crawler: AsyncWebCrawler | None) -> None:
    if crawler is None:
        return
    try:
        await asyncio.wait_for(crawler.__aexit__(None, None, None), CLOSE_SECONDS)
    except BaseException:  # noqa: BLE001 - a wedged browser must not block the run
        pass


async def run(sites: list[dict], limit: int | None, save_html: bool, quiet: bool,
              max_seconds: float | None = None) -> list[dict]:
    """Scrape each site. A site gets at most `max_seconds` (default SITE_SECONDS, or its config's
    "max_seconds"); a slow or stuck page is abandoned and the next site gets a fresh browser."""
    reports = []
    browser = BrowserConfig(headless=True, extra_args=project_paths.chromium_args())   # plain: no stealth
    crawler = None
    try:
        for site in sites:
            n = limit or site.get("limit", 10)
            budget = max_seconds or site.get("max_seconds", SITE_SECONDS)
            began = time.monotonic()
            for attempt in (1, 2):
                if crawler is None:
                    logger.info("Starting the browser...")
                    crawler = await _open(browser)
                try:
                    report = await asyncio.wait_for(scrape_site(crawler, site, n, save_html), budget)
                    died = not report["success"] and _browser_died(report.get("error"))
                except asyncio.TimeoutError:
                    report = _failed(site, f"gave up after {budget:.0f}s: the page didn't finish loading "
                                           "(slow connection, or the site is blocking the browser)")
                    logger.error("%s: %s", site["name"], report["error"])
                    await _close(crawler)        # the page may still be hanging: start clean for the next site
                    crawler = None
                    break
                except Exception as e:  # noqa: BLE001
                    if not _browser_died(str(e)):
                        raise
                    report, died = _failed(site, f"{e} (run: uv run python doctor.py)"), True
                if not died:
                    break
                await _close(crawler)            # Chromium crashed: start a fresh one and retry this site once
                crawler = None
                if attempt == 1:
                    logger.warning("%s: browser closed unexpectedly; restarting it and retrying", site["name"])
            report["seconds"] = round(time.monotonic() - began, 1)
            logger.info("%s: %d ads in %.1fs", site["name"], report["count"], report["seconds"])
            path = save_output(report, site["output_file"])
            saved = store.save_report(report)
            logger.info("Saved %s (+%d ads in %s)", path, saved, store.db_path().name)
            if not quiet:
                print(json.dumps(report, indent=2, ensure_ascii=False))
            reports.append(report)
    finally:
        await _close(crawler)
    return reports


def _browser_died(error: str | None) -> bool:
    low = (error or "").lower()
    return any(s in low for s in ("has been closed", "target closed", "browser closed", "browser has disconnected"))
