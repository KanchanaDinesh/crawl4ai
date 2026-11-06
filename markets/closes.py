"""Daily closing prices for the CSE and the crypto top 100, for indicators that need weeks of history.

Our own runs only started on 2026-11-03, so these come from public sources, one source per class:

  stock_lk  cse.lk's own API (robots.txt allows it; no limit or terms are published, its footer's "Terms & Conditions"
            isn't a link), one call every 1.5 s:
              POST /api/companyChartDataByStock  stockId=<id>&period=5   about a year, one row per day the share traded
              POST /api/tradeSummary                                     today's price of everything that traded
              POST /api/dailyMarketSummery                               the last two trading days
              POST /api/companyInfoSummery  symbol=<symbol>              a share's id, when it didn't trade today
            After the first time, one tradeSummary call adds the day's closes for every share. A share only needs its
            own call again if a whole trading day was missed.
  crypto    CoinPaprika's free API (personal use, attribution required, 1 year of daily prices):
            /v1/tickers/<id>/historical?start=...&interval=1d. The price at 00:00 UTC of each day.
            Its own 402 answer gives the limits: 60 requests an hour (soft) and 25k a month (its pricing page says 10 a
            second, which is not what happens). So a run takes at most PAPRIKA_CALLS_PER_HOUR coins, stalest first, and
            the rest wait for a later run. One call fills all the days a coin is behind, so a coin that waits loses nothing.
            /v1/coins (7.5 MB) maps our symbols to its ids, only when a coin has no id yet; a coin it doesn't have is
            looked for again after a week.

Not CoinGecko, which gives our live prices: its API terms discourage storing data and forbid deriving from it, apart
from some conditions a saved history can't meet (see STATUS.md). Not Binance: its robots.txt disallows the API host.

A close is only saved when the day is over: during trading the CSE's numbers for today are not a close. Each share is
saved as it arrives, so a run stopped anywhere continues there. On 401, 402, 403 or 429 the source is left alone for
REFUSAL_WAIT_MINUTES, with no retries.
"""

import asyncio
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

import aiohttp

logger = logging.getLogger("markets")

CSE_API = "https://www.cse.lk/api"
PAPRIKA_API = "https://api.coinpaprika.com/v1"
CSE_PAUSE = 1.5
PAPRIKA_PAUSE = 2.0
PAPRIKA_CALLS_PER_HOUR = 50             # its soft limit is 60 an hour; the margin covers /v1/coins and second candidates
NO_MATCH_RETRY_DAYS = 7
LK = timezone(timedelta(hours=5, minutes=30))
CSE_DAY_OVER = (15, 0)                  # trading ends at 14:30 Sri Lankan time
REFUSED_STATUSES = (401, 402, 403, 429)
REFUSAL_WAIT_MINUTES = 60
MAX_ERRORS_IN_A_ROW = 5
PAPRIKA_DAYS = 364                      # the free plan answers 402 further back than a year
PRICE_CHECK_FACTOR = 2.0                # a matched coin's price must be within this factor of the price we know
RANK_GAP = 300                          # a coin in our top 100 is not number 2,302 at CoinPaprika, whatever its name


class Refused(Exception):
    """The source said no: stop asking it."""


