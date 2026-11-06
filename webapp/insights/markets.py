"""Markets tab: prices, how they moved, and a chart comparing any of them."""

from django.shortcuts import render

from markets.indicators import MIN_CLOSES

from . import charts, queries
from .common import Reader, list_response, may_open, page, tab_required, text_param, url_with
from .templatetags.insights_ui import ago, when

CLASSES = (("index", "Indices"), ("stock_us", "US stocks"), ("stock_lk", "Sri Lanka (CSE)"), ("crypto", "Crypto top 100"),
           ("metals", "Gold, silver & oil"), ("fx", "Exchange rates"))
STOCH_COLUMNS = (("stoch_rsi", "Stoch RSI"), ("pct_k", "%K"), ("pct_d", "%D"), ("last_day", "Last close"))
STOCH_CLASSES = {"crypto": "Powered by CoinPaprika, the price at 00:00 UTC each day", "stock_lk": "From cse.lk, one close per day the share traded"}
COLUMNS = {"crypto": (("rank", "#"), ("name", "Coin"), ("price", "Price (USD)"), ("pct_1h", "1h"), ("change_pct", "24h"), ("pct_7d", "7d"), *STOCH_COLUMNS, ("market_cap", "Market cap"), ("volume", "Volume 24h")),
           "stock_lk": (("symbol", "Symbol"), ("name", "Company"), ("price", "Price (LKR)"), ("change", "Change"), ("change_pct", "%"), *STOCH_COLUMNS, ("volume", "Volume"), ("turnover", "Turnover"), ("market_cap", "Market cap")),
           "stock_us": (("symbol", "Symbol"), ("name", "Name"), ("price", "Price"), ("change", "Change"), ("change_pct", "%"), ("volume", "Volume"), ("as_of", "As of")),
           "default": (("name", "Name"), ("symbol", "Symbol"), ("price", "Price"), ("currency", ""), ("change", "Change"), ("change_pct", "%"), ("as_of", "As of"))}
NUMERIC = {"price", "change", "change_pct", "pct_1h", "pct_7d", "volume", "market_cap", "turnover", "rank", "stoch_rsi", "pct_k", "pct_d"}
TEXT_FIRST = {"name", "symbol", "currency"}             # the first click on these sorts A to Z; on numbers, biggest first
SHORT_NAMES = {"^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^DJI": "Dow", "^NDX": "Nasdaq 100", "^RUT": "Russell 2000", "^VIX": "VIX", "GC=F": "Gold", "SI=F": "Silver",
               "CL=F": "WTI oil", "BZ=F": "Brent oil", "PL=F": "Platinum", "LKR=X": "USD/LKR", "GOLD24K_LKR_PAWN": "Gold pawn (LKR)", "ASPI": "ASPI",
               "S&P SL20": "S&P SL20", "DX-Y.NYB": "Dollar index"}
PERF_CLASS = {"index": "Index", "commodity": "Commodity", "commodity_lkr": "In LKR", "fx": "Exchange rate", "crypto": "Crypto"}
PRESETS = (("World indices", ("index:^GSPC", "index:^IXIC", "index:^DJI", "index:ASPI")),
           ("Sri Lanka", ("index:ASPI", "index:S&P SL20", "fx:LKR=X", "commodity_lkr:GOLD24K_LKR_PAWN")),
           ("Gold, silver & oil", ("commodity:GC=F", "commodity:SI=F", "commodity:CL=F", "commodity:BZ=F")),
           ("Safe havens vs risk", ("commodity:GC=F", "index:^GSPC", "crypto:BTC", "index:^VIX")),
           ("Crypto", ("crypto:BTC", "crypto:ETH", "crypto:SOL", "crypto:XRP", "crypto:BNB")))
DEFAULT_COMPARE = ("index:^GSPC", "commodity:GC=F", "crypto:BTC", "index:ASPI")
COMPARE_DAYS = ((7, "1W"), (30, "1M"), (90, "3M"))
SYMBOL_DAYS = ((1, "1D"), (7, "1W"), (30, "1M"), (100, "3M"))
MAX_COMPARE = 8


def key(row: dict) -> str:
    return f"{row['asset_class']}:{row['symbol']}"


def short_name(row: dict) -> str:
    return SHORT_NAMES.get(row["symbol"]) or (row["symbol"] if row["asset_class"] == "crypto" else (row.get("name") or row["symbol"]))


