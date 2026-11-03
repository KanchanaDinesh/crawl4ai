"""Stooq daily prices (no API key): backup when Yahoo Finance fails.

GET https://stooq.com/q/d/l/?s=<symbol>&i=d&d1=YYYYMMDD&d2=YYYYMMDD  ->  CSV: Date,Open,High,Low,Close,Volume
The last row is the latest close (today's, while the market is open); the row before is the previous close.

Stooq uses its own symbols: aapl.us, ^spx, ^dji, ^ndq, xauusd, xagusd, usdlkr, eurusd, cl.f ...
Set "stooq" per instrument in settings.json; US stocks default to "<symbol>.us".

Since November 2026 Stooq answers automated requests with a "verify your browser" JavaScript
puzzle. We don't solve it (no getting around bot checks): StooqBlocked is raised, and collect.py
stops asking Stooq for the rest of the run.
"""

import csv
import io
from datetime import date, timedelta

from . import quote

API = "https://stooq.com/q/d/l/"


def default_symbol(yahoo_symbol: str) -> str:
    """US stock ticker -> Stooq symbol (AAPL -> aapl.us, BRK-B -> brk-b.us)."""
    return f"{yahoo_symbol.lower()}.us"


class StooqBlocked(ValueError):
    pass


BOT_CHECK = ("verify your browser", "requires javascript", "/__verify")


def parse_history_csv(text: str, asset_class: str, symbol: str, name: str, currency: str | None = None,
                      extra: dict | None = None) -> dict:
    head = text.strip()[:200].lower()
    if any(w in text[:3000].lower() for w in BOT_CHECK):
        raise StooqBlocked("Stooq asks for a browser check (a JavaScript puzzle); not worked around")
    if not head.startswith("date,"):
        raise ValueError(f"no data from Stooq ({text.strip()[:60] or 'empty response'})")
    rows = [r for r in csv.DictReader(io.StringIO(text)) if r.get("Close") not in (None, "", "0")]
    if not rows:
        raise ValueError("no data from Stooq")
    last, prev = rows[-1], (rows[-2] if len(rows) >= 2 else {})
    closes = [float(r["Close"]) for r in rows]
    return quote(asset_class, symbol, name, last["Close"], prev_close=prev.get("Close"), currency=currency,
                 volume=last.get("Volume"), high=last.get("High"), low=last.get("Low"),
                 as_of=f"{last['Date']}T00:00:00Z", source="stooq",
                 extra={**(extra or {}), "spark": closes[-5:], "daily_close": True})


async def fetch(session, stooq_symbol: str, asset_class: str, symbol: str, name: str, currency: str | None = None,
                extra: dict | None = None, timeout: float = 25) -> dict:
    import aiohttp
    today = date.today()
    params = {"s": stooq_symbol, "i": "d", "d1": (today - timedelta(days=12)).strftime("%Y%m%d"),
              "d2": (today + timedelta(days=1)).strftime("%Y%m%d")}
    async with session.get(API, params=params, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
        if resp.status >= 400:
            raise ValueError(f"Stooq HTTP {resp.status}")
        return parse_history_csv(await resp.text(), asset_class, symbol, name, currency, extra)
