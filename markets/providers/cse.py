"""Colombo Stock Exchange (cse.lk's own JSON API, no key).

  POST https://www.cse.lk/api/dailyMarketSummery  -> [[last trading day], [the day before]]: ASPI ("asi"),
                                                     S&P SL20 ("spp"), S&P SL20 total return ("spt"), turnover, P/E ...
  POST https://www.cse.lk/api/aspiData            -> ASPI now: {"value", "change", "percentage", "highValue", "lowValue",
  POST https://www.cse.lk/api/snpData             -> S&P SL20 now: same fields                     "timestamp" (ms)}
  POST https://www.cse.lk/api/marketStatus        -> {"status": "Regular Trading" | "Market Closed" | ...}
  POST https://www.cse.lk/api/tradeSummary        -> {"reqTradeSummery": [one row per listed company]}

The daily summary only has finished trading days: during the day it still shows yesterday. So the index values
come from aspiData / snpData (live, same as cse.lk's front page), with the summary as the fallback and for P/E,
turnover etc. "spt" is the S&P SL20 *total return* index (about twice the price index): saved as "S&P SL20 TRI".
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
    out = [
        quote("index", "ASPI", "All Share Price Index (ASPI)", today.get("asi"), prev_close=prev.get("asi"),
              currency="LKR", as_of=as_of, source="cse", extra={**extra, "market": "LK", "field": "asi"}),
        quote("index", "S&P SL20", "S&P Sri Lanka 20", today.get("spp"), prev_close=prev.get("spp"),
              currency="LKR", as_of=as_of, source="cse", extra={"market": "LK", "field": "spp"}),
    ]
    if today.get("spt") is not None:
        out.append(quote("index", "S&P SL20 TRI", "S&P Sri Lanka 20 Total Return", today.get("spt"),
                         prev_close=prev.get("spt"), currency="LKR", as_of=as_of, source="cse",
                         extra={"market": "LK", "field": "spt"}))
    return [q for q in out if q["price"] is not None]


def apply_live(q: dict, live: dict | None, status: str | None = None) -> dict:
    """Today's value from aspiData / snpData, if it is newer than the daily summary's last trading day.
    The change is the source's own, against the previous close."""
    if not isinstance(live, dict) or live.get("value") is None:
        return q
    as_of = _ms(live.get("timestamp"))
    if q.get("as_of") and as_of and as_of < q["as_of"]:
        return q
    value, change = float(live["value"]), live.get("change")
    prev = value - float(change) if change is not None else q.get("prev_close")
    updated = quote(q["asset_class"], q["symbol"], q["name"], value, prev_close=prev, change=change,
                    change_pct=live.get("percentage"), currency=q["currency"], high=live.get("highValue"),
                    low=live.get("lowValue"), as_of=as_of or q.get("as_of"), source="cse",
                    extra={**q.get("extra", {}), "live": True, "summary_date": q.get("as_of"),
                           **({"market_status": status} if status else {})})
    return updated


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
    """ASPI and S&P SL20 now (live during trading), from the daily summary if the live values can't be had."""
    quotes = parse_market_summary(await _post(session, "dailyMarketSummery", timeout))
    live = {}
    for symbol, endpoint in (("ASPI", "aspiData"), ("S&P SL20", "snpData"), ("status", "marketStatus")):
        try:
            live[symbol] = await _post(session, endpoint, timeout)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            live[symbol] = None                    # the end-of-day value is still right, just not today's
    status = (live.get("status") or {}).get("status") if isinstance(live.get("status"), dict) else None
    return [apply_live(q, live.get(q["symbol"]), status) for q in quotes]


async def fetch_stocks(session, timeout: float = 25) -> list[dict]:
    return parse_trade_summary(await _post(session, "tradeSummary", timeout))
