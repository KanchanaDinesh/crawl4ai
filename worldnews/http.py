"""Plain HTTP fetching (aiohttp) with browser-like headers and robots.txt checks.

Feeds are meant to be consumed by programs, so they're fetched directly.
Homepages and article pages are only fetched if the site's robots.txt allows it.
"""

import asyncio
import logging
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import urlsplit

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class Response:
    url: str
    ok: bool
    status: int | None = None
    final_url: str | None = None
    text: str = ""
    error: str | None = None


class Http:
    """`async with Http(fetch_config) as http: r = await http.get(url)`"""

    def __init__(self, config: dict):
        self.config = config
        self.user_agent = config.get("user_agent") or "Mozilla/5.0"
        self._timeout = aiohttp.ClientTimeout(total=float(config.get("timeout_seconds", 25)))
        self._semaphore = asyncio.Semaphore(int(config.get("concurrency", 8)))
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "Http":
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        self.session = aiohttp.ClientSession(headers=headers, timeout=self._timeout, trust_env=True)
        return self

    async def __aexit__(self, *exc) -> None:
        if self.session:
            await self.session.close()

    async def get(self, url: str) -> Response:
        async with self._semaphore:
            try:
                async with self.session.get(url, allow_redirects=True) as resp:
                    raw = await resp.read()
                    text = raw.decode(resp.get_encoding() if resp.charset else "utf-8", errors="replace")
                    return Response(url=url, ok=resp.status < 400, status=resp.status,
                                    final_url=str(resp.url), text=text,
                                    error=None if resp.status < 400 else f"HTTP {resp.status}")
            except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError, LookupError) as e:
                return Response(url=url, ok=False, error=f"{type(e).__name__}: {e}"[:200])

    async def allowed(self, url: str) -> bool:
        """robots.txt check for our user agent; unreachable robots.txt counts as allowed."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        lock = self._robots_locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._robots:
                parser = None
                r = await self.get(f"{origin}/robots.txt")
                if r.ok and r.text:
                    parser = robotparser.RobotFileParser()
                    parser.parse(r.text.splitlines())
                self._robots[origin] = parser
        parser = self._robots[origin]
        return True if parser is None else parser.can_fetch(self.user_agent, url)
