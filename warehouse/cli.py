"""python -m warehouse <command>

    sql "QUERY"          run SQL on PostgreSQL (read-only login: it can't change anything); without a query:
                         an interactive prompt.  `pg "QUERY"` does the same.
    sql "QUERY" --csv F  save the result as a CSV file
    examples             useful example queries
    info                 every schema with its tables, sizes and row counts
    init                 create every tool's tables (safe to run again)

    setup                create or repair the logins, database, schemas and grants (safe to run again)
    pg-check             check that everything the tools need is there
    migrate              create the tables, indexes and views (warehouse/schema/*.sql; safe to run again)
    copy-from-duckdb     copy the old DuckDB files in, with a check per table (used for the move in 2026-11)
                         [--tool companies] [--replace: copy again] [--check: only compare]

All data is in PostgreSQL, database crawl4ai (docs/postgres-migration.md). Prefix tables with the schema:
news.*  tech.*  markets.*  vehicles.*  companies.*  scrapers.*
e.g.  sql "select * from news.news_items order by published_at desc limit 20"
Or use psql:  psql -U crawl4ai_reader crawl4ai
"""

import argparse
import sys

EXAMPLES = {
    "Latest 20 headlines": "select published_at, source, title from news.news_items order by published_at desc nulls last limit 20",
    "Headlines per country today": "select group_code, count(*) from news.news_items where first_seen_at >= current_date group by 1 order by 2 desc",
    "Tech headlines this week": "select first_seen_at, source, title from news.news_items where 'tech' = any(topics) and first_seen_at >= now() - interval '7 days' order by first_seen_at desc",
    "Source health (last run)": "select source, status, via, headlines, error from news.news_source_runs where run_at = (select max(run_at) from news.news_runs) order by status, source",
    "New tech sites by category": "select category, count(*) from tech.v_qualifications where status = 'qualified' group by 1 order by 2 desc",
    "Most used tech stack": "select t as tech, count(*) from tech.v_profiles, jsonb_array_elements_text(tech_stack) t where jsonb_typeof(tech_stack) = 'array' group by 1 order by 2 desc limit 15",
    "Recent site changes": "select detected_at, domain, change_type, field from tech.v_changes order by detected_at desc limit 20",
    "Companies with LinkedIn, people and GitHub": "select domain, name, linkedin, employees, jsonb_array_length(people) as people, github_repos from tech.v_companies where linkedin is not null order by checked_at desc limit 20",
    "Newest registered domains": "select domain, registered_at from tech.v_registrations where registered_at is not null order by registered_at desc limit 20",
    "Market snapshot (key prices)": "select name, price, currency, round(change_pct::numeric, 2) as pct from markets.v_latest where symbol in ('^GSPC','^IXIC','^DJI','ASPI','S&P SL20','GC=F','SI=F','LKR=X','GOLD24K_LKR_PAWN') or (asset_class = 'crypto' and rank <= 5) order by asset_class, rank",
    "Top CSE gainers today": "select symbol, name, price, round(change_pct::numeric, 2) as pct, volume from markets.v_latest where asset_class = 'stock_lk' and volume > 0 order by change_pct desc nulls last limit 10",
    "Crypto top 20": "select rank, symbol, name, price, round(change_pct::numeric, 2) as pct_24h, market_cap from markets.v_latest where asset_class = 'crypto' order by rank limit 20",
    "Vehicle ads, cheapest first": "select site, title, price_lkr, url from vehicles.v_vehicle_ads_latest where price_lkr is not null order by price_lkr limit 20",
    "Vehicle price changes": "select saved_at, title, previous_price_lkr, price_lkr, url from vehicles.v_vehicle_price_changes order by saved_at desc",
    "Companies per country": "select country, count(*) as companies, count(website) as with_website from companies.companies group by 1 order by 2 desc limit 20",
    "Company websites with LinkedIn": "select name, country, website, linkedin, employees from companies.v_companies where linkedin is not null limit 20",
    "Search companies by name": "select name, country, website, site_status from companies.v_companies where lower(name) like '%bank%' and country = 'LK' order by name limit 20",
    "Scraper results per run": "select scraper, saved_at, count(*) from scrapers.scraper_rows group by 1, 2 order by 2 desc limit 20",
}


