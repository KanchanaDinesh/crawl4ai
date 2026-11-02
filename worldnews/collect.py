"""Collect the latest N headlines from every source.

For each source, methods are tried in this order until one gives headlines:
  1. feed        the RSS/Atom feeds listed in sources.json (or found on an earlier run)
  2. selectors   site-specific CSS selectors, if the source has them
  3. discovered  feeds advertised on the homepage (<link rel="alternate" ...>), remembered for next time
  4. homepage    article links picked out of the homepage automatically
Steps 2-4 read the homepage over plain HTTP; if that's blocked or finds too little,
a real browser (crawl4ai) is used instead. For homepage headlines, each article page
is opened to read its publish date and summary, so the newest can be put first.
"""

import asyncio
import hashlib
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import extract
from .http import Http
from .store import NewsStore
from .tagging import TopicTagger

logger = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Context:
    http: Http
    tagger: TopicTagger
    fetch_cfg: dict
    home_cfg: dict
    feed_cache: dict[str, str]
    stack: AsyncExitStack
    article_meta: bool = True
    use_browser: bool = True
    _browser: object = None
    _browser_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def browser_html(self, url: str) -> tuple[str | None, str | None]:
        """Render a page in a real browser (crawl4ai). Returns (html, error)."""
        async with self._browser_lock:
            if self._browser is None:
                from techintel.fetch import Fetcher   # shared crawl4ai wrapper
                self._browser = await self.stack.enter_async_context(Fetcher(
                    {"concurrency": 2, "delay_seconds": 0.5, "page_timeout_ms": 45000, "check_robots_txt": True},
                    "browser"))
        result = await self._browser.fetch(url)
        if result.html and (result.success or len(result.html) > 5000):
            return result.html, None
        return None, result.error or f"HTTP {result.status}"


def sort_latest(items: list[dict]) -> list[dict]:
    """Dated items newest first; undated ones keep their original order, after the dated ones."""
    dated = sorted((i for i in items if i.get("published_at")), key=lambda i: i["published_at"], reverse=True)
    return dated + [i for i in items if not i.get("published_at")]


async def _from_feeds(ctx: Context, urls: list[str], home: str) -> tuple[list[dict], str | None, str | None]:
    errors = []
    for url in urls:
        r = await ctx.http.get(url)
        if not r.ok:
            errors.append(f"{url}: {r.error}")
            continue
        if not extract.looks_like_feed(r.text):
            errors.append(f"{url}: not a feed")
            continue
        items = extract.parse_feed(r.text, r.final_url or home)
        if items:
            return items, url, None
        errors.append(f"{url}: feed is empty")
    return [], None, "; ".join(errors) or None


async def _add_article_meta(ctx: Context, items: list[dict]) -> None:
    sem = asyncio.Semaphore(int(ctx.fetch_cfg.get("article_meta_concurrency", 6)))

    async def one(item):
        async with sem:
            if not await ctx.http.allowed(item["url"]):
                return
            r = await ctx.http.get(item["url"])
            if r.ok and r.text:
                meta = extract.article_meta(r.text)
                item["published_at"] = item.get("published_at") or meta["published_at"]
                item["summary"] = item.get("summary") or meta["summary"]
                item["image"] = item.get("image") or meta["image"]
    await asyncio.gather(*(one(i) for i in items))


