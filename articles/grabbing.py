"""Finding each company's feeds, then reading its articles.

The rules here are the project's: an honest user agent, one request at a time per site with a pause, robots.txt respected for every page
(feeds a site publishes for programs are fetched directly, as in worldnews), no browser, and a site that says 401, 403 or 429 is left alone.

Feeds usually only carry the latest 10-50 items, so history builds up from the first run on.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import feedparser
from bs4 import BeautifulSoup

from worldnews.cleaning import clean_summary
from worldnews.extract import article_meta, clean_text, discover_feeds, looks_like_feed, parse_date
from worldnews.http import Http

from .tagging import tag_article

logger = logging.getLogger("articles")
REFUSED = (401, 403, 429)
SKIP_FEEDS = re.compile(r"comment|/wp-json/|oembed", re.I)
SKIP_ARTICLES = re.compile(r"/careers?[-/]|/jobs?/|/vacanc", re.I)       # some sites put job ads in the same feed
SKIP_TAGS = "script style nav footer header aside form noscript svg figure button".split()


class Web:
    """Http plus a pause per site, and memory of who said no this run."""

    def __init__(self, config: dict):
        self.http = Http(config)
        self.gap = float(config.get("gap_seconds", 1.0))
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}
        self.refused: dict[str, str] = {}

    async def __aenter__(self):
        await self.http.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self.http.__aexit__(*exc)

    @staticmethod
    def host(url: str) -> str:
        return urlsplit(url).netloc.lower()

    async def get(self, url: str, host_gap: float | None = None):
        host = self.host(url)
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = (host_gap if host_gap is not None else self.gap) - (time.monotonic() - self._last.get(host, 0))
            if wait > 0:
                await asyncio.sleep(wait)
            r = await self.http.get(url)
            self._last[host] = time.monotonic()
        if r.status in REFUSED:
            self.refused[host] = f"HTTP {r.status}"
        return r

    async def allowed(self, url: str) -> bool:
        return await self.http.allowed(url)


# ── reading ─────────────────────────────────────────────────────────────────────────────────────────────────
def article_text(html: str) -> str:
    """The article's words: its <article> or <main> if it has one, paragraphs and headings, no menus or scripts."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(SKIP_TAGS):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    parts = [re.sub(r"\s+", " ", p.get_text(" ", strip=True)) for p in root.find_all(["p", "h2", "h3", "li", "blockquote"])]
    text = "\n".join(p for p in parts if len(p) > 1)
    return text or re.sub(r"\s+", " ", root.get_text(" ", strip=True))


def _words(html: str) -> int:
    return len(BeautifulSoup(html, "html.parser").get_text(" ", strip=True)) if html else 0


def parse_items(xml: str, base_url: str = "") -> list[dict]:
    """Feed items with what worldnews.extract.parse_feed leaves out: the body, if the feed carries one, and the author."""
    items = []
    for entry in feedparser.parse(xml).entries:
        link, title = entry.get("link") or "", clean_text(entry.get("title"), 300)
        if not link or not title:
            continue
        body = ""
        for content in entry.get("content") or []:
            if content.get("value") and len(content["value"]) > len(body):
                body = content["value"]
        summary_html = entry.get("summary") or entry.get("description") or ""
        items.append({"url": urljoin(base_url, link).split("#")[0], "title": title, "summary": clean_text(clean_summary(BeautifulSoup(summary_html, "html.parser").get_text(" ", strip=True)), 600),
                      "body_html": body if _words(body) >= _words(summary_html) else summary_html, "author": clean_text(entry.get("author"), 120) or None,
                      "published_at": parse_date(entry.get("published_parsed") or entry.get("updated_parsed") or entry.get("published") or entry.get("updated"))})
    return items


# ── finding feeds ───────────────────────────────────────────────────────────────────────────────────────────
def candidates(website: str, discovery: dict) -> list[str]:
    """The company's home, then the usual places. The home's own <link> tags come first, as those aren't guesses."""
    parts = urlsplit(website if "//" in website else f"https://{website}")
    base = f"{parts.scheme}://{parts.netloc}"
    bare = re.sub(r"^www\d?\.", "", parts.netloc)
    return [f"{base}{p}" for p in discovery["paths"]] + [f"https://{sub}.{bare}/" for sub in discovery["subdomains"]]


def feed_identity(text: str, url: str) -> tuple[str, frozenset] | None:
    """(title, its newest links) if this really is a feed worth having. Sites answer at /feed/ and /rss/ with the same items, and a page's
    <link> can point at the comments on one old post."""
    if not looks_like_feed(text):
        return None
    parsed = feedparser.parse(text)
    title = parsed.feed.get("title") or url
    if not parsed.entries or title.lower().startswith("comments on"):
        return None
    return title, frozenset(e.get("link") for e in parsed.entries[:5])


