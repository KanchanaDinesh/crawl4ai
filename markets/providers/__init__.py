"""Market data providers. Each module has a pure parse_* function (tested offline with real
response samples) and an async fetch function. Every quote is a dict with these keys:

    asset_class  index | stock_us | stock_lk | commodity | commodity_lkr | fx | crypto
    symbol, name, price, prev_close, change, change_pct, currency,
    volume, market_cap, rank, high, low, as_of (ISO UTC), source, extra (dict)
"""

from datetime import datetime, timezone


def quote(asset_class: str, symbol: str, name: str, price, *, prev_close=None, change=None, change_pct=None,
          currency=None, volume=None, market_cap=None, rank=None, high=None, low=None, as_of=None,
          source: str = "", extra: dict | None = None) -> dict:
    """Build a quote, filling in change / change_pct from prev_close when the source doesn't give them."""
    price = _num(price)
    prev_close = _num(prev_close)
    change, change_pct = _num(change), _num(change_pct)
    if price is not None and prev_close not in (None, 0):
        if change is None:
            change = price - prev_close
        if change_pct is None:
            change_pct = (price - prev_close) / prev_close * 100
    return {"asset_class": asset_class, "symbol": symbol, "name": name, "price": price, "prev_close": prev_close,
            "change": change, "change_pct": change_pct, "currency": currency, "volume": _num(volume),
            "market_cap": _num(market_cap), "rank": rank, "high": _num(high), "low": _num(low), "as_of": as_of,
            "source": source, "extra": extra or {}}


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def iso_from_epoch(seconds) -> str | None:
    try:
        return datetime.fromtimestamp(float(seconds), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return None
