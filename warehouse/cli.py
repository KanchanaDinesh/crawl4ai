"""python -m warehouse <command>

    init                 create the databases (safe to run again)
    info                 where the databases are, their size, tables and row counts
    sql "QUERY"          run SQL across all of them (read-only); without a query: interactive prompt
    sql "QUERY" --csv F  save the result as a CSV file
    examples             useful example queries
    pg "QUERY"           run SQL on PostgreSQL (read-only login): the tools already moved there (companies, news, markets, vehicles)
    pg "QUERY" --csv F   save the result as a CSV file

    setup                PostgreSQL: create or repair the logins, database, schemas and grants (safe to run again)
    pg-check             PostgreSQL: check that everything the tools need is there
    migrate              PostgreSQL: create the tables and indexes (warehouse/schema/*.sql; safe to run again)
    copy-from-duckdb     PostgreSQL: copy the DuckDB data in, with a check per table
                         [--tool companies] [--replace: copy again] [--check: only compare]
                         (PostgreSQL is replacing DuckDB: docs/postgres-migration.md)

In SQL, prefix tables with the database name:  tech.*  scrapers.*
e.g.  select * from tech.v_qualifications limit 20
Moved to PostgreSQL (use `pg` instead of `sql`, or psql crawl4ai):  companies.*  news.*  markets.*  vehicles.*
e.g.  pg "select * from news.news_items order by published_at desc limit 20"
"""

import argparse
import sys

from . import DB_FILES, PG_TOOLS, connect

EXAMPLES = {
    "New tech sites by category": "select category, count(*) from tech.v_qualifications where status = 'qualified' group by 1 order by 2 desc",
    "Most used tech stack": "select t.value::varchar as tech, count(*) from tech.v_profiles, json_each(tech_stack) t group by 1 order by 2 desc limit 15",
    "Recent site changes": "select detected_at, domain, change_type, field from tech.v_changes order by detected_at desc limit 20",
    "Companies with LinkedIn, people and GitHub": "select domain, name, linkedin, employees, people, github_repos from tech.v_companies where linkedin is not null order by checked_at desc limit 20",
    "Newest registered domains": "select domain, registered_at from tech.v_registrations where registered_at is not null order by registered_at desc limit 20",
}

PG_EXAMPLES = {
    "Vehicle ads, cheapest first": "select site, title, price_lkr, url from vehicles.v_vehicle_ads_latest where price_lkr is not null order by price_lkr limit 20",
    "Vehicle price changes": "select saved_at, title, previous_price_lkr, price_lkr, url from vehicles.v_vehicle_price_changes order by saved_at desc",
    "Market snapshot (key prices)": "select name, price, currency, round(change_pct::numeric, 2) as pct from markets.v_latest where symbol in ('^GSPC','^IXIC','^DJI','ASPI','S&P SL20','GC=F','SI=F','LKR=X','GOLD24K_LKR_PAWN') or (asset_class = 'crypto' and rank <= 5) order by asset_class, rank",
    "Top CSE gainers today": "select symbol, name, price, round(change_pct::numeric, 2) as pct, volume from markets.v_latest where asset_class = 'stock_lk' and volume > 0 order by change_pct desc nulls last limit 10",
    "Crypto top 20": "select rank, symbol, name, price, round(change_pct::numeric, 2) as pct_24h, market_cap from markets.v_latest where asset_class = 'crypto' order by rank limit 20",
    "Latest 20 headlines": "select published_at, source, title from news.news_items order by published_at desc nulls last limit 20",
    "Headlines per country today": "select group_code, count(*) from news.news_items where first_seen_at >= current_date group by 1 order by 2 desc",
    "Tech headlines this week": "select first_seen_at, source, title from news.news_items where 'tech' = any(topics) and first_seen_at >= now() - interval '7 days' order by first_seen_at desc",
    "Source health (last run)": "select source, status, via, headlines, error from news.news_source_runs where run_at = (select max(run_at) from news.news_runs) order by status, source",
    "Companies per country": "select country, count(*) as companies, count(website) as with_website from companies.companies group by 1 order by 2 desc limit 20",
    "Company websites with LinkedIn": "select name, country, website, linkedin, employees from companies.v_companies where linkedin is not null limit 20",
    "Search companies by name": "select name, country, website, site_status from companies.v_companies where lower(name) like '%bank%' and country = 'LK' order by name limit 20",
}


