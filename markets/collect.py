"""Collect one snapshot of every configured market: US + Sri Lanka stocks and indices,
commodities, exchange rates and the crypto top N. Sources run in parallel; one failing
source never stops the others.

Where each price comes from, in order (the first that works wins):
  US indices/stocks, commodities  Yahoo Finance -> Stooq (Stooq now shows a bot check: no real backup)
  exchange rates                  Yahoo Finance -> Stooq -> open.er-api.com (daily)
  Sri Lanka                       cse.lk
  crypto                          CoinGecko -> CoinPaprika
If Yahoo blocks or rate-limits us, it's skipped for the rest of the run (no retrying).
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import aiohttp

from .providers import crypto, cse, fxapi, quote, stooq, yahoo

logger = logging.getLogger("markets")
TROY_OUNCE_G = 31.1034768
GROUPS = ("us", "lk", "metals", "fx", "crypto")


def now_iso(micro: bool = False) -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ" if micro else "%Y-%m-%dT%H:%M:%SZ")


def lkr_metal_prices(quotes: list[dict]) -> list[dict]:
    """Gold/silver in LKR per gram and per pawn (8 g), from the USD/oz price and USD/LKR.
    An international reference price, not a local jeweller's rate (no duties or margins)."""
    fx = next((q for q in quotes if q["asset_class"] == "fx" and q["extra"].get("lkr_rate")), None)
    out = []
    if not fx or not fx.get("price"):
        return out
    for q in quotes:
        metal = q["extra"].get("metal") if q["asset_class"] == "commodity" else None
        if not metal or not q.get("price"):
            continue
        per_g = q["price"] / TROY_OUNCE_G * fx["price"]
        prev_g = (q["prev_close"] / TROY_OUNCE_G * (fx.get("prev_close") or fx["price"])) if q.get("prev_close") else None
        label = metal.title()
        rows = [(f"{metal.upper()}_LKR_G", f"{label} 24K, LKR per gram" if metal == "gold" else f"{label}, LKR per gram", 1)]
        if metal == "gold":
            rows += [("GOLD24K_LKR_PAWN", "Gold 24K, LKR per pawn (8 g)", 8),
                     ("GOLD22K_LKR_PAWN", "Gold 22K, LKR per pawn (8 g)", 8 * 22 / 24)]
        for symbol, name, factor in rows:
            out.append(quote("commodity_lkr", symbol, name, per_g * factor,
                             prev_close=prev_g * factor if prev_g else None, currency="LKR", as_of=q.get("as_of"),
                             source="derived", extra={"from": [q["symbol"], fx["symbol"]],
                                                      "note": "international price converted to LKR; not a local jeweller's rate"}))
    return out


def fill_change_from_previous(quotes: list[dict], previous: dict) -> None:
    """For sources without a previous close (daily FX rates), compare with the last saved price
    from an earlier day, so the day's change can still be shown."""
    for q in quotes:
        if q.get("change_pct") is not None or q.get("price") is None:
            continue
        prev = previous.get((q["asset_class"], q["symbol"]))
        if not prev or not prev.get("price") or (prev.get("as_of") or "")[:10] == (q.get("as_of") or "")[:10]:
            continue
        q["prev_close"] = prev["price"]
        q["change"] = q["price"] - prev["price"]
        q["change_pct"] = (q["price"] - prev["price"]) / prev["price"] * 100
        q["extra"]["change_vs"] = "previous saved rate"