def display_name(row: dict) -> str:
    return row["symbol"] if row.get("asset_class") == "stock_us" and row.get("name") == row.get("symbol") else row.get("name")


# ── the compare chart ───────────────────────────────────────────────────────────────────────────────────────────
def _chosen(request) -> dict[str, int]:
    """What this person is comparing: {item: colour slot}, kept in their session. An item keeps its slot while it stays
    chosen, so removing one line never repaints the others."""
    saved = request.session.get("compare_items")
    if isinstance(saved, dict) and all(isinstance(v, int) for v in saved.values()):
        return saved
    return {k: i for i, k in enumerate(DEFAULT_COMPARE)}


def _with_item(chosen: dict[str, int], item: str) -> dict[str, int]:
    if item in chosen:
        return {k: v for k, v in chosen.items() if k != item}
    if len(chosen) >= MAX_COMPARE:
        return chosen
    free = next(i for i in range(MAX_COMPARE) if i not in chosen.values())
    return {**chosen, item: free}


def _compare_series(data: list[dict], chosen: dict[str, int]) -> list[dict]:
    """Each line as % change from its first price in the period. A line whose history starts later (CSE and crypto
    prices only build up from our own runs) says so in its name."""
    series = []
    for s in data or []:
        points = [p for p in s["points"] if p["price"] and p["price"] > 0]
        if points:
            base = points[0]["price"]
            series.append({"name": short_name(s), "slot": chosen[key(s)], "start": points[0]["t"], "x": [charts.local_time(p["t"]) for p in points],
                           "y": [round((p["price"] / base - 1) * 100, 3) for p in points]})
    if series:
        first = min(s["start"] for s in series)
        for s in series:
            if s["start"][:10] > _two_days_after(first):
                s["name"] += f" (since {when(s['start'])[:10]})"
    return series


def _two_days_after(iso: str) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(iso[:10]) + timedelta(days=2)).isoformat()


def _compare_context(db, perf, chosen: dict[str, int], days: int) -> dict:
    known = {key(r) for r in perf or []}
    if known:
        chosen = {k: v for k, v in chosen.items() if k in known}
    items = [tuple(k.split(":", 1)) for k in chosen]
    data = db.get(queries.markets_compare, items, days, default=[]) if items else []
    figure = charts.lines(_compare_series(data, chosen), height=320, percent=True, empty="Not enough history for this period yet.") if items else None
    picks = [{"key": key(r), "label": short_name(r), "name": r["name"] or r["symbol"], "slot": chosen.get(key(r)), "on": key(r) in chosen} for r in perf or []]
    return {"picks": picks, "figure": figure, "cmp_days": days, "presets": [{"i": i, "name": name} for i, (name, _) in enumerate(PRESETS)],
            "day_choices": COMPARE_DAYS}


@tab_required("markets")
def compare_view(request):
    """The compare panel, after adding or removing a line, a quick pick, or another period."""
    chosen = _chosen(request)
    item, preset = request.GET.get("toggle"), request.GET.get("preset")
    days = int(request.GET.get("days", 30)) if request.GET.get("days", "30").isdigit() else 30
    days = days if days in dict(COMPARE_DAYS) else 30
    with Reader() as db:
        perf = db.get(queries.markets_performance, default=[])
        known = {key(r) for r in perf}
        if item in known:
            chosen = _with_item(chosen, item)
        elif preset and preset.isdigit() and int(preset) < len(PRESETS):
            chosen = {k: i for i, k in enumerate(k for k in PRESETS[int(preset)][1] if k in known)}
        request.session["compare_items"] = chosen
        context = _compare_context(db, perf, chosen, days)
    return render(request, "insights/_mk_compare.html", context)


# ── the prices table ────────────────────────────────────────────────────────────────────────────────────────────
def _price_filters(request) -> dict:
    cls, mode = request.GET.get("cls"), request.GET.get("mode")
    return {"cls": cls if cls in dict(CLASSES) else "index", "q": text_param(request, "q"), "mode": "map" if mode == "map" else ""}


def _sorted(rows: list[dict], sort: str, direction: int) -> list[dict]:
    if not sort:
        return rows
    present = [r for r in rows if r.get(sort) is not None]
    missing = [r for r in rows if r.get(sort) is None]
    present.sort(key=lambda r: r[sort].lower() if isinstance(r[sort], str) else r[sort], reverse=direction < 0)
    return present + missing                             # no value goes last either way