async def collect_source(ctx: Context, source: dict, limit: int) -> dict:
    name, home = source["name"], source["homepage"]
    report = {"name": name, "group": source.get("group"), "homepage": home, "status": "failed",
              "via": None, "feed_url": None, "error": None, "items": []}
    errors: list[str] = []
    items: list[dict] = []

    # 1. configured feeds (plus one remembered from discovery)
    feeds = list(source.get("feeds") or [])
    if ctx.feed_cache.get(name) and ctx.feed_cache[name] not in feeds:
        feeds.append(ctx.feed_cache[name])
    if feeds:
        items, feed_url, err = await _from_feeds(ctx, feeds, home)
        if items:
            report.update(via="feed", feed_url=feed_url)
        elif err:
            errors.append(f"feeds: {err}")

    # 2-4. homepage based
    if not items:
        html, home_err = None, None
        if await ctx.http.allowed(home):
            r = await ctx.http.get(home)
            html, home_err = (r.text, None) if r.ok else (None, r.error)
        else:
            home_err = "robots.txt disallows the homepage"
        fallback: tuple[list[dict], str | None] = ([], None)   # best "too few" result, used if nothing better
        for attempt in ("http", "browser"):
            if attempt == "browser":
                if not ctx.use_browser or not ctx.fetch_cfg.get("browser_fallback", True) or home_err == "robots.txt disallows the homepage":
                    break
                html, home_err = await ctx.browser_html(home)
            if not html:
                continue
            if source.get("selectors"):
                items = extract.selector_headlines(html, home, source["selectors"])
                if items:
                    report["via"] = f"selectors ({attempt})"
                    break
            if attempt == "http" and not items:
                discovered = [u for u in extract.discover_feeds(html, home) if u not in feeds]
                if discovered:
                    items, feed_url, err = await _from_feeds(ctx, discovered[:3], home)
                    if items:
                        report.update(via="discovered feed", feed_url=feed_url)
                        ctx.feed_cache[name] = feed_url
                        break
            items = extract.homepage_headlines(html, home, ctx.home_cfg)
            if len(items) >= min(3, limit):
                report["via"] = f"homepage ({attempt})"
                break
            if len(items) > len(fallback[0]):
                fallback = (items, f"homepage ({attempt})")
            items = []
        if not items and fallback[0]:
            items, report["via"] = fallback
        if not items and home_err:
            errors.append(f"homepage: {home_err}")
        if items and report["via"] and report["via"].startswith(("homepage", "selectors")):
            candidates = items[: max(limit * 2, limit + 5)]
            if ctx.article_meta and ctx.fetch_cfg.get("fetch_article_meta", True):
                await _add_article_meta(ctx, candidates)
            items = candidates

    items = sort_latest(items)[:limit]
    collected = utc_now()
    for rank, item in enumerate(items, 1):
        item.update(id=hashlib.sha1(item["url"].encode()).hexdigest()[:16], source=name,
                    group=source.get("group"), rank=rank, collected_at=collected)
        ctx.tagger.tag(item, source.get("group", ""))
    report["items"] = items
    if items:
        report["status"] = "ok"
    else:
        report["error"] = "; ".join(errors) or "no headlines found"
    return report


async def run_collection(settings, store: NewsStore, sources: list[dict], limit: int,
                         article_meta: bool = True, use_browser: bool = True) -> dict:
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")   # unique per run
    known = store.known_urls()
    cache = store.feed_cache()
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(Http(settings.section("fetch")))
        ctx = Context(http=http, tagger=TopicTagger.from_settings(settings.raw),
                      fetch_cfg=settings.section("fetch"), home_cfg=settings.section("homepage"),
                      feed_cache=cache, stack=stack, article_meta=article_meta, use_browser=use_browser)

        async def safe(source):
            try:
                return await collect_source(ctx, source, int(source.get("limit") or limit))
            except Exception as e:   # one broken source must not stop the others
                logger.exception("source %s crashed", source["name"])
                return {"name": source["name"], "group": source.get("group"), "homepage": source["homepage"],
                        "status": "failed", "via": None, "error": f"crashed: {e}", "items": []}
        results = await asyncio.gather(*(safe(s) for s in sources))

    new_items = []
    for result in results:
        for item in result["items"]:
            item["is_new"] = item["url"] not in known
            if item["is_new"]:
                known.add(item["url"])
                new_items.append(item)
        logger.info("%-26s %-6s %-22s %3d headlines %s", result["name"], result["status"], result["via"] or "-",
                    len(result["items"]), result["error"] or "")
    store.append_items(new_items)
    store.save_feed_cache(cache)
    snapshot = {"run_at": run_at, "finished_at": utc_now(), "limit": limit, "sources": results,
                "totals": {"sources": len(results), "ok": sum(r["status"] == "ok" for r in results),
                           "headlines": sum(len(r["items"]) for r in results), "new": len(new_items)}}
    store.save_snapshot(snapshot)
    return snapshot
