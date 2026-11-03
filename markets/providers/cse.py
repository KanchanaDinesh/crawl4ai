"""Colombo Stock Exchange (cse.lk's own JSON API, no key).

  POST https://www.cse.lk/api/dailyMarketSummery  -> [[today], [previous day]]: ASPI ("asi"), S&P SL20 ("spt"), turnover ...
  POST https://www.cse.lk/api/tradeSummary        -> {"reqTradeSummery": [one row per listed company]}
"""

import aiohttp

from . import iso_from_epoch, quote

API = "https://www.cse.lk/api"


def _ms(value) -> str | None:
    try:
        return iso_from_epoch(float(value) / 1000)
    except (TypeError, ValueError):
        return None


def parse_market_summary(data) -> list[dict]:
    """ASPI and S&P SL20, with the change from the previous trading day."""
    try:
        today = data[0][0]
    except (IndexError, KeyError, TypeError):
        raise ValueError("unexpected dailyMarketSummery format")
    try:
        prev = data[1][0]
    except (IndexError, KeyError, TypeError):
        prev = {}
    extra = {k: today.get(k) for k in ("marketTurnover", "tradesNo", "marketCap", "per", "pbv", "dy",
                                       "listedCompanyNumber", "tradeCompanyNumber", "equityForeignPurchase",
                                       "equityForeignSales")}
    as_of = _ms(today.get("tradeDate"))
    return [
        quote("index", "ASPI", "All Share Price Index (ASPI)", today.get("asi"), prev_close=prev.get("asi"),
              currency="LKR", as_of=as_of, source="cse", extra={**extra, "market": "LK"}),
        quote("index", "S&P SL20", "S&P Sri Lanka 20", today.get("spt"), prev_close=prev.get("spt"),
              currency="LKR", as_of=as_of, source="cse", extra={"market": "LK"}),
    ]


def parse_trade_summary(data) -> list[dict]:
    rows = data.get("reqTradeSummery", []) if isinstance(data, dict) else (data or [])
    out = []
    for r in rows:
        if not r.get("symbol"):
            continue
        out.append(quote(
            "stock_lk", r["symbol"], (r.get("name") or "").title().replace(" Plc", " PLC"), r.get("price"),
            prev_close=r.get("previousClose"), change=r.get("change"), change_pct=r.get("percentageChange"),
            currency="LKR", volume=r.get("sharevolume"), market_cap=r.get("marketCap"), high=r.get("high"),
            low=r.get("low"), as_of=_ms(r.get("lastTradedTime")), source="cse",
            extra={"turnover": r.get("turnover"), "trades": r.get("tradevolume"), "open": r.get("open")}))
    return out


async def _post(session: aiohttp.ClientSession, endpoint: str, timeout: float):
    # Only our own headers: the API answers without pretending to be cse.lk's website (Origin/Referer)
    headers = {"Accept": "application/json, text/plain, */*"}
    async with session.post(f"{API}/{endpoint}", headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
        if resp.status >= 400:
            raise ValueError(f"{endpoint}: HTTP {resp.status}")
        return await resp.json(content_type=None)


async def fetch_indices(session, timeout: float = 25) -> list[dict]:
    return parse_market_summary(await _post(session, "dailyMarketSummery", timeout))


async def fetch_stocks(session, timeout: float = 25) -> list[dict]:
    return parse_trade_summary(await _post(session, "tradeSummary", timeout))
