"""Markets database: data/markets/markets.duckdb.

    quotes        one row per instrument per run: price, change, %, volume, market cap, rank, ... (history)
    runs          one row per run: when, how many quotes, errors
    source_runs   one row per data source per run: ok?, how many, error, seconds
    v_latest      the latest quote of every instrument
    v_daily       the last quote of each day per instrument (for charts over weeks/months)
"""

import json
from pathlib import Path

SCHEMA = """
create table if not exists quotes (
    run_at timestamptz, asset_class varchar, symbol varchar, name varchar, price double, prev_close double,
    change double, change_pct double, currency varchar, volume double, market_cap double, rank integer,
    high double, low double, as_of timestamptz, source varchar, extra json);
create table if not exists runs (run_at timestamptz primary key, finished_at timestamptz, quotes integer, errors json);
create table if not exists source_runs (run_at timestamptz, source varchar, grp varchar, ok boolean, count integer,
    error varchar, seconds double);
create or replace view v_latest as
    select * from quotes qualify row_number() over (partition by asset_class, symbol order by run_at desc) = 1;
create or replace view v_daily as
    select date_trunc('day', run_at) as day, * exclude (run_at), run_at from quotes
    qualify row_number() over (partition by asset_class, symbol, date_trunc('day', run_at) order by run_at desc) = 1;
"""

COLUMNS = ("asset_class", "symbol", "name", "price", "prev_close", "change", "change_pct", "currency", "volume",
           "market_cap", "rank", "high", "low", "as_of", "source")


class MarketStore:
    def __init__(self, root: Path):
        from warehouse import connect
        self.root = Path(root)
        self.con = connect(self.root / "markets.duckdb")
        self.con.execute(SCHEMA)

    def save_run(self, run_at: str, finished_at: str, quotes: list[dict], sources: list[dict]) -> None:
        if quotes:     # (DuckDB rejects an empty batch; a run with no prices still records its sources)
            self.con.executemany(
                f"insert into quotes (run_at, {', '.join(COLUMNS)}, extra) values ({', '.join(['?'] * (len(COLUMNS) + 2))})",
                [(run_at, *[q.get(c) for c in COLUMNS], json.dumps(q.get("extra") or {}, default=str)) for q in quotes])
        if sources:
            self.con.executemany("insert into source_runs values (?, ?, ?, ?, ?, ?, ?)",
                                 [(run_at, s["source"], s["group"], s["ok"], s["count"], s.get("error"), s["seconds"])
                                  for s in sources])
        errors = {s["source"]: s["error"] for s in sources if s.get("error")}
        self.con.execute("insert or replace into runs values (?, ?, ?, ?)", [run_at, finished_at, len(quotes), json.dumps(errors)])

    def latest_prices(self) -> dict:
        """{(asset_class, symbol): {"price", "as_of"}} from the last saved run of each instrument."""
        rows = self.con.execute("select asset_class, symbol, price, strftime(as_of, '%Y-%m-%dT%H:%M:%SZ') from v_latest").fetchall()
        return {(c, s): {"price": p, "as_of": a} for c, s, p, a in rows}

    def close(self) -> None:
        self.con.close()