async def _json(session: aiohttp.ClientSession, method: str, url: str, *, data=None, params=None, timeout: float = 30):
    async with session.request(method, url, data=data, params=params, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
        if r.status in REFUSED_STATUSES:
            raise Refused(f"HTTP {r.status}")
        if r.status >= 400:
            raise ValueError(f"HTTP {r.status}")
        return await r.json(content_type=None)


def lk_day(ms) -> date:
    return datetime.fromtimestamp(float(ms) / 1000, LK).date()


def cse_day_is_over(now: datetime) -> bool:
    local = now.astimezone(LK)
    return (local.hour, local.minute) >= CSE_DAY_OVER


def finished_day(day: date, now: datetime) -> bool:
    """A close only counts once its day's trading has ended."""
    today = now.astimezone(LK).date()
    return day < today or (day == today and cse_day_is_over(now))


def parse_cse_chart(data, now: datetime) -> list[tuple[date, float]]:
    """[(day, close), ...] oldest first, finished days only. "t" is midnight Sri Lankan time, "p" the day's close."""
    rows = data.get("chartData") if isinstance(data, dict) else None
    if rows is None:
        raise ValueError(f"unexpected chart answer: {str(data)[:120]}")
    closes = {}
    for r in rows:
        if r.get("t") and r.get("p") and float(r["p"]) > 0 and finished_day(lk_day(r["t"]), now):
            closes[lk_day(r["t"])] = float(r["p"])
    return sorted(closes.items())


def parse_trade_summary(data) -> tuple[dict[str, str], dict[str, float], date | None]:
    """From one tradeSummary call: ({symbol: id}, {symbol: close} of the latest session, that session's day).
    A share without a trade that day has no close: its last close stands."""
    rows = data.get("reqTradeSummery", []) if isinstance(data, dict) else []
    traded = [r for r in rows if r.get("symbol") and r.get("lastTradedTime")]
    session = max((lk_day(r["lastTradedTime"]) for r in traded), default=None)
    ids = {r["symbol"]: str(r["id"]) for r in rows if r.get("symbol") and r.get("id")}
    closes = {r["symbol"]: float(r["price"]) for r in traded
              if lk_day(r["lastTradedTime"]) == session and r.get("sharevolume") and r.get("price") and float(r["price"]) > 0}
    return ids, closes, session


def parse_trading_days(data) -> list[date]:
    """The last finished trading days from dailyMarketSummery, newest first."""
    days = []
    for group in data if isinstance(data, list) else []:
        if group and group[0].get("tradeDate"):
            days.append(lk_day(group[0]["tradeDate"]))
    return sorted(set(days), reverse=True)


def plan_cse(synced: dict[str, date | None], session: date | None, trading_days: list[date], now: datetime) -> dict:
    """What a run has to do. Pure, so the gap logic can be tested without a network.

    target       the newest finished trading day
    append       True when one tradeSummary call gives target's closes, because `session` is that finished day
    chart        shares that must fetch their own history: never fetched, or missing a day tradeSummary can't give.
                 tradeSummary only ever has the latest session, so during trading it can't fill in yesterday
    """
    known = set(trading_days)
    if session:
        known.add(session)
    finished = sorted((d for d in known if finished_day(d, now)), reverse=True)
    if not finished:
        return {"target": None, "chart": sorted(s for s, d in synced.items() if d is None), "append": False}
    target = finished[0]
    append = session == target
    needed = next((d for d in finished if d < target), None) if append else target
    chart = sorted(s for s, d in synced.items() if d is None or needed is None or d < needed)
    return {"target": target, "chart": chart, "append": append}


def _name_key(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def match_coins(coins: list[dict], symbol: str, name: str | None, gecko_id: str | None = None, keep: int = 2,
                rank: int | None = None) -> list[str]:
    """CoinPaprika ids for one of our coins, best first. Symbols aren't unique and names mislead: our "Jupiter" (JUP, rank
    82) is its "Jupiter Exchange Token" at 92, and its coin named "Jupiter" is at 2,302. So a coin near our rank comes
    first, then the same name or slug, then the highest ranked. The price check in sync_crypto has the last word."""
    same = [c for c in coins if (c.get("symbol") or "").upper() == symbol.upper() and c.get("is_active", True)]

    def score(c):
        slug = c["id"].split("-", 1)[1] if "-" in c["id"] else c["id"]
        named = _name_key(c.get("name")) == _name_key(name) or (gecko_id and _name_key(slug) == _name_key(gecko_id))
        far = bool(rank) and (not c.get("rank") or abs(c["rank"] - rank) > RANK_GAP)
        return (1 if far else 0, 0 if named else 1, c.get("rank") or 10 ** 9)
    return [c["id"] for c in sorted(same, key=score)[:keep]]


def parse_paprika(rows) -> list[tuple[date, float]]:
    if not isinstance(rows, list):
        raise ValueError(f"unexpected answer: {str(rows)[:120]}")
    closes = {}
    for r in rows:
        if r.get("timestamp") and r.get("price") and float(r["price"]) > 0:
            closes[date.fromisoformat(r["timestamp"][:10])] = float(r["price"])
    return sorted(closes.items())


def price_is_plausible(close: float, known_price: float | None) -> bool:
    """Same ticker, different coin is common. A wrong coin would give a believable-looking indicator of the wrong
    thing, so a new match has to agree with the price we already have."""
    return bool(known_price) and known_price / PRICE_CHECK_FACTOR <= close <= known_price * PRICE_CHECK_FACTOR


def _on_refusal(store, source: str, label: str, error: Refused) -> None:
    store.note_refusal(source, str(error))
    logger.warning("%s said no (%s) at %s. Stopping and leaving it alone for %d minutes.", source, error, label, REFUSAL_WAIT_MINUTES)


async def sync_cse(session, store, now: datetime, deadline: float, pause: float = CSE_PAUSE) -> dict:
    summary = {"source": "cse", "fetched": 0, "appended": 0, "errors": 0, "left": 0, "refused": None}
    try:
        trade = await _json(session, "POST", f"{CSE_API}/tradeSummary")
        await asyncio.sleep(pause)
        days = parse_trading_days(await _json(session, "POST", f"{CSE_API}/dailyMarketSummery"))
    except Refused as e:
        _on_refusal(store, "cse", "the first call", e)
        return {**summary, "refused": str(e)}
    ids, today_closes, session_day = parse_trade_summary(trade)
    state = store.sync_state("stock_lk")
    symbols = set(store.known_symbols("stock_lk")) | set(ids)
    plan = plan_cse({s: state.get(s, {}).get("synced_day") for s in symbols}, session_day, days, now)
    target = plan["target"]
    chart = set(plan["chart"])

    if plan["append"]:
        added = {s: c for s, c in today_closes.items() if s in symbols and s not in chart}
        store.save_day_closes("stock_lk", target, added, "cse")
        store.mark_synced("stock_lk", [s for s in symbols if s not in chart], target, "cse",
                          {s: ids[s] for s in ids if s in symbols and s not in chart})
        summary["appended"] = len(added)

    in_a_row = 0
    for i, symbol in enumerate(sorted(chart)):
        if time.monotonic() > deadline:
            summary["left"] = len(chart) - i
            logger.info("CSE: out of time, %d shares left. Run it again to continue.", summary["left"])
            break
        await asyncio.sleep(pause)
        try:
            stock_id = ids.get(symbol) or state.get(symbol, {}).get("source_id")
            if not stock_id:
                info = await _json(session, "POST", f"{CSE_API}/companyInfoSummery", data={"symbol": symbol})
                stock_id = str(((info or {}).get("reqSymbolInfo") or {}).get("id") or "")
                await asyncio.sleep(pause)
            if not stock_id:
                raise ValueError("cse.lk doesn't know this share")
            closes = parse_cse_chart(await _json(session, "POST", f"{CSE_API}/companyChartDataByStock",
                                                 data={"stockId": stock_id, "period": "5"}), now)
            store.save_closes("stock_lk", symbol, closes, "cse", source_id=stock_id, synced_day=target,
                              error=None if closes else "cse.lk has no daily prices for this share")
            summary["fetched"] += 1
            in_a_row = 0
            if summary["fetched"] % 25 == 0:
                logger.info("CSE: %d of %d shares", summary["fetched"], len(chart))
        except Refused as e:
            _on_refusal(store, "cse", symbol, e)
            return {**summary, "refused": str(e), "left": len(chart) - i}
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            summary["errors"] += 1
            in_a_row += 1
            store.note_sync_error("stock_lk", symbol, "cse", str(e) or type(e).__name__)
            logger.warning("CSE %s: %s", symbol, str(e) or type(e).__name__)
            if in_a_row >= MAX_ERRORS_IN_A_ROW:
                logger.warning("CSE: %d errors in a row, stopping for now.", in_a_row)
                summary["left"] = len(chart) - i - 1
                break
    return summary


def _waiting_after_failure(saved: dict, today: date) -> bool:
    """A coin CoinPaprika doesn't have won't appear tomorrow, and looking means a 7.5 MB download. A coin it has that
    failed gets another go tomorrow."""
    failed = saved.get("failed_on")
    if not failed:
        return False
    return failed == today if saved.get("source_id") else failed > today - timedelta(days=NO_MATCH_RETRY_DAYS)


async def sync_crypto(session, store, now: datetime, deadline: float, pause: float = PAPRIKA_PAUSE) -> dict:
    summary = {"source": "coinpaprika", "fetched": 0, "appended": 0, "errors": 0, "left": 0, "refused": None}
    today = now.astimezone(timezone.utc).date()
    state = store.sync_state("crypto")
    coins = store.known_coins()
    behind = [c for c in coins if (state.get(c["symbol"], {}).get("synced_day") or date.min) < today
              and not _waiting_after_failure(state.get(c["symbol"], {}), today)]
    behind.sort(key=lambda c: state.get(c["symbol"], {}).get("synced_day") or date.min)    # stable: biggest first among equals
    budget = max(0, PAPRIKA_CALLS_PER_HOUR - store.calls_last_hour("coinpaprika"))
    summary["left"] = max(0, len(behind) - budget)
    if summary["left"]:
        logger.info("CoinPaprika allows about 60 requests an hour: %d coins now, %d in a later run (at least an hour apart).", budget, summary["left"])
    behind = behind[:budget]
    waiting = summary["left"]
    catalogue = None
    in_a_row = 0
    for i, coin in enumerate(behind):
        symbol = coin["symbol"]
        if time.monotonic() > deadline:
            summary["left"] = waiting + len(behind) - i
            logger.info("CoinPaprika: out of time, %d coins left. Run it again to continue.", summary["left"])
            break
        saved = state.get(symbol, {})
        try:
            ids = [saved["source_id"]] if saved.get("source_id") else None
            if ids is None:
                if catalogue is None:
                    await asyncio.sleep(pause)
                    catalogue = await _json(session, "GET", f"{PAPRIKA_API}/coins", timeout=120)
                ids = match_coins(catalogue, symbol, coin.get("name"), coin.get("gecko_id"), rank=coin.get("rank"))
            last = saved.get("last_day")
            start = max(last - timedelta(days=3), today - timedelta(days=PAPRIKA_DAYS)) if last else today - timedelta(days=PAPRIKA_DAYS)
            chosen, closes = None, []
            for paprika_id in ids:
                await asyncio.sleep(pause)
                got = parse_paprika(await _json(session, "GET", f"{PAPRIKA_API}/tickers/{paprika_id}/historical",
                                                params={"start": start.isoformat(), "interval": "1d"}))
                if saved.get("source_id") or (got and price_is_plausible(got[-1][1], coin.get("price"))):
                    chosen, closes = paprika_id, got
                    break
            if chosen is None:
                store.note_sync_error("crypto", symbol, "coinpaprika", "CoinPaprika has no coin that matches this one")
                logger.info("CoinPaprika: no match for %s (%s)", symbol, coin.get("name"))
                continue
            store.save_closes("crypto", symbol, closes, "coinpaprika", source_id=chosen, synced_day=today, error=None)
            summary["fetched"] += 1
            in_a_row = 0
        except Refused as e:
            _on_refusal(store, "coinpaprika", symbol, e)
            return {**summary, "refused": str(e), "left": waiting + len(behind) - i}
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            summary["errors"] += 1
            in_a_row += 1
            store.note_sync_error("crypto", symbol, "coinpaprika", str(e) or type(e).__name__)
            logger.warning("CoinPaprika %s: %s", symbol, str(e) or type(e).__name__)
            if in_a_row >= MAX_ERRORS_IN_A_ROW:
                logger.warning("CoinPaprika: %d errors in a row, stopping for now.", in_a_row)
                summary["left"] = waiting + len(behind) - i - 1
                break
    return summary


async def sync_all(store, settings: dict, only: list[str], max_minutes: float, now: datetime | None = None) -> list[dict]:
    """Each source on its own pace, side by side. A refused source waits; the other carries on."""
    now = now or datetime.now(timezone.utc)
    deadline = time.monotonic() + max_minutes * 60
    agent = settings.get("fetch", {}).get("user_agent") or "personal-markets-tracker/1.0"
    headers = {"User-Agent": agent, "Accept": "application/json, text/plain, */*"}
    jobs = {"lk": ("cse", sync_cse), "crypto": ("coinpaprika", sync_crypto)}
    async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
        async def run(source, fn):
            refused = store.refused_recently(source, REFUSAL_WAIT_MINUTES)
            if refused:
                logger.info("%s refused us %s ago (%s): leaving it alone for now.", source, refused["ago"], refused["reason"])
                return {"source": source, "fetched": 0, "appended": 0, "errors": 0, "left": 0, "refused": refused["reason"]}
            try:
                return await fn(session, store, now, deadline)
            except (aiohttp.ClientError, TimeoutError, ValueError) as e:
                logger.warning("%s: %s", source, str(e) or type(e).__name__)
                return {"source": source, "fetched": 0, "appended": 0, "errors": 1, "left": 0, "refused": None}
        return list(await asyncio.gather(*(run(*jobs[g]) for g in only)))
