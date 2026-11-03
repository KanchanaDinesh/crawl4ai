"""Yahoo Finance chart data (no API key): US indices and stocks, commodities, exchange rates.

GET https://query1.finance.yahoo.com/v8/finance/chart/<symbol>?range=5d&interval=1d
The last 5 daily closes give the previous close (for the day's change) and a small sparkline.

Yahoo often blocks or rate-limits automated requests. We don't try to get around that: on the
first "blocked" (401/403) or "too many requests" (429) answer, Yahoo is skipped for the rest of
the run (YahooBackoff), and collect.py uses Stooq / open.er-api.com instead.
"""

from urllib.parse import quote as urlquote

import aiohttp

from . import iso_from_epoch, quote

HOSTS = ("https://query1.finance.yahoo.com",)


class YahooBlocked(ValueError):
    pass


class YahooBackoff:
    """Shared per run: once Yahoo says no, stop asking it."""

    def __init__(self):
        self.reason: str | None = None


def _day(epoch, meta: dict) -> int | None:
    """The market's calendar day of a timestamp (its own time zone, from gmtoffset)."""
    return None if epoch is None else (int(epoch) + int(meta.get("gmtoffset") or 0)) // 86400


def previous_close(stamps: list, closes: list, meta: dict) -> float | None:
    """The last close from a day *before* the latest price's day.

    Yahoo's daily closes sometimes don't include today yet. Taking "the second-to-last close"
    then compared today with the day before yesterday (USD/LKR once showed the wrong change)."""
    today = _day(meta.get("regularMarketTime"), meta)
    if today is None or not stamps or len(stamps) != len(closes):
        return None
    earlier = [c for t, c in zip(stamps, closes) if c is not None and _day(t, meta) < today]
    return earlier[-1] if earlier else None


def has_today(stamps: list, closes: list, meta: dict) -> bool:
    """Is the latest price's day among the daily closes?"""
    dated = [t for t, c in zip(stamps, closes) if c is not None]
    return bool(dated) and _day(dated[-1], meta) == _day(meta.get("regularMarketTime"), meta)


def parse_chart(data: dict, asset_class: str, name: str | None = None, extra: dict | None = None) -> dict:
    result = ((data or {}).get("chart") or {}).get("result") or []
    if not result:
        err = ((data or {}).get("chart") or {}).get("error") or {}
        raise ValueError(err.get("description") or "no chart data")
    r = result[0]
    meta = r.get("meta") or {}
    raw = (((r.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
    stamps = r.get("timestamp") or []
    closes = [c for c in raw if c is not None]
    price = meta.get("regularMarketPrice")
    if price is None and closes:
        price = closes[-1]
    prev = previous_close(stamps, raw, meta)
    if prev is None:                              # no dates to go by: assume the last close is today's
        prev = closes[-2] if len(closes) >= 2 else meta.get("previousClose") or meta.get("chartPreviousClose")
    elif price is not None and not has_today(stamps, raw, meta):
        closes = [*closes, price]                 # today isn't in the list yet: show it in the sparkline
    return quote(asset_class, meta.get("symbol") or "", name or meta.get("longName") or meta.get("shortName") or meta.get("symbol"),
                 price, prev_close=prev, currency=meta.get("currency"), volume=meta.get("regularMarketVolume"),
                 high=meta.get("regularMarketDayHigh"), low=meta.get("regularMarketDayLow"),
                 as_of=iso_from_epoch(meta.get("regularMarketTime")), source="yahoo",
                 extra={**(extra or {}), "exchange": meta.get("exchangeName"), "spark": [round(c, 6) for c in closes]})


async def fetch(session: aiohttp.ClientSession, symbol: str, asset_class: str, name: str | None = None,
                extra: dict | None = None, timeout: float = 25, backoff: YahooBackoff | None = None) -> dict:
    if backoff and backoff.reason:
        raise YahooBlocked(f"skipped: {backoff.reason}")
    last_error = None
    for host in HOSTS:
        url = f"{host}/v8/finance/chart/{urlquote(symbol, safe='')}?range=5d&interval=1d"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status in (401, 403, 429):
                    reason = f"Yahoo answered HTTP {resp.status} ({'rate limited' if resp.status == 429 else 'blocked'})"
                    if backoff:
                        backoff.reason = reason
                    raise YahooBlocked(reason)
                data = await resp.json(content_type=None)
                if resp.status >= 400 and not (data or {}).get("chart"):
                    raise ValueError(f"HTTP {resp.status}")
                q = parse_chart(data, asset_class, name, extra)
                q["symbol"] = symbol
                return q
        except YahooBlocked:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            last_error = e
    raise ValueError(f"{symbol}: {last_error}")
