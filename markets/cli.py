"""python -m markets <command>

    run      [--only us,lk,metals,fx,crypto]   collect a snapshot of every market and save it
    check    [--only ...]                      same, but only prints what each source returned (nothing saved)
    show     [--class index|stock_us|stock_lk|commodity|commodity_lkr|fx|crypto] [--limit N]
             latest saved prices
    history  SYMBOL [--days 30]                saved prices of one instrument over time

Settings (watchlists, crypto top N): markets/config/settings.json
"""

import argparse
import asyncio
import json
import logging
import os
import sys

from .collect import GROUPS, run_collection
from .store import MarketStore

os.environ.setdefault("CRAWL4AI_NO_CLOUD_NOTICE", "1")
CONFIG = os.path.join(os.path.dirname(__file__), "config", "settings.json")


def data_dir():
    from warehouse import DB_FILES
    return DB_FILES["markets"].parent


def fmt(v, digits=2):
    if v is None:
        return "–"
    if abs(v) >= 1e9:
        return f"{v / 1e9:,.2f}B"
    if abs(v) >= 1e6:
        return f"{v / 1e6:,.2f}M"
    return f"{v:,.{digits if abs(v) < 1e4 else 0}f}" if abs(v) >= 1 else f"{v:.6g}"


def pct(v):
    return "–" if v is None else f"{v:+.2f}%"


async def cmd_run(args, save: bool) -> None:
    settings = json.load(open(args.config or CONFIG))
    only = [g.strip() for g in args.only.split(",")] if args.only else None
    if only and set(only) - set(GROUPS):
        sys.exit(f"--only takes: {', '.join(GROUPS)}")
    store = MarketStore(data_dir())        # PostgreSQL; the prices saved last time fill gaps in this run
    previous = store.latest_prices()
    store.close()
    snap = await run_collection(settings, only, previous)
    ok = sum(s["ok"] for s in snap["sources"])
    failed = [s for s in snap["sources"] if not s["ok"]]
    if save:
        store = MarketStore(data_dir())
        store.save_run(snap["run_at"], snap["finished_at"], snap["quotes"], snap["sources"])
        store.close()
    print(f"\n{len(snap['quotes'])} prices from {ok}/{len(snap['sources'])} sources"
          + ("  (saved)" if save else "  (check only, nothing saved)"))
    via = {}
    for q in snap["quotes"]:
        via[q["source"]] = via.get(q["source"], 0) + 1
    print("Prices by source: " + ", ".join(f"{k} {v}" for k, v in sorted(via.items(), key=lambda x: -x[1])))
    if snap.get("yahoo_backoff"):
        print(f"Note: {snap['yahoo_backoff']}, so Yahoo was skipped this run and backup sources were used.")
    if failed:
        print("No price from any source for:")
        for s in failed:
            print(f"  {s['source']:<24} {s['error'][:150]}")
    if not snap["quotes"]:
        print("\nNo prices at all: check your internet connection (or the errors above).")
        sys.exit(1)
    key = {"^GSPC", "^IXIC", "^DJI", "ASPI", "S&P SL20", "GC=F", "SI=F", "LKR=X", "BTC", "ETH", "GOLD24K_LKR_PAWN"}
    rows = [q for q in snap["quotes"] if q["symbol"] in key and (q["asset_class"] != "crypto" or q["rank"] in (1, 2))]
    if rows:
        print()
        for q in rows:
            print(f"  {q['name'][:34]:<34} {fmt(q['price']):>16} {q.get('currency') or '':<4} {pct(q['change_pct']):>9}")


def _open_read_only():
    """PostgreSQL, read-only login (works while a run is saving)."""
    from warehouse.postgres import DatabaseError, connect
    try:
        con = connect("reader", autocommit=True)
    except DatabaseError as e:
        sys.exit(str(e))
    if not con.execute("select to_regclass('markets.quotes') is not null").fetchone()[0]:
        sys.exit("No prices saved yet. Run:  python -m markets run")
    return con


def cmd_show(args) -> None:
    con = _open_read_only()
    where, params = "", []
    if args.asset_class:
        where, params = "where asset_class = %s", [args.asset_class]
    rows = con.execute(f"""select asset_class, symbol, name, price, currency, change_pct, market_cap, rank
        from markets.v_latest {where} order by asset_class, rank nulls last, market_cap desc nulls last, symbol
        limit {int(args.limit)}""", params).fetchall()
    con.close()
    if not rows:
        print("No prices saved yet. Run:  python -m markets run")
    for cls, sym, name, price, cur, chg, mcap, rank in rows:
        print(f"{cls:<14} {(str(rank) + '.') if rank else '':<4} {sym:<14} {(name or '')[:30]:<30} "
              f"{fmt(price):>14} {cur or '':<4} {pct(chg):>9} {fmt(mcap) if mcap else '':>10}")


def cmd_history(args) -> None:
    con = _open_read_only()
    rows = con.execute(f"""select to_char(run_at, 'YYYY-MM-DD HH24:MI'), price, change_pct, currency
        from markets.quotes where symbol = %s and run_at >= now() - interval '{int(args.days)} days'
        order by run_at""", [args.symbol]).fetchall()
    con.close()
    if not rows:
        print(f"No saved prices for {args.symbol}. Symbols look like ^GSPC, AAPL, ASPI, JKH.N0000, BTC, GC=F, LKR=X")
    for when, price, chg, cur in rows:
        print(f"{when}  {fmt(price):>16} {cur or ''}  {pct(chg)}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m markets", description="Stocks, indices, metals, FX and crypto prices.")
    p.add_argument("--config", help="A different settings.json")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("run", "check"):
        s = sub.add_parser(name)
        s.add_argument("--only", help=f"Comma-separated groups: {', '.join(GROUPS)}")
    s = sub.add_parser("show")
    s.add_argument("--class", dest="asset_class")
    s.add_argument("--limit", type=int, default=60)
    h = sub.add_parser("history")
    h.add_argument("symbol")
    h.add_argument("--days", type=int, default=30)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    try:
        if args.command in ("run", "check"):
            asyncio.run(cmd_run(args, save=args.command == "run"))
        elif args.command == "show":
            cmd_show(args)
        else:
            cmd_history(args)
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(130)
