"""Fetching pages with crawl4ai, in one of two modes:

  http     plain HTTP request: fast and cheap, but no JavaScript (good for qualifying)
  browser  real headless browser: renders JavaScript sites (good for profiling)

Politeness is built in: limited concurrency, a delay between requests, and
robots.txt is respected (settings["crawl"]).
"""

import asyncio
import logging
from dataclasses import dataclass, field

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


class Fetcher:
    """Async context manager: `async with Fetcher(settings.section("crawl"), "http") as f: ...`"""

    def __init__(self, crawl_config: dict, mode: str = "http"):
        if mode not in ("http", "browser"):
            raise ValueError("mode must be 'http' or 'browser'")
        self.mode = mode
        self.config = crawl_config
        self._semaphore = asyncio.Semaphore(crawl_config.get("concurrency", 4))
        self._crawler: AsyncWebCrawler | None = None

    async def __aenter__(self) -> "Fetcher":
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
        return self

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

    async def fetch(self, url: str) -> FetchResult:
        async with self._semaphore:
            try:
                result = await self._crawler.arun(url=url, config=self._run_config())
            except Exception as e:  # network errors, timeouts, browser crashes
                logger.debug("fetch %s failed: %s", url, e)
                return FetchResult(url=url, mode=self.mode, success=False, error=str(e))
            finally:
                await asyncio.sleep(self.config.get("delay_seconds", 1.0))

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
            error=None if result.success else result.error_message,
        )

    async def fetch_many(self, urls: list[str]) -> list[FetchResult]:
        return await asyncio.gather(*(self.fetch(u) for u in urls))