async def run_collection(settings: dict, only: list[str] | None = None, previous: dict | None = None) -> dict:
    groups = [g for g in GROUPS if not only or g in only]
    fetch_cfg = settings.get("fetch", {})
    timeout = float(fetch_cfg.get("timeout_seconds", 25))
    sem = asyncio.Semaphore(int(fetch_cfg.get("concurrency", 6)))
    run_at = now_iso(micro=True)
    quotes: list[dict] = []
    sources: list[dict] = []
    backoff = yahoo.YahooBackoff()
    stooq_blocked: list[str] = []          # once Stooq shows its bot check, it isn't asked again this run
    use_yahoo = fetch_cfg.get("try_yahoo", True)
    use_stooq = fetch_cfg.get("stooq_fallback", True)

    async def job(source: str, group: str, coro_fn):
        t = time.monotonic()
        try:
            async with sem:
                got = await coro_fn()
            quotes.extend(got)
            via = sorted({q["source"] for q in got})
            sources.append({"source": source, "group": group, "ok": True, "count": len(got),
                            "via": ", ".join(via), "seconds": round(time.monotonic() - t, 2)})
            logger.info("%-8s %-22s %4d quote(s) via %s", group, source, len(got), ", ".join(via) or "-")
        except Exception as e:  # noqa: BLE001  one failing source never stops the rest
            sources.append({"source": source, "group": group, "ok": False, "count": 0, "error": str(e)[:300],
                            "seconds": round(time.monotonic() - t, 2)})
            logger.warning("%-8s %-22s FAILED: %s", group, source, str(e)[:200])

    headers = {"User-Agent": fetch_cfg.get("user_agent") or "personal-markets-tracker/1.0", "Accept": "application/json,text/csv,*/*"}
    async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
        jobs = []

        def price_job(item: dict, asset_class: str, group: str):
            symbol, name = item["symbol"], item.get("name")
            extra = {k: v for k, v in item.items() if k in ("unit", "metal", "lkr_rate", "market")}
            stooq_symbol = item.get("stooq") or (stooq.default_symbol(symbol) if asset_class == "stock_us" else None)

            async def run():
                errors = []
                if use_yahoo:
                    try:
                        return [await yahoo.fetch(session, symbol, asset_class, name, extra, timeout, backoff)]
                    except ValueError as e:
                        errors.append(f"yahoo: {e}")
                if use_stooq and stooq_symbol and stooq_blocked:
                    errors.append(f"stooq: skipped ({stooq_blocked[0]})")
                elif use_stooq and stooq_symbol:
                    try:
                        return [await stooq.fetch(session, stooq_symbol, asset_class, symbol, name or symbol,
                                                  "USD" if asset_class != "fx" else None, extra, timeout)]
                    except stooq.StooqBlocked as e:
                        stooq_blocked.append(str(e))
                        errors.append(f"stooq: {e}")
                    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
                        errors.append(f"stooq: {e}")
                raise ValueError("; ".join(errors) or "no source configured")
            jobs.append(job(symbol, group, run))

        if "us" in groups:
            for i in settings.get("us_indices", []):
                price_job({**i, "market": "US"}, "index", "us")
            for s in settings.get("us_stocks", []):
                price_job({"symbol": s}, "stock_us", "us")
        if "metals" in groups:
            for c in settings.get("commodities", []):
                price_job(c, "commodity", "metals")
        fx_items = [f for f in settings.get("fx", []) if "fx" in groups or ("metals" in groups and f.get("lkr_rate"))]
        for f in fx_items:                                   # USD/LKR is also needed for gold in LKR
            price_job(f, "fx", "fx")
        if "lk" in groups and settings.get("cse", {}).get("enabled", True):
            jobs.append(job("cse indices", "lk", lambda: cse.fetch_indices(session, timeout)))
            jobs.append(job("cse companies", "lk", lambda: cse.fetch_stocks(session, timeout)))
        if "crypto" in groups:
            c = settings.get("crypto", {})
            jobs.append(job("crypto top", "crypto",
                            lambda: crypto.fetch_top(session, int(c.get("top_n", 100)), c.get("vs_currency", "usd"), timeout)))
        await asyncio.gather(*jobs)

        # Exchange rates still missing (Yahoo and Stooq both failed): one call to open.er-api.com
        have = {q["symbol"] for q in quotes if q["asset_class"] == "fx"}
        missing = [f for f in fx_items if f["symbol"] not in have]
        if missing and fetch_cfg.get("fx_api_fallback", True):
            failed_before = {f["symbol"] for f in missing}
            await job("open.er-api.com", "fx", lambda: fxapi.fetch(session, missing, timeout))
            got = {q["symbol"] for q in quotes if q["asset_class"] == "fx"}
            for s in sources:                                # now covered: don't report them as failed
                if s["source"] in failed_before and s["source"] in got and not s["ok"]:
                    s.update(ok=True, count=1, via="open.er-api.com", error=None)

    if backoff.reason:
        logger.warning("Yahoo Finance was skipped for most of this run: %s. Prices came from the backup sources.",
                       backoff.reason)
    fill_change_from_previous(quotes, previous or {})
    if "metals" in groups:
        quotes.extend(lkr_metal_prices(quotes))
    return {"run_at": run_at, "finished_at": now_iso(), "quotes": quotes, "sources": sources,
            "yahoo_backoff": backoff.reason}
