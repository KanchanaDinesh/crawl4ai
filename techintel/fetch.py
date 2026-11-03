"""Fetching pages with crawl4ai, in one of two modes:

  http     plain HTTP request: fast and cheap, but no JavaScript (good for qualifying)
  browser  real headless browser: renders JavaScript sites (good for profiling)

Politeness is built in: limited concurrency, a delay between requests to the same
site (different sites don't wait for each other), and robots.txt is respected
(settings["crawl"]).

When the browser itself crashes, that says nothing about the site: such results have
browser_failed=True, so the stages don't save them as "unreachable". After a few restarts
the Fetcher gives up (`dead`), and the stages stop instead of failing every site left.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig, HTTPCrawlerConfig
from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy

import project_paths

# Plain HTTP requests say what they are. Left unset, crawl4ai would send a made-up Windows browser
# user agent, and the project never pretends to be a browser (sites that refuse us are left alone).
HONEST_USER_AGENT = "crawl4ai-techintel/1.0 (personal research; https://github.com/KanchanaDinesh/crawl4ai)"

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
    browser_failed: bool = False      # our browser crashed: the site wasn't really tried


ERROR_PATTERNS = (
    (("name or service not known", "nodename nor servname", "getaddrinfo", "no address associated",
      "err_name_not_resolved", "temporary failure in name resolution"), "domain does not resolve (no website yet)"),
    (("connect call failed", "connection refused", "err_connection_refused"), "connection refused"),
    (("timed out", "timeout", "err_timed_out"), "timed out"),
    (("certificate", "ssl", "err_cert"), "SSL certificate error"),
    (("connection reset", "server disconnected", "err_connection_reset", "err_empty_response"), "connection dropped"),
    (("robots",), "robots.txt disallows crawling"),
    (("anti-bot", "captcha", "cloudflare"), "possible bot protection"),
    (("has been closed", "target closed", "browser closed", "browser has disconnected",
      "browser is not available"), "browser closed unexpectedly (run: uv run python doctor.py)"),
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
                "connection closed", "browser.new_context", "context or browser", "browser is not available")
CHROMIUM_WHY = ("fatal", "check failed", "crash", "killed", "signal", "denied", "not permitted", "sandbox",
                "icudtl", "gpu process", "out of memory", "exitcode=")


def browser_died(error: str | None) -> bool:
    low = (error or "").lower()
    return any(s in low for s in BROWSER_GONE)


def chromium_reason(error: str | None) -> str:
    """The lines of crawl4ai's long error that say why Chromium died (its own log is inside the error)."""
    lines = [ln.strip() for ln in (error or "").splitlines() if ln.strip()]
    why = [ln for ln in lines if any(w in ln.lower() for w in CHROMIUM_WHY) and "<launching>" not in ln]
    # crawl4ai's first line is only "Unexpected error in _crawl_web at line ...": the "Error:" line says what failed
    errors = [ln for ln in lines if ln.startswith("Error:")]
    picked = errors[:1] + why[-3:] or lines[:1]
    return " | ".join(ln[:200] for ln in picked) or "no message"


def error_excerpt(error: str | None, max_lines: int = 30) -> str:
    """The useful part of crawl4ai's long error (without the code listing), indented for the log."""
    lines = [ln.rstrip() for ln in (error or "").splitlines() if ln.strip()]
    code_at = next((i for i, ln in enumerate(lines) if ln.strip().startswith("Code context:")), len(lines))
    return "\n".join("    " + ln[:240] for ln in lines[:code_at][:max_lines])


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
        self.max_restarts = int(crawl_config.get("max_browser_restarts", 3))
        self.dead: str | None = None                  # why the browser is given up on, if it is

    async def __aenter__(self) -> "Fetcher":
        await self._start()
        return self

    async def _start(self) -> None:
        user_agent = self.config.get("user_agent")
        browser_config = BrowserConfig(headless=True, verbose=False, extra_args=project_paths.chromium_args(),
                                       **({"user_agent": user_agent} if user_agent else {}))
        if self.mode == "http":
            http_config = HTTPCrawlerConfig(headers={"User-Agent": user_agent or HONEST_USER_AGENT})
            strategy = AsyncHTTPCrawlerStrategy(browser_config=http_config)
            self._crawler = AsyncWebCrawler(crawler_strategy=strategy, config=browser_config)
        else:
            self._crawler = AsyncWebCrawler(config=browser_config)
        await self._crawler.__aenter__()

    async def _restart(self, seen_generation: int, error: str | None) -> None:
        """Replace a browser that died. Several pages may notice at once: only the first restarts it.

        After max_restarts it gives up (self.dead): a browser that keeps dying won't get better by
        itself, and every site tried with it would wrongly look unreachable.
        """
        async with self._restart_lock:
            if self._generation != seen_generation or self.dead:
                return
            why = chromium_reason(error)
            if self.restarts >= self.max_restarts:
                self.dead = f"the browser crashed {self.restarts + 1} times (Chromium: {why})"
                logger.error("browser keeps crashing; giving up for this run. Chromium said: %s", why)
                logger.error("Check it with: uv run python doctor.py --verbose")
                return
            logger.warning("browser closed unexpectedly; restarting it (restart %d). Chromium said: %s",
                           self.restarts + 1, why)
            if self.restarts == 0:             # the whole message once per run: enough to find the cause
                logger.warning("full browser error (first crash only):\n%s", error_excerpt(error))
            try:
                await self._crawler.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001  it's already gone
                pass
            try:
                await self._start()
            except Exception as e:  # noqa: BLE001
                self.dead = f"the browser won't start again ({friendly_error(str(e))})"
                logger.error("browser won't start again: %s. Check it with: uv run python doctor.py --verbose",
                             chromium_reason(str(e)))
                return
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

    def _hard_timeout(self) -> float:
        """A page can't take longer than this in total. crawl4ai's own page_timeout covers loading only;
        with a crashed browser a call could otherwise wait forever (a run once hung for 10+ minutes)."""
        page = float(self.config.get("page_timeout_ms", 30000)) / 1000
        return float(self.config.get("fetch_hard_timeout_seconds") or page * 2 + 30)

    def _browser_down(self, url: str) -> FetchResult:
        return FetchResult(url=url, mode=self.mode, success=False, browser_failed=True,
                           error=f"not tried: {self.dead}")

    async def fetch(self, url: str) -> FetchResult:
        if self.dead:
            return self._browser_down(url)
        await self._wait_for_host(url)
        for attempt in (1, 2):                       # one retry if the browser itself died
            generation = self._generation
            async with self._semaphore:
                if self.dead:
                    return self._browser_down(url)
                try:
                    result = await asyncio.wait_for(self._crawler.arun(url=url, config=self._run_config()),
                                                    self._hard_timeout())
                    error = None if result.success else result.error_message
                except asyncio.TimeoutError:
                    result, error = None, f"timed out: no answer within {self._hard_timeout():.0f}s"
                except Exception as e:  # network errors, timeouts, browser crashes
                    logger.debug("fetch %s failed: %s", url, e)
                    result, error = None, str(e)
            if self.mode == "browser" and browser_died(error):
                if attempt == 1:
                    await self._restart(generation, error)
                    if not self.dead:
                        continue
                return FetchResult(url=url, mode=self.mode, success=False, browser_failed=True,
                                   error=friendly_error(error))
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