def _map_tiles(rows: list[dict]) -> list[dict]:
    """Bigger company, bigger tile; the colour says how far it moved today."""
    moved = [r for r in rows if r.get("change_pct") is not None]
    top = max((r.get("market_cap") or 0 for r in moved), default=0)
    tiles = []
    for r in sorted(moved, key=lambda x: -(x.get("market_cap") or 0)):
        grow = 1 + 9 * ((r.get("market_cap") or 0) / top) ** 0.5 if top else 1
        strength = min(1, abs(r["change_pct"]) / 4) * 0.75 + 0.2
        tone = f"rgba(19,140,72,{strength:.2f})" if r["change_pct"] > 0 else f"rgba(196,40,36,{strength:.2f})" if r["change_pct"] < 0 else "rgba(120,128,140,.5)"
        tiles.append({**r, "grow": f"{grow:.2f}", "basis": int(70 * grow ** 0.5 + 0.5), "tone": tone, "title": display_name(r) or r["symbol"]})
    return tiles


def _no_history() -> dict:
    """A row that has no Stochastic RSI row yet: nothing has been collected for it, and that's what it says."""
    return {"stoch_rsi": None, "pct_k": None, "pct_d": None, "zone": None, "last_day": None, "closes_used": 0, "closes_needed": MIN_CLOSES,
            "status": "short", "message": f"not enough history yet (0 of {MIN_CLOSES} days)"}


def _with_stoch_rsi(db, cls: str, rows: list[dict]) -> str:
    """Adds the indicator to each price row, and returns the line under the table that says what the numbers are."""
    found = {r["symbol"]: r for r in db.get(queries.markets_stoch_rsi, cls, default=[])}
    for r in rows:
        r.update(found.get(r["symbol"]) or _no_history())
    zones = [r["zone"] for r in rows]
    missing = sum(1 for r in rows if r["status"] != "ok")
    days = [r["last_day"] for r in rows if r.get("last_day")]
    return (f"Stochastic RSI on a 0-100 scale: RSI 14, Stochastic 14, %K 3, %D 3, on daily closes. %K above 80 is marked overbought, below 20 oversold: "
            f"{zones.count('overbought')} overbought, {zones.count('oversold')} oversold, {missing} without a value. Closes up to {max(days) if days else 'none yet'}. "
            f"{STOCH_CLASSES[cls]}. Numbers only, not advice.")


def _prices_context(db, f: dict, sort: str, direction: int) -> dict:
    classes = ("commodity", "commodity_lkr") if f["cls"] == "metals" else (f["cls"],)
    rows = []
    for cls in classes:
        rows += db.get(queries.markets_quotes, cls, f["q"] or None, default=[])
    stoch_note = _with_stoch_rsi(db, f["cls"], rows) if f["cls"] in STOCH_CLASSES else ""
    columns = []
    for k, label in COLUMNS.get(f["cls"], COLUMNS["default"]):
        next_dir = -direction if sort == k else (1 if k in TEXT_FIRST else -1)
        columns.append({"key": k, "label": label, "numeric": k in NUMERIC, "sorted": sort == k,
                        "arrow": ("▲" if direction > 0 else "▼") if sort == k else "",
                        "url": url_with("insights_prices", f, sort=k, dir=next_dir)})
    movers = [r for r in rows if r.get("change_pct") is not None and (r["asset_class"] != "stock_lk" or (r.get("volume") or 0) > 0)]
    movers.sort(key=lambda r: r["change_pct"], reverse=True)
    notes = list(dict.fromkeys(r["note"] for r in rows if r.get("note")))
    note = (f"LKR gold/silver: {notes[0]}." if notes else
            "Gold and silver in USD per troy ounce (COMEX futures from Yahoo, or spot price from Stooq; click a row to see the source)." if f["cls"] == "metals" else "")
    return {"rows": _sorted(rows, sort, direction), "columns": columns, "f": f, "note": note, "title": dict(CLASSES)[f["cls"]],
            "stoch_note": stoch_note, "tiles": _map_tiles(rows) if f["mode"] == "map" else [], "gainers": [r for r in movers if r["change_pct"] > 0][:6],
            "losers": [r for r in reversed(movers) if r["change_pct"] < 0][:6]}


