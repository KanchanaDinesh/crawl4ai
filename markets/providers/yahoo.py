"""Yahoo Finance chart data (no API key): US indices and stocks, commodities, exchange rates.

GET https://query1.finance.yahoo.com/v8/finance/chart/<symbol>?range=5d&interval=1d
The last 5 daily closes give the previous close (for the day's change) and a small sparkline.
"""

from urllib.parse import quote as urlquote

import aiohttp

from . import iso_from_epoch, quote

HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")


def parse_chart(data: dict, asset_class: str, name: str | None = None, extra: dict | None = None) -> dict:
    result = ((data or {}).get("chart") or {}).get("result") or []
    if not result:
        err = ((data or {}).get("chart") or {}).get("error") or {}
        raise ValueError(err.get("description") or "no chart data")
    r = result[0]
    meta = r.get("meta") or {}
    closes = [c for c in (((r.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []) if c is not None]
    price = meta.get("regularMarketPrice")
    if price is None and closes:
        price = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else meta.get("previousClose") or meta.get("chartPreviousClose")
    return quote(asset_class, meta.get("symbol") or "", name or meta.get("longName") or meta.get("shortName") or meta.get("symbol"),
                 price, prev_close=prev, currency=meta.get("currency"), volume=meta.get("regularMarketVolume"),
                 high=meta.get("regularMarketDayHigh"), low=meta.get("regularMarketDayLow"),
                 as_of=iso_from_epoch(meta.get("regularMarketTime")), source="yahoo",
                 extra={**(extra or {}), "exchange": meta.get("exchangeName"), "spark": [round(c, 6) for c in closes]})


async def fetch(session: aiohttp.ClientSession, symbol: str, asset_class: str, name: str | None = None,
                extra: dict | None = None, timeout: float = 25) -> dict:
    last_error = None
    for host in HOSTS:
        url = f"{host}/v8/finance/chart/{urlquote(symbol, safe='')}?range=5d&interval=1d"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400 and not (data or {}).get("chart"):
                    raise ValueError(f"HTTP {resp.status}")
                q = parse_chart(data, asset_class, name, extra)
                q["symbol"] = symbol
                return q
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            last_error = e
    raise ValueError(f"{symbol}: {last_error}")