def print_examples() -> None:
    for title, sql in EXAMPLES.items():
        print(f"-- {title}\n{sql};\n")


def init() -> None:
    """Create every tool's tables by opening its store (each store runs its schema file)."""
    from companies.store import CompanyStore
    from markets.store import MarketStore
    from techintel.store import TechStore
    from vehicles.store import VehicleStore
    from worldnews.store import NewsStore
    from . import DB_FILES, postgres
    for store in (NewsStore(DB_FILES["news"].parent), TechStore(DB_FILES["tech"].parent), MarketStore(),
                  VehicleStore(), CompanyStore()):
        store.close()
    postgres.migrate(schemas=("scrapers",))
    print("Every tool's tables are there (PostgreSQL schemas: " + ", ".join(postgres.SCHEMAS) + ").")


def _reader():
    from .postgres import DatabaseError, connect
    try:
        return connect("reader", autocommit=True)
    except DatabaseError as e:
        sys.exit(str(e))


def info() -> None:
    from .postgres import SCHEMAS
    with _reader() as con:
        for schema in SCHEMAS:
            size = con.execute("""select pg_size_pretty(coalesce(sum(pg_total_relation_size(c.oid)), 0))
                                  from pg_class c join pg_namespace n on n.oid = c.relnamespace
                                  where n.nspname = %s and c.relkind = 'r'""", [schema]).fetchone()[0]
            print(f"\n{schema}: PostgreSQL schema {schema}  ({size})")
            for table, kind in con.execute("""select table_name, table_type from information_schema.tables
                                              where table_schema = %s order by 2, 1""", [schema]).fetchall():
                count = con.execute(f'select count(*) from {schema}."{table}"').fetchone()[0]
                print(f"  {'view ' if kind == 'VIEW' else 'table'}  {table:<26} {count:>10,} rows")


def _show(con, query: str, csv_path: str | None = None) -> None:
    """Run one query and print it as a table (first 200 rows) or save it as CSV."""
    import csv
    cur = con.execute(query)
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
    """A query as the read-only login (it can't change anything)."""
    import psycopg
    with _reader() as con:
        try:
            _show(con, query, csv_path)
        except psycopg.Error as e:
            sys.exit(f"Error: {str(e).strip()}")


def repl() -> None:
    import psycopg
    con = _reader()
    print("SQL prompt (PostgreSQL, read-only). Schemas: news, tech, markets, vehicles, companies, scrapers.\n"
          "Type 'examples' or 'exit'.")
    with con:
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
                    _show(con, query)
                except psycopg.Error as e:
                    print(f"Error: {str(e).strip()}")


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


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m warehouse", description="The project's PostgreSQL database.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("sql", "pg"):
        q = sub.add_parser(name, help="run SQL (read-only); without a query: interactive prompt")
        q.add_argument("query", nargs="?")
        q.add_argument("--csv", help="Save the result to this CSV file")
    sub.add_parser("examples", help="useful example queries")
    sub.add_parser("info", help="schemas, tables, sizes and row counts")
    sub.add_parser("init", help="create every tool's tables")
    sub.add_parser("setup", help="create or repair the logins, database, schemas and grants")
    sub.add_parser("pg-check", help="check that everything the tools need is there")
    sub.add_parser("migrate", help="create the tables, indexes and views (safe to run again)")
    c = sub.add_parser("copy-from-duckdb", help="copy the old DuckDB files in, with a check per table")
    c.add_argument("--tool", action="append", help="Only this tool (news, tech, markets, vehicles, companies, "
                   "scrapers); repeatable")
    c.add_argument("--replace", action="store_true", help="Copy again: empties the PostgreSQL tables first")
    c.add_argument("--check", action="store_true", help="Only compare DuckDB and PostgreSQL, copy nothing")
    args = p.parse_args(argv)
    if args.command in ("sql", "pg"):
        if args.query:
            run_sql(args.query, args.csv)
        else:
            repl()
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
