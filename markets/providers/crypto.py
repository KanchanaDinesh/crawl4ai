"""Cryptocurrency top N (no API key).

  CoinGecko (main):  GET https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc
                         &per_page=N&page=1&price_change_percentage=1h,24h,7d
  CoinPaprika (backup): GET https://api.coinpaprika.com/v1/tickers   (sorted by rank)
"""

import aiohttp

from . import quote

COINGECKO = "https://api.coingecko.com/api/v3/coins/markets"
COINPAPRIKA = "https://api.coinpaprika.com/v1/tickers"


def parse_coingecko(rows: list, vs: str = "usd") -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError(f"unexpected CoinGecko response: {str(rows)[:120]}")
    out = []
    for r in rows:
        out.append(quote(
            "crypto", (r.get("symbol") or "").upper(), r.get("name"), r.get("current_price"),
            change=r.get("price_change_24h"),
            change_pct=r.get("price_change_percentage_24h_in_currency", r.get("price_change_percentage_24h")),
            currency=vs.upper(), volume=r.get("total_volume"), market_cap=r.get("market_cap"),
            rank=r.get("market_cap_rank"), high=r.get("high_24h"), low=r.get("low_24h"), as_of=r.get("last_updated"),
            source="coingecko",
            extra={"id": r.get("id"), "image": r.get("image"),
                   "pct_1h": r.get("price_change_percentage_1h_in_currency"),
                   "pct_7d": r.get("price_change_percentage_7d_in_currency"),
                   "ath": r.get("ath"), "circulating_supply": r.get("circulating_supply")}))
    return out


def parse_coinpaprika(rows: list, top_n: int) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError(f"unexpected CoinPaprika response: {str(rows)[:120]}")
    ranked = sorted((r for r in rows if r.get("rank")), key=lambda r: r["rank"])[:top_n]
    out = []
    for r in ranked:
        usd = (r.get("quotes") or {}).get("USD") or {}
        out.append(quote(
            "crypto", (r.get("symbol") or "").upper(), r.get("name"), usd.get("price"),
            change_pct=usd.get("percent_change_24h"), currency="USD", volume=usd.get("volume_24h"),
            market_cap=usd.get("market_cap"), rank=r.get("rank"), as_of=r.get("last_updated"), source="coinpaprika",
            extra={"id": r.get("id"), "pct_1h": usd.get("percent_change_1h"), "pct_7d": usd.get("percent_change_7d"),
                   "ath": usd.get("ath_price")}))
    return out


async def fetch_top(session: aiohttp.ClientSession, top_n: int = 100, vs: str = "usd", timeout: float = 25) -> list[dict]:
    """CoinGecko first; if it fails (it rate-limits free use), CoinPaprika."""
    errors = []
    params = {"vs_currency": vs, "order": "market_cap_desc", "per_page": str(min(top_n, 250)), "page": "1",
              "price_change_percentage": "1h,24h,7d"}
    try:
        async with session.get(COINGECKO, params=params, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status >= 400:
                raise ValueError(f"CoinGecko HTTP {resp.status}")
            return parse_coingecko(await resp.json(content_type=None), vs)[:top_n]
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        errors.append(f"coingecko: {e}")
    try:
        async with session.get(COINPAPRIKA, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status >= 400:
                raise ValueError(f"CoinPaprika HTTP {resp.status}")
            return parse_coinpaprika(await resp.json(content_type=None), top_n)
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        errors.append(f"coinpaprika: {e}")
    raise ValueError("; ".join(errors))
