"""Selector-finding helper for building new scraper configs.

Replaces site_inspector.py, inspect_broadcom.py, inspect_imdb.py,
inspect_news_sites.py, analyze_broadcom_html.py, analyze_veeam.py and
verify_selector.py with one tool.

TARGET can be a URL, a site name from config/news_sites.json, or a local
HTML file (e.g. a dump saved earlier with --save-html).

Usage:
    python -m scrapers.inspect_site "Daily Mirror"           # test the configured selectors
    python -m scrapers.inspect_site https://example.com      # no selector: suggest candidates
    python -m scrapers.inspect_site https://example.com --base "article" --title "h2 a"
    python -m scrapers.inspect_site https://example.com --save-html
    python -m scrapers.inspect_site output/html_dumps/example.html --base "div.card"
"""

import argparse
import asyncio
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig

from .common import load_config, output_path


async def fetch_html(url: str) -> str | None:
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=url, config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS))
    if not result.success:
        print(f"Error fetching {url}: {result.error_message}")
        return None
    return result.html


def test_selectors(soup: BeautifulSoup, base_url: str, base: str, title: str | None,
                   link: str | None, limit: int) -> None:
    items = soup.select(base)
    print(f"Base selector '{base}' matched {len(items)} elements.")
    for i, item in enumerate(items[:limit], 1):
        print(f"\n[{i}]")
        if title:
            el = item.select_one(title)
            print(f"  title: {el.get_text(strip=True) if el else f'(nothing for {title!r})'}")
        if link:
            el = item.select_one(link)
            href = el.get("href") if el else None
            print(f"  url:   {urljoin(base_url, href) if href else f'(nothing for {link!r})'}")
        for a in item.find_all("a")[:5]:
            print(f"    <a> {a.get_text(strip=True)[:40]!r} -> {a.get('href')}")


def suggest_selectors(soup: BeautifulSoup, limit: int) -> None:
    """No selector given: show likely headline links and common container classes."""
    print(f"<article> tags: {len(soup.find_all('article'))}")

    print("\nLikely headline links (text longer than 20 chars):")
    parents: Counter[str] = Counter()
    shown = 0
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if len(text) <= 20:
            continue
        parent = a.parent
        parent_sel = parent.name + "".join(f".{c}" for c in parent.get("class", []))
        parents[parent_sel] += 1
        if shown < limit:
            print(f"  {text[:60]!r}\n    href={a['href']}  parent={parent_sel}")
            shown += 1

    print("\nMost common parents of those links (good base/title selector candidates):")
    for sel, count in parents.most_common(8):
        print(f"  {count:>4}  {sel}")

    pattern = re.compile(r"post|news|article|card|story|item|blog", re.I)
    classes: Counter[str] = Counter(
        f"{tag.name}.{cls}" for tag in soup.find_all(class_=pattern)
        for cls in tag.get("class", []) if pattern.search(cls)
    )
    print("\nRepeated container classes:")
    for sel, count in classes.most_common(10):
        print(f"  {count:>4}  {sel}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a page to find CSS selectors.")
    parser.add_argument("target", help="URL, configured news site name, or local HTML file")
    parser.add_argument("--base", help="CSS selector for each repeated item")
    parser.add_argument("--title", help="CSS selector for the title inside each item")
    parser.add_argument("--link", help="CSS selector for the link inside each item")
    parser.add_argument("--limit", type=int, default=5, help="How many matches to preview")
    parser.add_argument("--save-html", action="store_true", help="Save fetched HTML to output/html_dumps/")
    args = parser.parse_args()

    sites = {s["name"].lower(): s for s in load_config("news_sites.json")}
    site = sites.get(args.target.lower())
    base, title, link = args.base, args.title, args.link
    if site:
        url = site["url"]
        base = base or site["base_selector"]
        title = title or site["title_selector"]
        link = link or site["url_selector"]
    else:
        url = args.target

    if Path(url).is_file():
        html = Path(url).read_text(encoding="utf-8")
        base_url = ""
    else:
        html = asyncio.run(fetch_html(url))
        base_url = url
        if html and args.save_html:
            name = re.sub(r"[^\w.-]+", "_", urlparse(url).netloc + urlparse(url).path).strip("_")
            path = output_path(f"html_dumps/{name}.html")
            path.write_text(html, encoding="utf-8")
            print(f"Saved HTML to {path}")
    if not html:
        return

    print(f"\n=== {args.target} ({len(html):,} chars of HTML) ===\n")
    soup = BeautifulSoup(html, "html.parser")
    if base:
        test_selectors(soup, base_url, base, title, link, args.limit)
    else:
        suggest_selectors(soup, args.limit)


if __name__ == "__main__":
    main()
