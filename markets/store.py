"""Markets database: PostgreSQL schema `markets` (database crawl4ai; tables in warehouse/schema/markets.sql).

    quotes        one row per instrument per run: price, change, %, volume, market cap, rank, ... (history)
    runs          one row per run: when, how many quotes, errors
    source_runs   one row per data source per run: ok?, how many, error, seconds
    v_latest      the latest quote of every instrument
    v_daily       the last quote of each day (UTC) per instrument (for charts over weeks/months)

Moved from DuckDB (data/markets/markets.duckdb) on 2026-11-06: the dashboard reads while a run saves. A run is
saved in one short transaction.
"""

from pathlib import Path

COLUMNS = ("asset_class", "symbol", "name", "price", "prev_close", "change", "change_pct", "currency", "volume",
           "market_cap", "rank", "high", "low", "as_of", "source")


class MarketStore:
    def __init__(self, root: Path | None = None):
        """root: kept for the callers' sake (no files are written there now); the data is in PostgreSQL."""
        from warehouse import postgres
        self.root = Path(root) if root else None
        self.con = postgres.connect("writer", autocommit=True)
        self.con.execute("set timezone = 'UTC'")
        postgres.migrate(self.con, ("markets",))
        self._relabel_sl20_total_return()

    def _relabel_sl20_total_return(self) -> None:
        """Until 2026-11-05, "S&P SL20" was saved from the CSE field "spt", which is the S&P SL20 *total return*
        index (about 12,000; the price index is about 6,000). Those rows keep their values under the right name.
        Rows saved since then carry extra.field, so this only ever touches the old ones."""
        self.con.execute("""update markets.quotes set symbol = 'S&P SL20 TRI', name = 'S&P Sri Lanka 20 Total Return'
            where symbol = 'S&P SL20' and source = 'cse' and extra ->> 'field' is null""")

    def save_run(self, run_at: str, finished_at: str, quotes: list[dict], sources: list[dict]) -> None:
        from warehouse.postgres import clean_text as t, json_text
        errors = {s["source"]: s["error"] for s in sources if s.get("error")}
        with self.con.transaction():
            cur = self.con.cursor()
            if quotes:
                cur.executemany(
                    f"insert into markets.quotes (run_at, {', '.join(COLUMNS)}, extra) "
                    f"values ({', '.join(['%s'] * (len(COLUMNS) + 2))})",
                    [(run_at, *[t(q.get(c)) for c in COLUMNS], json_text(q.get("extra") or {})) for q in quotes])
            if sources:
                cur.executemany("insert into markets.source_runs values (%s, %s, %s, %s, %s, %s, %s)",
                                [(run_at, s["source"], s["group"], s["ok"], s["count"], t(s.get("error")), s["seconds"])
                                 for s in sources])
            cur.execute("""insert into markets.runs values (%s, %s, %s, %s)
                           on conflict (run_at) do update set finished_at = excluded.finished_at,
                               quotes = excluded.quotes, errors = excluded.errors""",
                        [run_at, finished_at, len(quotes), json_text(errors)])

    def latest_prices(self) -> dict:
        """{(asset_class, symbol): {"price", "as_of"}} from the last saved run of each instrument."""
        rows = self.con.execute("""select asset_class, symbol, price,
                to_char(as_of at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') from markets.v_latest""").fetchall()
        return {(c, s): {"price": p, "as_of": a} for c, s, p, a in rows}

    def close(self) -> None:
        self.con.close()
