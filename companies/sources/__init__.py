"""Where companies come from. Only open data and public APIs, used the way their owners allow:

    wikidata         Wikidata SPARQL (CC0): name, official website, country, industry
    gleif            GLEIF LEI API (CC0): legal entities worldwide, name and country (no websites)
    cse              Colombo Stock Exchange API: Sri Lankan listed companies, with website and sector
    companies_house  UK Companies House bulk data (OGL), off by default (millions of rows, no websites)
    asic             ASIC company dataset (Australia), off by default; read from a file you download yourself
    sec              SEC EDGAR tickers, off until you set a contact email (the SEC requires one)

Never: LinkedIn, Crunchbase, OpenCorporates bulk scraping, or search-engine results.

Each source is a class with `async def batches(cursors)`, an async generator. It reads its saved
cursors (where it stopped last time, per country), does one unit of work at a time (one query, one
page, one chunk of a file) and yields a Batch: the companies found and the new cursor values. The
collector saves both straight away, so a run stopped at any point continues there next time.

When a source refuses us (401/403/429) it raises Refused: the collector stops that source for this
run and saves nothing for it; nothing is retried in a loop.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiohttp

from ..store import now_iso

logger = logging.getLogger("companies")


class Refused(Exception):
    """The source said no (401/403/429). Back off: not asked again this run."""

    def __init__(self, source: str, status: int, retry_after: str | None = None):
        self.status = status
        wait = f", it asks us to wait {retry_after}s" if retry_after else ""
        super().__init__(f"{source} refused the request (HTTP {status}{wait})")


class SourceError(Exception):
    """Anything else that went wrong with a request (server error, timeout, bad answer)."""


@dataclass
class Batch:
    companies: list[dict]
    cursors: dict[str, dict] = field(default_factory=dict)    # key (e.g. a country) -> new cursor value
    label: str = ""                                            # for the log: "US company"


@dataclass
class Context:
    session: aiohttp.ClientSession
    settings: dict
    countries: list[dict]                   # every known country: {"code", "name", "wikidata"}
    only_countries: list[str] | None = None  # --country
    deadline: float | None = None           # time.monotonic() after which nothing new starts
    download_dir: object = None

    def time_left(self) -> float:
        return float("inf") if self.deadline is None else self.deadline - time.monotonic()

    @property
    def priority(self) -> list[str]:
        return [c.upper() for c in self.settings.get("priority_countries", [])]

    def country_order(self) -> list[str]:
        """Priority countries first, then every other country (alphabetical), limited by --country."""
        codes = [c["code"] for c in self.countries]
        order = [c for c in self.priority if c in codes] + [c for c in codes if c not in self.priority]
        if self.only_countries:
            order = [c for c in order if c in self.only_countries]
        return order


class Source:
    name = "source"
    unit_seconds = 10       # roughly how long one unit takes: none is started with less time than this left

    def __init__(self, config: dict, ctx: Context):
        self.config = config
        self.ctx = ctx

    def has_time(self) -> bool:
        return self.ctx.time_left() > self.unit_seconds

    async def pause(self, seconds: float) -> bool:
        """Wait between requests (politeness). False if the run's time is up meanwhile."""
        if seconds > 0:
            await asyncio.sleep(min(seconds, max(0.0, self.ctx.time_left())))
        return self.has_time()

    def due(self, cursor: dict | None) -> bool:
        """A pass that isn't finished continues; a finished one starts again after refresh_days."""
        if not cursor or not cursor.get("done_at"):
            return True
        return age_days(cursor["done_at"]) >= float(self.config.get("refresh_days", 30))

    async def batches(self, cursors: dict[str, dict]):  # pragma: no cover - each source has its own
        raise NotImplementedError
        yield


def age_days(iso: str | None) -> float:
    if not iso:
        return float("inf")
    try:
        when = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400


def new_pass() -> dict:
    return {"pass_started": now_iso(), "done_at": None}


class Rotation:
    """Takes turns between countries: each priority country gets a turn, then one other country
    (the others take turns too), and again. So US, UK, AU, IN and LK move quickly, and the rest of
    the world still moves every cycle. Countries that are finished are dropped."""

    def __init__(self, order: list[str], priority: list[str], start_other: int = 0):
        self.priority = [c for c in order if c in priority]
        self.others = [c for c in order if c not in priority]
        self.other_pos = start_other % len(self.others) if self.others else 0

    def drop(self, code: str) -> None:
        if code in self.priority:
            self.priority.remove(code)
        elif code in self.others:
            i = self.others.index(code)
            self.others.remove(code)
            if i < self.other_pos:
                self.other_pos -= 1
            if self.others:
                self.other_pos %= len(self.others)

    def __iter__(self):
        while self.priority or self.others:
            for code in list(self.priority):
                if code in self.priority:
                    yield code
            if self.others:
                code = self.others[self.other_pos]
                self.other_pos = (self.other_pos + 1) % len(self.others)
                yield code


async def request(session: aiohttp.ClientSession, source: str, url: str, *, method: str = "GET",
                  params: dict | None = None, data: dict | None = None, headers: dict | None = None,
                  timeout: float = 60, want: str = "json"):
    """One HTTP request. Refused for 401/403/429; SourceError for anything else that isn't a 2xx answer."""
    try:
        async with session.request(method, url, params=params, data=data, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status in (401, 403, 429):
                raise Refused(source, r.status, r.headers.get("Retry-After"))
            body = await r.read()
            if r.status >= 400:
                text = body[:300].decode("utf-8", "replace")
                why = "the query took too long" if "TimeoutException" in text else f"HTTP {r.status}"
                raise SourceError(f"{source}: {why}")
            if want == "bytes":
                return body, dict(r.headers), r.status
            if want == "text":
                return body.decode(r.charset or "utf-8", "replace")
            try:
                return json.loads(body)
            except ValueError as e:
                raise SourceError(f"{source}: the answer wasn't JSON ({body[:80]!r})") from e
    except asyncio.TimeoutError as e:
        raise SourceError(f"{source}: no answer within {timeout:.0f}s") from e
    except aiohttp.ClientError as e:
        raise SourceError(f"{source}: {type(e).__name__}: {str(e)[:150]}") from e


def website(value: str | None) -> str | None:
    """'www.keells.com' -> 'https://www.keells.com'. Only http(s) addresses count."""
    value = (value or "").strip()
    if not value or " " in value or "." not in value:
        return None
    if "://" not in value:
        value = "https://" + value.lstrip("/")
    return value if value.lower().startswith(("http://", "https://")) else None


def company(source: str, source_id: str, name: str | None, country: str | None, site: str | None = None,
            industry: str | None = None, **extra) -> dict:
    """One company row, with its website's root domain (so `update` reads each website once)."""
    from techintel.domains import root_domain
    url = website(site)
    return {"id": f"{source}:{source_id}", "source": source, "source_id": str(source_id),
            "name": " ".join((name or "").split()) or None, "country": (country or "").upper() or None,
            "website": url, "domain": root_domain(url) if url else None, "industry": industry or None,
            "extra": {k: v for k, v in extra.items() if v not in (None, "", [], {})}}


def all_sources() -> dict:
    from .asic import Asic
    from .companies_house import CompaniesHouse
    from .cse import Cse
    from .gleif import Gleif
    from .sec import Sec
    from .wikidata import Wikidata
    return {s.name: s for s in (Wikidata, Gleif, Cse, CompaniesHouse, Asic, Sec)}