def print_examples() -> None:
    for title, sql in EXAMPLES.items():
        print(f"-- {title}\n{sql};\n")
    print("-- PostgreSQL (python -m warehouse pg \"...\"):\n")
    for title, sql in PG_EXAMPLES.items():
        print(f"-- {title}\n{sql};\n")


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
    from vehicles.store import VehicleStore
    VehicleStore().close()                       # PostgreSQL (schema vehicles)
    from companies.store import CompanyStore
    CompanyStore().close()                       # PostgreSQL (schema companies)
    for name, path in DB_FILES.items():
        print(f"{name:<9} {path}")


def open_all():
    import duckdb
    con = duckdb.connect()
    for name, path in DB_FILES.items():
        if name in PG_TOOLS:                       # moved to PostgreSQL: its DuckDB file is only the old copy
            continue
        if path.exists():
            try:
                con.execute(f"attach '{path}' as {name} (read_only)")
            except duckdb.Error as e:
                print(f"(skipping {name}: {e})", file=sys.stderr)
    return con


def info() -> None:
    con = open_all()
    for name, path in DB_FILES.items():
        if name in PG_TOOLS:
            pg_info(name)
            continue
        size = f"{path.stat().st_size / 1e6:.1f} MB" if path.exists() else "not created yet"
        print(f"\n{name}: {path}  ({size})")
        if not path.exists():
            continue
        for (table, kind) in con.execute(
                "select table_name, table_type from information_schema.tables where table_catalog = ? order by 2, 1",
                [name]).fetchall():
            count = con.execute(f'select count(*) from {name}."{table}"').fetchone()[0]
            print(f"  {'view ' if kind == 'VIEW' else 'table'}  {table:<26} {count:>10,} rows")


def pg_info(schema: str) -> None:
    from .postgres import DatabaseError, connect
    try:
        con = connect("reader", autocommit=True)
    except DatabaseError as e:
        print(f"\n{schema}: PostgreSQL, {e}")
        return
    with con:
        size = con.execute("""select pg_size_pretty(coalesce(sum(pg_total_relation_size(c.oid)), 0))
                              from pg_class c join pg_namespace n on n.oid = c.relnamespace
                              where n.nspname = %s and c.relkind = 'r'""", [schema]).fetchone()[0]
        print(f"\n{schema}: PostgreSQL schema {schema}  ({size})")
        for table, kind in con.execute("""select table_name, table_type from information_schema.tables
                                          where table_schema = %s order by 2, 1""", [schema]).fetchall():
            count = con.execute(f'select count(*) from {schema}."{table}"').fetchone()[0]
            print(f"  {'view ' if kind == 'VIEW' else 'table'}  {table:<26} {count:>10,} rows")


def run_pg(query: str, csv_path: str | None = None) -> None:
    """A query on PostgreSQL as the read-only login (it can't change anything), printed as a table or saved as CSV."""
    import csv
    import psycopg
    from .postgres import DatabaseError, connect
    try:
        con = connect("reader", autocommit=True)
    except DatabaseError as e:
        sys.exit(str(e))
    with con:
        try:
            cur = con.execute(query)
        except psycopg.Error as e:
            sys.exit(f"Error: {str(e).strip()}")
        cols = [d.name for d in cur.description or []]
        found = cur.fetchall() if cur.description else []
    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(found)
        print(f"Saved {csv_path} ({len(found):,} rows)")
        return
    shown = [[("" if v is None else str(v))[:60] for v in r] for r in found[:200]]
    widths = [max([len(c)] + [len(r[i]) for r in shown]) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in shown:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)))
    print(f"({len(found):,} rows{', first 200 shown' if len(found) > 200 else ''})")


def run_sql(query: str, csv_path: str | None = None) -> None:
    con = open_all()
    if csv_path:
        con.execute(f"copy ({query}) to '{csv_path}' (header, delimiter ',')")
        print(f"Saved {csv_path}")
    else:
        con.sql(query).show(max_width=200, max_rows=200)


