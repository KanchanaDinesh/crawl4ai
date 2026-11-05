"""The day's facts, computed from the project's own databases (read-only).

Everything the summary and the video say comes from here, so a number in a post always
traces back to one query. Nothing is sent anywhere: no AI, no tokens, no internet.

One check is built in: a day's move far outside the usual (more than UNUSUAL_FACTOR times the
typical, i.e. median, daily move in the saved history) is "held back". The summary and video
then show only the level, not the change, until a second source confirms it. The median is
used because a few earlier glitches would inflate a maximum. Measured on 3 Nov 2026: USD/LKR
+3.1% was 18 times its typical move (a gap in one source's history), while Brent oil's real
-11% fall was 5.8 times and everything else under 3.5 times.
"""

import statistics

import json
import logging
from datetime import datetime

import warehouse

logger = logging.getLogger("briefing")
UNUSUAL_FACTOR = 10

# (asset class, symbol, label); gold per pawn is worked out from gold and USD/LKR
PRICES = [("index", "ASPI", "ASPI (Colombo)"), ("commodity", "GC=F", "Gold, USD / oz"),
          ("fx", "LKR=X", "USD / LKR"), ("commodity_lkr", "GOLD24K_LKR_PAWN", "Gold 24K, per pawn"),
          ("index", "^GSPC", "S&P 500"), ("commodity", "BZ=F", "Brent oil")]
DERIVED_FROM = {"GOLD24K_LKR_PAWN": ["LKR=X", "GC=F"]}


def _open(name: str):
    if name in warehouse.PG_TOOLS:                     # moved to PostgreSQL: read with the read-only login
        from warehouse.postgres import DatabaseError, connect
        try:
            con = connect("reader", autocommit=True)
        except DatabaseError as e:
            logger.info("%s: %s; that part is left out", name, e)
            return None
        con.execute("set timezone = 'UTC'")
        return con
    path = warehouse.DB_FILES[name]
    if not path.exists():
        logger.info("%s: no database yet (%s); that part is left out", name, path)
        return None
    con = warehouse.connect(path, read_only=True, wait_seconds=30)
    con.execute("set TimeZone = 'UTC'")
    return con


def unusual(change_pct: float | None, daily: list) -> bool:
    """Is today's move more than UNUSUAL_FACTOR times the typical (median) daily move in the history?

    Needs at least 20 days of history; the last close is left out (it may be today's)."""
    closes = [c for _, c in daily if c]
    moves = [abs(b / a - 1) * 100 for a, b in zip(closes, closes[1:]) if a][:-1]
    if change_pct is None or len(moves) < 20:
        return False
    typical = statistics.median(moves)
    return typical > 0 and abs(change_pct) > UNUSUAL_FACTOR * typical


def _markets(con) -> dict:
    out = {}
    for cls, sym, label in PRICES:
        row = con.execute("select price, change_pct, currency, extra->'daily' from markets.v_latest "
                          "where asset_class = %s and symbol = %s", [cls, sym]).fetchone()
        if not row or row[0] is None:
            continue
        daily = (json.loads(row[3]) if isinstance(row[3], str) else row[3]) or []
        out[sym] = {"label": label, "price": row[0], "change_pct": row[1], "currency": row[2],
                    "held_back": unusual(row[1], daily),
                    "three_months": [[d, c] for d, c in daily[-66:] if c is not None]}
    for sym, sources in DERIVED_FROM.items():               # a price worked out from a held-back one is held too
        if sym in out and any(out.get(s, {}).get("held_back") for s in sources):
            out[sym]["held_back"] = True
    for sym in out:
        series = out[sym]["three_months"]
        out[sym]["change_3m"] = (series[-1][1] / series[0][1] - 1) * 100 if len(series) >= 20 else None
    return out


def _news(con) -> dict:
    n, sources = con.execute("""select count(*), count(distinct source) from news.news_items
        where first_seen_at >= now() - interval '7 days'""").fetchone()
    return {"week_headlines": n, "week_sources": sources}


def _tech(con) -> dict:
    by_cat = con.execute("""select q.category, count(*) from v_qualifications q join v_registrations r using (domain)
        where q.status = 'qualified' and r.registered_at >= now() - interval 7 day
        group by 1 order by 2 desc, 1""").fetchall()
    found = con.execute("select count(*) from v_candidates where discovered_at >= now() - interval 1 day").fetchone()[0]
    return {"launches_by_category": [[c or "other", n] for c, n in by_cat],
            "launches": sum(n for _, n in by_cat), "found_24h": found}


def _vehicles(con) -> dict:
    # The make is the first word of the title; prices under Rs 50,000 are rents or monthly payments
    makes = con.execute("""select lower(split_part(trim(title), ' ', 1)) as make, count(*),
            percentile_cont(0.5) within group (order by price_lkr) filter (where price_lkr >= 50000)
        from vehicles.v_vehicle_ads_latest where title is not null group by 1 order by 2 desc, 1 limit 4""").fetchall()
    ads = con.execute("select count(*) from vehicles.v_vehicle_ads_latest").fetchone()[0]
    return {"ads": ads, "makes": [[m.capitalize() if len(m) > 3 else m.upper(), n, p] for m, n, p in makes]}


def collect(now: datetime | None = None) -> dict:
    """All facts for today. A part whose database is missing is None, and is left out of the posts."""
    now = now or datetime.now().astimezone()
    facts = {"date": now.strftime("%Y-%m-%d"), "date_label": now.strftime("%a %d %b %Y")}
    for name, fn in (("markets", _markets), ("news", _news), ("tech", _tech), ("vehicles", _vehicles)):
        con = _open(name)
        try:
            facts[name] = fn(con) if con else None
        except Exception as e:  # noqa: BLE001  one empty or changed database must not stop the briefing
            logger.warning("%s: left out (%s)", name, e)
            facts[name] = None
        finally:
            if con:
                con.close()
    return facts
