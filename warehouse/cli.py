"""python -m warehouse <command>

    init                 create the three databases (safe to run again)
    info                 where the databases are, their size, tables and row counts
    sql "QUERY"          run SQL across all three (read-only); without a query: interactive prompt
    sql "QUERY" --csv F  save the result as a CSV file
    examples             useful example queries

In SQL, prefix tables with the database name:  news.*  tech.*  scrapers.*  markets.*
e.g.  select * from news.news_items order by published_at desc limit 20
"""

import argparse
import sys

from . import DB_FILES, connect

EXAMPLES = {
    "Latest 20 headlines": "select published_at, source, title from news.news_items order by published_at desc nulls last limit 20",
    "Headlines per country today": "select group_code, count(*) from news.news_items where first_seen_at >= current_date group by 1 order by 2 desc",
    "Tech headlines this week": "select first_seen_at, source, title from news.news_items where list_contains(topics, 'tech') and first_seen_at >= now() - interval 7 day order by first_seen_at desc",
    "Source health (last run)": "select source, status, via, headlines, error from news.news_source_runs where run_at = (select max(run_at) from news.news_runs) order by status, source",
    "New tech sites by category": "select category, count(*) from tech.v_qualifications where status = 'qualified' group by 1 order by 2 desc",
    "Most used tech stack": "select t.value::varchar as tech, count(*) from tech.v_profiles, json_each(tech_stack) t group by 1 order by 2 desc limit 15",
    "Recent site changes": "select detected_at, domain, change_type, field from tech.v_changes order by detected_at desc limit 20",
    "Newest registered domains": "select domain, registered_at from tech.v_registrations where registered_at is not null order by registered_at desc limit 20",
    "Vehicle ads, cheapest first": "select site, title, price_lkr, url from scrapers.v_vehicle_ads_latest where price_lkr is not null order by price_lkr limit 20",
    "Market snapshot (key prices)": "select name, price, currency, round(change_pct, 2) as pct from markets.v_latest where symbol in ('^GSPC','^IXIC','^DJI','ASPI','S&P SL20','GC=F','SI=F','LKR=X','GOLD24K_LKR_PAWN') or (asset_class = 'crypto' and rank <= 5) order by asset_class, rank",
    "Top CSE gainers today": "select symbol, name, price, round(change_pct, 2) as pct, volume from markets.v_latest where asset_class = 'stock_lk' and volume > 0 order by change_pct desc limit 10",
    "Crypto top 20": "select rank, symbol, name, price, round(change_pct, 2) as pct_24h, market_cap from markets.v_latest where asset_class = 'crypto' order by rank limit 20",
    "Vehicle price changes": "select saved_at, title, previous_price_lkr, price_lkr, url from scrapers.v_vehicle_price_changes order by saved_at desc",
}


def init() -> None:
    from techintel.store import DuckStore
    from worldnews.store import NewsStore
    from .scrapers_db import init as init_scrapers
    NewsStore(DB_FILES["news"].parent).close()
    DuckStore(DB_FILES["tech"].parent).close()
    con = connect(DB_FILES["scrapers"])
    init_scrapers(con)
    con.close()
    from markets.store import MarketStore
    MarketStore(DB_FILES["markets"].parent).close()
    for name, path in DB_FILES.items():
        print(f"{name:<9} {path}")


def open_all():
    import duckdb
    con = duckdb.connect()
    for name, path in DB_FILES.items():
        if path.exists():
            try:
                con.execute(f"attach '{path}' as {name} (read_only)")
            except duckdb.Error as e:
                print(f"(skipping {name}: {e})", file=sys.stderr)
    return con


def info() -> None:
    con = open_all()
    for name, path in DB_FILES.items():
        size = f"{path.stat().st_size / 1e6:.1f} MB" if path.exists() else "not created yet"
        print(f"\n{name}: {path}  ({size})")
        if not path.exists():
            continue
        for (table, kind) in con.execute(
                "select table_name, table_type from information_schema.tables where table_catalog = ? order by 2, 1",
                [name]).fetchall():
            count = con.execute(f'select count(*) from {name}."{table}"').fetchone()[0]
            print(f"  {'view ' if kind == 'VIEW' else 'table'}  {table:<26} {count:>10,} rows")


def run_sql(query: str, csv_path: str | None = None) -> None:
    con = open_all()
    if csv_path:
        con.execute(f"copy ({query}) to '{csv_path}' (header, delimiter ',')")
        print(f"Saved {csv_path}")
    else:
        con.sql(query).show(max_width=200, max_rows=200)


def repl() -> None:
    con = open_all()
    print("SQL prompt (read-only). Tables: news.*, tech.*, scrapers.*, markets.*  Type 'examples' or 'exit'.")
    while True:
        try:
            query = input("sql> ").strip().rstrip(";")
        except (EOFError, KeyboardInterrupt):
            break
        if query in ("exit", "quit", "\\q"):
            break
        if query == "examples":
            for title, sql in EXAMPLES.items():
                print(f"-- {title}\n{sql};\n")
            continue
        if query:
            try:
                con.sql(query).show(max_width=200, max_rows=200)
            except Exception as e:  # noqa: BLE001
                print(f"Error: {e}")


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m warehouse", description="The project's DuckDB databases.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("info")
    sub.add_parser("examples")
    q = sub.add_parser("sql")
    q.add_argument("query", nargs="?")
    q.add_argument("--csv", help="Save the result to this CSV file")
    args = p.parse_args()
    if args.command == "init":
        init()
    elif args.command == "info":
        info()
    elif args.command == "examples":
        for title, sql in EXAMPLES.items():
            print(f"-- {title}\n{sql};\n")
    elif args.query:
        run_sql(args.query, args.csv)
    else:
        repl()

