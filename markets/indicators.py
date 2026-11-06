"""Stochastic RSI on daily closes: plain Python, no outside library, so every step can be checked by hand.

    RSI        Wilder's: the first average gain and loss are simple means of `period` changes, then each new one is
               (previous average * (period - 1) + today) / period. RSI = 100 * gain / (gain + loss).
    StochRSI   where today's RSI sits between the highest and lowest RSI of the last `stoch` days, 0 to 100.
    %K         the mean of the last 3 StochRSI values.
    %D         the mean of the last 3 %K values.

Everything is on a 0-100 scale (some charts show the same thing as 0-1).

A number is only given when it can be calculated. Prices that never moved have no RSI, and an RSI that didn't move has
no stochastic: both come back as None, never as a made-up 0 or 50. Prices that rise and then stop keep a constant RSI
apart from rounding, so "didn't move" has a small tolerance (RANGE_TOLERANCE); dividing by that noise would give a
confident, meaningless number.
"""

RSI_PERIOD = 14
STOCH_PERIOD = 14
K_SMOOTH = 3
D_SMOOTH = 3
MIN_CLOSES = 34              # 14 + 14 + 3 + 3. The first %D appears at 32 closes; 34 keeps a small margin
OVERBOUGHT = 80.0
OVERSOLD = 20.0
RANGE_TOLERANCE = 1e-9


def wilder_rsi(closes: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    """One RSI per close, None until there are `period` changes to average (or when nothing has moved at all)."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(c, 0.0) for c in changes[:period]) / period
    loss = sum(max(-c, 0.0) for c in changes[:period]) / period
    out[period] = _rsi(gain, loss)
    for i, c in enumerate(changes[period:], start=period + 1):
        gain = (gain * (period - 1) + max(c, 0.0)) / period
        loss = (loss * (period - 1) + max(-c, 0.0)) / period
        out[i] = _rsi(gain, loss)
    return out


def _rsi(gain: float, loss: float) -> float | None:
    total = gain + loss
    return 100.0 * (gain / total) if total > 0 else None


def _stochastic(values: list[float | None], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        if any(v is None for v in window):
            continue
        low, high = min(window), max(window)
        if high - low > RANGE_TOLERANCE:
            out[i] = 100.0 * ((values[i] - low) / (high - low))     # in this order the top comes to exactly 100
    return out


def _mean(values: list[float | None], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        if all(v is not None for v in window):
            out[i] = sum(window) / period
    return out


def stoch_rsi_series(closes: list[float], rsi_period: int = RSI_PERIOD, stoch_period: int = STOCH_PERIOD,
                     k_smooth: int = K_SMOOTH, d_smooth: int = D_SMOOTH) -> dict[str, list[float | None]]:
    """The whole series, one value per close: {"rsi", "stoch_rsi", "pct_k", "pct_d"}."""
    rsi = wilder_rsi(closes, rsi_period)
    raw = _stochastic(rsi, stoch_period)
    k = _mean(raw, k_smooth)
    return {"rsi": rsi, "stoch_rsi": raw, "pct_k": k, "pct_d": _mean(k, d_smooth)}


def zone(pct_k: float | None) -> str | None:
    """Overbought or oversold by %K, the line people usually read. A label for the number, not advice."""
    if pct_k is None:
        return None
    return "overbought" if pct_k > OVERBOUGHT else "oversold" if pct_k < OVERSOLD else "middle"


def latest_stoch_rsi(closes: list[float]) -> dict:
    """The newest values for one instrument: stoch_rsi, pct_k, pct_d, zone, closes_used, status (ok, short or flat),
    and a message saying why when there's no number."""
    used = len(closes)
    empty = {"stoch_rsi": None, "pct_k": None, "pct_d": None, "zone": None, "closes_used": used}
    if used < MIN_CLOSES:
        return {**empty, "status": "short", "message": f"not enough history yet ({used} of {MIN_CLOSES} days)"}
    series = stoch_rsi_series(closes)
    raw, k, d = series["stoch_rsi"][-1], series["pct_k"][-1], series["pct_d"][-1]
    values = {"stoch_rsi": raw, "pct_k": k, "pct_d": d, "zone": zone(k), "closes_used": used}
    if raw is None or k is None or d is None:
        return {**values, "status": "flat", "message": "no value: RSI hardly moved in recent days"}
    return {**values, "status": "ok", "message": None}
