"""Markets database: PostgreSQL schema `markets` (database crawl4ai; tables in warehouse/schema/markets.sql).

    quotes        one row per instrument per run: price, change, %, volume, market cap, rank, ... (history)
    runs          one row per run: when, how many quotes, errors
    source_runs   one row per data source per run: ok?, how many, error, seconds
    v_latest      the latest quote of every instrument
    v_daily       the last quote of each day (UTC) per instrument (for charts over weeks/months)
    daily_closes  one close per day per instrument, from public sources (markets/closes.py: CSE and crypto)
    history_sync  how far each instrument's closes are complete, and its id at the source
    history_refusals  when a source last said no, so we leave it alone for a while
    stoch_rsi     the latest Stochastic RSI per instrument (markets/indicators.py), rewritten after every sync

Moved from DuckDB (data/markets/markets.duckdb) on 2026-11-06: the dashboard reads while a run saves. A run is
saved in one short transaction.
"""

from pathlib import Path

COLUMNS = ("asset_class", "symbol", "name", "price", "prev_close", "change", "change_pct", "currency", "volume",
           "market_cap", "rank", "high", "low", "as_of", "source")


def _rounded(value: float | None) -> float | None:
    """More digits than this only look precise, and fill the grid."""
    return None if value is None else round(value, 2)


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

    # ── daily closes ─────────────────────────────────────────────────────────────────────────────────────────
    def known_symbols(self, asset_class: str) -> list[str]:
        return [r[0] for r in self.con.execute("select symbol from markets.v_latest where asset_class = %s", [asset_class]).fetchall()]

    def known_coins(self) -> list[dict]:
        """The coins we show, biggest first, so a run stopped early has done the ones most people look at."""
        rows = self.con.execute("""select symbol, name, price, extra ->> 'id', rank from markets.v_latest where asset_class = 'crypto'
            order by rank nulls last, symbol""").fetchall()
        return [{"symbol": s, "name": n, "price": p, "gecko_id": g, "rank": r} for s, n, p, g, r in rows]

    def calls_last_hour(self, source: str) -> int:
        """Roughly how many calls this source has had from us in the last hour: one per instrument touched."""
        return self.con.execute("select count(*) from markets.history_sync where source = %s and fetched_at > now() - interval '1 hour'",
                                [source]).fetchone()[0]

    def sync_state(self, asset_class: str) -> dict:
        """{symbol: {"source_id", "synced_day", "last_day", "failed_on"}}. A share that never worked has synced_day None,
        so it's tried again."""
        rows = self.con.execute("""select h.symbol, h.source_id, h.synced_day,
                (select max(c.day) from markets.daily_closes c where c.asset_class = h.asset_class and c.symbol = h.symbol),
                case when h.error is not null then (h.fetched_at at time zone 'UTC')::date end
            from markets.history_sync h where h.asset_class = %s""", [asset_class]).fetchall()
        return {s: {"source_id": i, "synced_day": d, "last_day": last, "failed_on": failed} for s, i, d, last, failed in rows}

    def save_closes(self, asset_class: str, symbol: str, closes: list, source: str, *, source_id: str | None,
                    synced_day, error: str | None) -> None:
        """One instrument's closes and how far they're complete, together: a run stopped between shares loses nothing."""
        with self.con.transaction():
            cur = self.con.cursor()
            if closes:
                cur.executemany("""insert into markets.daily_closes (asset_class, symbol, day, close, source) values (%s, %s, %s, %s, %s)
                    on conflict (asset_class, symbol, day) do update set close = excluded.close, source = excluded.source, fetched_at = now()""",
                                [(asset_class, symbol, day, close, source) for day, close in closes])
            cur.execute("""insert into markets.history_sync (asset_class, symbol, source, source_id, synced_day, error, fetched_at)
                values (%s, %s, %s, %s, %s, %s, now())
                on conflict (asset_class, symbol) do update set source = excluded.source, source_id = excluded.source_id,
                    synced_day = excluded.synced_day, error = excluded.error, fetched_at = now()""",
                        [asset_class, symbol, source, source_id, synced_day, error])

    def save_day_closes(self, asset_class: str, day, closes: dict, source: str) -> None:
        if closes:
            with self.con.transaction():
                self.con.cursor().executemany("""insert into markets.daily_closes (asset_class, symbol, day, close, source) values (%s, %s, %s, %s, %s)
                    on conflict (asset_class, symbol, day) do update set close = excluded.close, source = excluded.source, fetched_at = now()""",
                                              [(asset_class, s, day, c, source) for s, c in closes.items()])

    def mark_synced(self, asset_class: str, symbols: list[str], day, source: str, ids: dict) -> None:
        """These are complete up to `day`, including those that didn't trade that day: no trade means no new close."""
        with self.con.transaction():
            self.con.cursor().executemany("""insert into markets.history_sync (asset_class, symbol, source, source_id, synced_day, fetched_at)
                values (%s, %s, %s, %s, %s, now())
                on conflict (asset_class, symbol) do update set synced_day = excluded.synced_day, error = null, fetched_at = now(),
                    source_id = coalesce(excluded.source_id, markets.history_sync.source_id)""",
                                          [(asset_class, s, source, ids.get(s), day) for s in symbols])

    def note_sync_error(self, asset_class: str, symbol: str, source: str, error: str) -> None:
        """Remembers why, and keeps the id and the day already reached."""
        self.con.execute("""insert into markets.history_sync (asset_class, symbol, source, error, fetched_at) values (%s, %s, %s, %s, now())
            on conflict (asset_class, symbol) do update set error = excluded.error, fetched_at = now()""",
                         [asset_class, symbol, source, error[:300]])

    def note_refusal(self, source: str, reason: str) -> None:
        self.con.execute("""insert into markets.history_refusals (source, refused_at, reason) values (%s, now(), %s)
            on conflict (source) do update set refused_at = now(), reason = excluded.reason""", [source, reason[:300]])

    def refused_recently(self, source: str, minutes: int) -> dict | None:
        row = self.con.execute("""select reason, ceil(extract(epoch from now() - refused_at) / 60)::int from markets.history_refusals
            where source = %s and refused_at > now() - make_interval(mins => %s)""", [source, minutes]).fetchone()
        return {"reason": row[0], "ago": f"{row[1]} min"} if row else None

    def update_stoch_rsi(self) -> dict:
        """Recalculate the latest values from the saved closes and replace the table in one transaction, so a page
        never sees it half done. Every CSE share and coin we show gets a row, also with no history: "0 of 34 days" is
        an answer, a missing row isn't."""
        from markets.indicators import MIN_CLOSES, latest_stoch_rsi
        from warehouse.postgres import clean_text as t
        history: dict[tuple, list] = {}
        for cls, sym, day, close, source in self.con.execute("""select asset_class, symbol, day, close, source from markets.daily_closes
                where asset_class in ('stock_lk', 'crypto') order by asset_class, symbol, day"""):
            history.setdefault((cls, sym), []).append((day, close, source))
        why = dict(((c, s), e) for c, s, e in self.con.execute("select asset_class, symbol, error from markets.history_sync where error is not null").fetchall())
        rows = []
        counts = {"ok": 0, "short": 0, "flat": 0}
        for cls, sym, name in self.con.execute("select asset_class, symbol, name from markets.v_latest where asset_class in ('stock_lk', 'crypto')").fetchall():
            closes = history.get((cls, sym), [])
            v = latest_stoch_rsi([c for _, c, _ in closes])
            last_day, last_close, source = closes[-1] if closes else (None, None, None)
            message = v["message"]
            if message and v["status"] == "short" and why.get((cls, sym)):
                message += f": {why[(cls, sym)]}"
            counts[v["status"]] += 1
            rows.append((cls, sym, t(name), _rounded(v["stoch_rsi"]), _rounded(v["pct_k"]), _rounded(v["pct_d"]), v["zone"], last_close, last_day, v["closes_used"],
                         MIN_CLOSES, v["status"], t(message), source))
        with self.con.transaction():
            cur = self.con.cursor()
            cur.execute("delete from markets.stoch_rsi")
            cur.executemany("""insert into markets.stoch_rsi (asset_class, symbol, name, stoch_rsi, pct_k, pct_d, zone, last_close, last_day,
                closes_used, closes_needed, status, message, source) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", rows)
        return counts

    def close(self) -> None:
        self.con.close()