def _sort_args(request) -> tuple[str, int]:
    sort = request.GET.get("sort") or ""
    return (sort if sort in NUMERIC | TEXT_FIRST | {"as_of", "last_day"} else ""), (1 if request.GET.get("dir") == "1" else -1)


@tab_required("markets")
def prices_view(request):
    f = _price_filters(request)
    sort, direction = _sort_args(request)
    with Reader() as db:
        context = _prices_context(db, f, sort, direction)
    context["oob"] = True                                # the title and the movers change with the list
    count = f"{len(context['rows'])} · click a row for its price history · click a column to sort"
    return list_response(request, "insights/_mk_prices.html", context, count, url_with("insights_markets", f))


# ── the whole tab ───────────────────────────────────────────────────────────────────────────────────────────────
def _with_names(rows) -> list[dict]:
    return [{**r, "short": short_name(r), "class_label": PERF_CLASS.get(r["asset_class"], r["asset_class"])} for r in rows]


@tab_required("markets")
def page_view(request):
    f = _price_filters(request)
    sort, direction = _sort_args(request)
    show_news = may_open(request.user, "news")           # the news panels are the news tab's data
    with Reader() as db:
        s = db.get(queries.markets_summary)
        context = {"f": f, "s": s, "show_news": show_news}
        if s:
            perf = db.get(queries.markets_performance, default=[])
            counts = {r["asset_class"]: r["n"] for r in s["counts"]}
            counts["metals"] = counts.get("commodity", 0) + counts.get("commodity_lkr", 0)
            sources = s["sources"]
            context.update(
                cards=s["cards"], perf=_with_names(perf), chips=[{"code": c, "name": label, "n": counts.get(c, 0)} for c, label in CLASSES],
                asof=f"Last update {when(s['last_run']['run_at'])} ({ago(s['last_run']['run_at'])})" if s["last_run"].get("run_at") else "",
                sources=sources, bad_sources=[x for x in sources if not x["ok"]], ok_sources=sum(1 for x in sources if x["ok"]),
                **_compare_context(db, perf, _chosen(request), 30), **_prices_context(db, f, sort, direction))
            context["count"] = f"{len(context['rows'])} · click a row for its price history · click a column to sort"
            if show_news:
                for name, group in (("market_news", "MARKETS"), ("crypto_news", "CRYPTO")):
                    found = db.get(queries.news_items, group=group, days=3, limit=10) or {"items": []}
                    context[name] = found["items"]
    return page(request, "markets", db, **context)


@tab_required("markets")
def symbol_view(request):
    """One instrument's details and price history, in the side panel."""
    cls, symbol = request.GET.get("class") or "", request.GET.get("symbol") or ""
    days = request.GET.get("days", "30")
    days = int(days) if days.isdigit() and int(days) in dict(SYMBOL_DAYS) else 30
    with Reader() as db:
        d = db.get(queries.markets_history, cls, symbol, 100, default={})
        stoch = None
        if cls in STOCH_CLASSES:
            stoch = next(iter(db.get(queries.markets_stoch_rsi, cls, symbol, default=[])), None) or _no_history()
    q = d.get("quote") or {}
    since = charts.last_moment(days)
    points = [p for p in d.get("points") or [] if p["price"] is not None and p["t"] >= since]
    size = max((abs(p["price"]) for p in points), default=q.get("price") or 0)
    value_format = ".4~g" if size < 1 else ",.4f" if cls == "fx" and size < 20 else ",.2f"
    figure = charts.lines([{"name": symbol, "slot": 0, "x": [charts.local_time(p["t"]) for p in points], "y": [p["price"] for p in points]}],
                          height=240, value_format=value_format, empty="Not enough history for this period yet: each run adds a point.")
    stats = None
    values = [p["price"] for p in points]
    if len(values) >= 2 and values[0]:
        stats = {"points": len(values), "high": max(values), "low": min(values), "change": (values[-1] / values[0] - 1) * 100}
    return render(request, "insights/_mk_symbol.html", {
        "q": q, "cls": cls, "symbol": symbol, "name": display_name(q) or symbol, "figure": figure, "stats": stats, "days": days, "stoch": stoch, "stoch_source": STOCH_CLASSES.get(cls),
        "ranges": [{"days": n, "label": label, "on": n == days} for n, label in SYMBOL_DAYS], "banner": db.error,
        "row": {"asset_class": cls, "symbol": symbol}})
