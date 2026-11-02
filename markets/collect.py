"""Collect one snapshot of every configured market: US + Sri Lanka stocks and indices,
commodities, exchange rates and the crypto top N. Sources run in parallel; one failing
source never stops the others."""

import asyncio
import logging
import time
from datetime import datetime, timezone

import aiohttp

from .providers import crypto, cse, quote, yahoo

logger = logging.getLogger("markets")
TROY_OUNCE_G = 31.1034768
GROUPS = ("us", "lk", "metals", "fx", "crypto")


def now_iso(micro: bool = False) -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ" if micro else "%Y-%m-%dT%H:%M:%SZ")


def lkr_metal_prices(quotes: list[dict]) -> list[dict]:
    """Gold/silver in LKR per gram and per pawn (8 g), from the USD/oz price and USD/LKR.
    An international reference price, not a local jeweller's rate (no duties or margins)."""
    by_symbol = {q["symbol"]: q for q in quotes}
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


async def run_collection(settings: dict, only: list[str] | None = None) -> dict:
    groups = [g for g in GROUPS if not only or g in only]
    fetch_cfg = settings.get("fetch", {})
    timeout = float(fetch_cfg.get("timeout_seconds", 25))
    sem = asyncio.Semaphore(int(fetch_cfg.get("concurrency", 6)))
    run_at = now_iso(micro=True)
    quotes: list[dict] = []
    sources: list[dict] = []

    async def job(source: str, group: str, coro_fn):
        t = time.monotonic()
        try:
            async with sem:
                got = await coro_fn()
            quotes.extend(got)
            sources.append({"source": source, "group": group, "ok": True, "count": len(got), "seconds": round(time.monotonic() - t, 2)})
            logger.info("%-8s %-24s %4d quote(s)", group, source, len(got))
        except Exception as e:  # noqa: BLE001  one failing source never stops the rest
            sources.append({"source": source, "group": group, "ok": False, "count": 0, "error": str(e)[:300],
                            "seconds": round(time.monotonic() - t, 2)})
            logger.warning("%-8s %-24s FAILED: %s", group, source, str(e)[:200])

    headers = {"User-Agent": fetch_cfg.get("user_agent") or "Mozilla/5.0", "Accept": "application/json,*/*"}
    async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
        jobs = []

        def yahoo_job(symbol, asset_class, group, name=None, extra=None):
            jobs.append(job(f"yahoo {symbol}", group,
                            lambda: _one(yahoo.fetch(session, symbol, asset_class, name, extra, timeout))))

        if "us" in groups:
            for i in settings.get("us_indices", []):
                yahoo_job(i["symbol"], "index", "us", i.get("name"), {"market": "US"})
            for s in settings.get("us_stocks", []):
                yahoo_job(s, "stock_us", "us")
        if "metals" in groups:
            for c in settings.get("commodities", []):
                yahoo_job(c["symbol"], "commodity", "metals", c.get("name"),
                          {k: v for k, v in c.items() if k in ("unit", "metal")})
        if "fx" in groups or "metals" in groups:      # USD/LKR is needed for gold in LKR
            for f in settings.get("fx", []):
                if "fx" in groups or f.get("lkr_rate"):
                    yahoo_job(f["symbol"], "fx", "fx", f.get("name"), {"lkr_rate": bool(f.get("lkr_rate"))})
        if "lk" in groups and settings.get("cse", {}).get("enabled", True):
            jobs.append(job("cse indices", "lk", lambda: cse.fetch_indices(session, timeout)))
            jobs.append(job("cse companies", "lk", lambda: cse.fetch_stocks(session, timeout)))
        if "crypto" in groups:
            c = settings.get("crypto", {})
            jobs.append(job("crypto top", "crypto",
                            lambda: crypto.fetch_top(session, int(c.get("top_n", 100)), c.get("vs_currency", "usd"), timeout)))
        await asyncio.gather(*jobs)

    if "metals" in groups:
        quotes.extend(lkr_metal_prices(quotes))
    return {"run_at": run_at, "finished_at": now_iso(), "quotes": quotes, "sources": sources}


async def _one(coro) -> list[dict]:
    return [await coro]
