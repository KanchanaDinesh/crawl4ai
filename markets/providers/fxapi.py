"""Exchange rates from open.er-api.com (ExchangeRate-API's free open endpoint, no key; updated daily).

GET https://open.er-api.com/v6/latest/USD  ->  {"result": "success", "time_last_update_unix": ..., "rates": {"LKR": 296.1, ...}}
Last fallback for FX when Yahoo and Stooq both fail. Gives the rate only (no previous close),
so the day's change is worked out from the previous saved run instead (collect.py).
"""

import aiohttp

from . import iso_from_epoch, quote

API = "https://open.er-api.com/v6/latest/USD"

# Yahoo symbol -> how to read it from USD-based rates
PAIRS = {
    "LKR=X": ("LKR", False), "INR=X": ("INR", False), "JPY=X": ("JPY", False),
    "EURUSD=X": ("EUR", True), "GBPUSD=X": ("GBP", True),   # quoted the other way round: 1 / rate
}


def parse_rates(data: dict, wanted: list[dict]) -> list[dict]:
    if (data or {}).get("result") != "success":
        raise ValueError(f"open.er-api.com: {(data or {}).get('error-type') or 'unexpected response'}")
    rates, out = data.get("rates") or {}, []
    as_of = iso_from_epoch(data.get("time_last_update_unix"))
    for item in wanted:
        pair = PAIRS.get(item["symbol"])
        if not pair or not rates.get(pair[0]):
            continue
        code, inverse = pair
        price = 1 / rates[code] if inverse else rates[code]
        out.append(quote("fx", item["symbol"], item.get("name") or item["symbol"], price,
                         currency="USD" if inverse else code, as_of=as_of, source="open.er-api.com",
                         extra={"lkr_rate": bool(item.get("lkr_rate")), "daily_rate": True}))
    return out


async def fetch(session: aiohttp.ClientSession, wanted: list[dict], timeout: float = 25) -> list[dict]:
    async with session.get(API, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
        if resp.status >= 400:
            raise ValueError(f"open.er-api.com HTTP {resp.status}")
        return parse_rates(await resp.json(content_type=None), wanted)