def repl() -> None:
    con = open_all()
    print("SQL prompt (read-only). Tables: tech.*, scrapers.*  (news, markets, vehicles, companies: "
          "python -m warehouse pg)  Type 'examples' or 'exit'.")
    while True:
        try:
            query = input("sql> ").strip().rstrip(";")
        except (EOFError, KeyboardInterrupt):
            break
        if query in ("exit", "quit", "\\q"):
            break
        if query == "examples":
            print_examples()
            continue
        if query:
            try:
                con.sql(query).show(max_width=200, max_rows=200)
            except Exception as e:  # noqa: BLE001
                print(f"Error: {e}")


def pg_setup() -> None:
    from .postgres import DatabaseError, names, setup
    try:
        done = setup()
    except DatabaseError as e:
        sys.exit(f"Setup failed: {e}")
    n = names()
    print("\n".join(f"  {d}" for d in done) if done else "  Everything was already there (grants re-applied).")
    print(f"Database {n['database']}: writer login {n['writer']}, read-only login {n['reader']}.")
    pg_migrate()
    print()
    if not pg_check():
        sys.exit(1)


def pg_migrate() -> None:
    from .postgres import DatabaseError, migrate
    try:
        done = migrate()
    except DatabaseError as e:
        sys.exit(f"Creating the tables failed: {e}")
    print(f"Tables and indexes updated for: {', '.join(done)}." if done else "Tables and indexes are up to date.")


def pg_copy(tools: list[str] | None, replace: bool, check_only: bool) -> bool:
    from .copy_duckdb import copy_all, report
    from .postgres import SCHEMAS, DatabaseError
    unknown = [t for t in tools or [] if t not in SCHEMAS]
    if unknown:
        sys.exit(f"Unknown tool {', '.join(unknown)}. Choose from: {', '.join(SCHEMAS)}")
    try:
        results = copy_all(tools, replace, check_only)
    except DatabaseError as e:
        sys.exit(f"Copy failed: {e}")
    print(report(results, check_only))
    bad = [r.schema for r in results if not r.ok and r.error != "no DuckDB file (nothing to copy)"]
    print(f"\n{'All good.' if not bad else 'Problems in: ' + ', '.join(bad)}")
    return not bad


def pg_check() -> bool:
    """Print the PostgreSQL checks; True when nothing failed."""
    from .postgres import check
    results = check()
    for r in results:
        state = "OK  " if r.ok and not r.warn else ("WARN" if r.ok else "FAIL")
        print(f"[{state}] {r.what}{(': ' + r.detail) if r.detail else ''}")
    return all(r.ok for r in results)


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m warehouse", description="The project's DuckDB databases.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("info")
    sub.add_parser("examples")
    q = sub.add_parser("sql")
    q.add_argument("query", nargs="?")
    q.add_argument("--csv", help="Save the result to this CSV file")
    g = sub.add_parser("pg", help="run SQL on PostgreSQL (read-only): the tools already moved there")
    g.add_argument("query")
    g.add_argument("--csv", help="Save the result to this CSV file")
    sub.add_parser("setup", help="PostgreSQL: create or repair the logins, database, schemas and grants")
    sub.add_parser("pg-check", help="PostgreSQL: check that everything the tools need is there")
    sub.add_parser("migrate", help="PostgreSQL: create the tables and indexes (safe to run again)")
    c = sub.add_parser("copy-from-duckdb", help="PostgreSQL: copy the DuckDB data in, with a check per table")
    c.add_argument("--tool", action="append", help="Only this tool (news, tech, markets, vehicles, companies, "
                   "scrapers); repeatable")
    c.add_argument("--replace", action="store_true", help="Copy again: empties the PostgreSQL tables first")
    c.add_argument("--check", action="store_true", help="Only compare DuckDB and PostgreSQL, copy nothing")
    args = p.parse_args()
    if args.command == "pg":
        run_pg(args.query, args.csv)
    elif args.command == "migrate":
        pg_migrate()
    elif args.command == "copy-from-duckdb":
        sys.exit(0 if pg_copy(args.tool, args.replace, args.check) else 1)
    elif args.command == "setup":
        pg_setup()
    elif args.command == "pg-check":
        sys.exit(0 if pg_check() else 1)
    elif args.command == "init":
        init()
    elif args.command == "info":
        info()
    elif args.command == "examples":
        print_examples()
    elif args.query:
        run_sql(args.query, args.csv)
    else:
        repl()

