"""Fetching pages with crawl4ai, in one of two modes:

  http     plain HTTP request: fast and cheap, but no JavaScript (good for qualifying)
  browser  real headless browser: renders JavaScript sites (good for profiling)

Politeness is built in: limited concurrency, a delay between requests to the same
site (different sites don't wait for each other), and robots.txt is respected
(settings["crawl"]).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig, HTTPCrawlerConfig
from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    url: str
    mode: str
    success: bool
    status: int | None = None
    final_url: str | None = None
    html: str = ""
    markdown: str = ""
    headers: dict = field(default_factory=dict)
    internal_links: list[dict] = field(default_factory=list)
    external_links: list[dict] = field(default_factory=list)
    error: str | None = None


ERROR_PATTERNS = (
    (("name or service not known", "nodename nor servname", "getaddrinfo", "no address associated",
      "err_name_not_resolved", "temporary failure in name resolution"), "domain does not resolve (no website yet)"),
    (("connect call failed", "connection refused", "err_connection_refused"), "connection refused"),
    (("timed out", "timeout", "err_timed_out"), "timed out"),
    (("certificate", "ssl", "err_cert"), "SSL certificate error"),
    (("connection reset", "server disconnected", "err_connection_reset", "err_empty_response"), "connection dropped"),
    (("robots",), "robots.txt disallows crawling"),
    (("anti-bot", "captcha", "cloudflare"), "possible bot protection"),
    (("has been closed", "target closed", "browser closed", "browser has disconnected"),
     "browser closed unexpectedly (run: uv run python doctor.py)"),
)


def friendly_error(error: str | None) -> str | None:
    """crawl4ai wraps errors in long tracebacks; turn them into a short, readable reason."""
    if not error:
        return error
    low = error.lower()
    for needles, label in ERROR_PATTERNS:
        if any(n in low for n in needles):
            return label
    for line in error.splitlines():
        if line.strip().startswith("Error:"):
            return line.strip()[6:].strip()[:160]
    return error.strip().splitlines()[0][:160]


BROWSER_GONE = ("has been closed", "target closed", "browser closed", "browser has disconnected",
                "connection closed", "browser.new_context", "context or browser")


def browser_died(error: str | None) -> bool:
    low = (error or "").lower()
    return any(s in low for s in BROWSER_GONE)


class Fetcher:
    """Async context manager: `async with Fetcher(settings.section("crawl"), "http") as f: ...`"""

    def __init__(self, crawl_config: dict, mode: str = "http", concurrency: int | None = None,
                 timeout_ms: int | None = None):
        if mode not in ("http", "browser"):
            raise ValueError("mode must be 'http' or 'browser'")
        self.mode = mode
        self.config = dict(crawl_config)
        if timeout_ms:
            self.config["page_timeout_ms"] = timeout_ms
        self._semaphore = asyncio.Semaphore(concurrency or crawl_config.get("concurrency", 4))
        self._crawler: AsyncWebCrawler | None = None
        self._host_next: dict[str, float] = {}       # politeness: earliest next request time per host
        self._host_lock = asyncio.Lock()
        self._restart_lock = asyncio.Lock()
        self._generation = 0                          # bumped each time the browser is restarted
        self.restarts = 0

    async def __aenter__(self) -> "Fetcher":
        await self._start()
        return self

    async def _start(self) -> None:
        user_agent = self.config.get("user_agent")
        browser_config = BrowserConfig(headless=True, verbose=False,
                                       **({"user_agent": user_agent} if user_agent else {}))
        if self.mode == "http":
            http_config = HTTPCrawlerConfig(headers={"User-Agent": user_agent} if user_agent else None)
            strategy = AsyncHTTPCrawlerStrategy(browser_config=http_config)
            self._crawler = AsyncWebCrawler(crawler_strategy=strategy, config=browser_config)
        else:
            self._crawler = AsyncWebCrawler(config=browser_config)
        await self._crawler.__aenter__()

    async def _restart(self, seen_generation: int) -> None:
        """Replace a browser that died. Several pages may notice at once: only the first restarts it."""
        async with self._restart_lock:
            if self._generation != seen_generation:
                return
            logger.warning("browser closed unexpectedly; restarting it (restart %d)", self.restarts + 1)
            try:
                await self._crawler.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001  it's already gone
                pass
            await self._start()
            self._generation += 1
            self.restarts += 1

    async def __aexit__(self, *exc) -> None:
        if self._crawler:
            await self._crawler.__aexit__(*exc)

    def _run_config(self) -> CrawlerRunConfig:
        return CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            check_robots_txt=self.config.get("check_robots_txt", True),
            page_timeout=self.config.get("page_timeout_ms", 30000),
            verbose=False,
        )

    async def _wait_for_host(self, url: str) -> None:
        """Space out requests to the *same* site by delay_seconds; different sites don't wait."""
        delay = float(self.config.get("delay_seconds", 1.0))
        if delay <= 0:
            return
        host = urlsplit(url).netloc.lower()
        loop = asyncio.get_running_loop()
        async with self._host_lock:
            now = loop.time()
            start = max(now, self._host_next.get(host, 0.0))
            self._host_next[host] = start + delay
        if start > now:
            await asyncio.sleep(start - now)

    async def fetch(self, url: str) -> FetchResult:
        await self._wait_for_host(url)
        for attempt in (1, 2):                       # one retry if the browser itself died
            generation = self._generation
            async with self._semaphore:
                try:
                    result = await self._crawler.arun(url=url, config=self._run_config())
                    error = None if result.success else result.error_message
                except Exception as e:  # network errors, timeouts, browser crashes
                    logger.debug("fetch %s failed: %s", url, e)
                    result, error = None, str(e)
            if attempt == 1 and self.mode == "browser" and browser_died(error) and self.restarts < 3:
                await self._restart(generation)
                continue
            break
        if result is None:
            return FetchResult(url=url, mode=self.mode, success=False, error=friendly_error(error))

        markdown = ""
        if result.markdown is not None:
            markdown = getattr(result.markdown, "raw_markdown", None) or str(result.markdown)
        links = result.links or {}
        return FetchResult(
            url=url,
            mode=self.mode,
            success=bool(result.success),
            status=result.status_code,
            final_url=result.redirected_url or result.url,
            html=result.html or "",
            markdown=markdown,
            headers=dict(result.response_headers or {}),
            internal_links=links.get("internal", []),
            external_links=links.get("external", []),
            error=None if result.success else friendly_error(result.error_message),
        )

    async def fetch_many(self, urls: list[str]) -> list[FetchResult]:
        return await asyncio.gather(*(self.fetch(u) for u in urls))