async def find_feeds(web: Web, company: dict, discovery: dict, known: list[str]) -> tuple[list[dict], str]:
    """[{"url", "found_how", "title"}], and a note in plain words. Pages are only opened if robots.txt allows it."""
    found: list[dict] = []
    tried: set[str] = set()
    same_items: set[frozenset] = set()
    limit, budget = discovery["max_feeds"], discovery["max_requests"]

    def keep(text: str, url: str, how: str) -> None:
        identity = feed_identity(text, url)
        if identity and identity[1] not in same_items:
            same_items.add(identity[1])
            found.append({"url": url, "found_how": how, "title": clean_text(identity[0], 200)})

    async def try_feed(url: str, how: str) -> None:
        nonlocal budget
        if url in tried or SKIP_FEEDS.search(url) or len(found) >= limit or budget <= 0:
            return
        tried.add(url)
        budget -= 1
        r = await web.get(url)
        if r.ok:
            keep(r.text, url, how)

    for url in known:
        await try_feed(url, "known")
    website = company.get("website")
    if not website:
        return found, "found from settings" if found else "no website known"
    if found:
        return found, "found from settings"
    if Web.host(website) in web.refused:
        return found, f"{Web.host(website)} said no this run"
    for page in [website, *candidates(website, discovery)]:
        if len(found) >= limit or budget <= 0:
            break
        if Web.host(page) in web.refused or page in tried:
            continue
        if not await web.allowed(page):
            continue
        budget -= 1
        tried.add(page)
        r = await web.get(page)
        if not r.ok:
            continue
        if looks_like_feed(r.text):
            keep(r.text, page, "guess")
            continue
        for url in discover_feeds(r.text, r.final_url or page):
            await try_feed(url, "homepage")
    note = f"{len(found)} feeds found" if found else f"no feed found in {len(tried)} tries"
    if Web.host(website) in web.refused:
        note += f"; {Web.host(website)} said {web.refused[Web.host(website)]}"
    return found, note


# ── articles ────────────────────────────────────────────────────────────────────────────────────────────────
async def read_article(web: Web, item: dict, cfg: dict, compiled: dict) -> dict | None:
    """One feed item as an article with its text and tags, or None if it isn't about any of the services. Tags from the title and summary
    decide whether the page is worth opening at all, so a bank's sports news costs its site nothing."""
    if SKIP_ARTICLES.search(urlsplit(item["url"]).path):
        return None
    feed_text = article_text(item["body_html"]) if item.get("body_html") else ""
    if not tag_article(item["title"], item["summary"], feed_text, compiled, cfg["body_hits_needed"]):
        return None
    text, status, published, date_from = feed_text, "feed", item.get("published_at"), "feed" if item.get("published_at") else "none"
    if len(feed_text) < cfg["enough_feed_text"]:
        status = "short"
        host = Web.host(item["url"])
        if host in web.refused:
            status = "refused"
        elif not await web.allowed(item["url"]):
            status = "not_allowed"
        else:
            r = await web.get(item["url"])
            if r.status in REFUSED:
                status = "refused"
            elif not r.ok:
                status = "failed"
            else:
                page = article_text(r.text)
                if len(page) > len(text):
                    text = page
                status = "page" if len(page) >= cfg["enough_feed_text"] else "short"    # often a page that only draws itself in a browser
                if not published:
                    published = article_meta(r.text).get("published_at")
                    date_from = "page" if published else "none"
    text = text[:cfg["keep_text_chars"]]
    tags = tag_article(item["title"], item["summary"], text, compiled, cfg["body_hits_needed"])
    if not tags:
        return None
    return {"url": item["url"], "title": item["title"], "summary": item["summary"] or None, "text": text or None, "text_status": status, "word_count": len(text.split()),
            "author": item.get("author"), "published_at": published, "date_from": date_from, "tags": tags}


async def read_feed(web: Web, feed: dict, known_urls, cfg: dict, compiled: dict, deadline: float) -> dict:
    """One feed: {"status", "items", "new": [articles], "skipped", "error"}. New items are read one at a time, so a stop loses nothing that's
    saved already, and the next run picks up the rest."""
    r = await web.get(feed["url"])
    if r.status in REFUSED:
        return {"status": "refused", "error": f"HTTP {r.status}", "items": 0, "new": [], "skipped": 0}
    if not r.ok or not looks_like_feed(r.text):
        return {"status": "failed", "error": r.error or "the answer isn't a feed", "items": 0, "new": [], "skipped": 0}
    items = parse_items(r.text, feed["url"])
    fresh = [i for i in items if i["url"] not in known_urls]
    new, skipped = [], 0
    for item in fresh:
        if time.monotonic() > deadline:
            break
        article = await read_article(web, item, cfg, compiled)
        if article:
            new.append(article)
        else:
            skipped += 1
    return {"status": "ok", "error": None, "items": len(items), "new": new, "skipped": skipped}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["Web", "article_text", "candidates", "find_feeds", "parse_items", "read_article", "read_feed", "utc_now"]
