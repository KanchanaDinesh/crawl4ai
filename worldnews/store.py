"""Storage: PostgreSQL schema `news` (database crawl4ai; tables in warehouse/schema/news.sql) + a JSON snapshot
file per run.

Tables
    news_items         one row per headline ever seen (unique URL): title, summary, published_at,
                       source, group_code, topics, first_seen_at, last_seen_at, times_seen
    news_runs          one row per run: when, limit, sources OK, headlines, new
    news_source_runs   one row per source per run: status, method used, error, how many headlines
    news_run_items     which headlines each run collected, and their rank on the source
    feed_cache         feed URLs found by auto-discovery, reused on the next run
Files
    runs/<time>.json   full snapshot of each run (raw copy; the report is built from it)

Moved from DuckDB (data/worldnews/worldnews.duckdb) on 2026-11-06, so the dashboard and analysis can read while a
run saves. A run's results are saved in one short transaction.

dry_run (`worldnews check`): reads the history and the feed cache, but saves nothing to the database (its
snapshot file goes to the folder given, e.g. data/worldnews/_check/runs/).

Older versions stored history in items.jsonl and feed_cache.json; they're imported once
automatically and renamed to *.imported.
"""

import json
from pathlib import Path


class NewsStore:
    def __init__(self, root: Path, dry_run: bool = False):
        from warehouse import postgres
        self.root = Path(root)
        self.dry_run = dry_run
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        self.con = postgres.connect("writer", autocommit=True)
        self.con.execute("set timezone = 'UTC'")
        postgres.migrate(self.con, ("news",))
        if not dry_run:
            self._import_old_files()

    def close(self) -> None:
        self.con.close()

    def _import_old_files(self) -> None:
        items = self.root / "items.jsonl"
        if items.exists():
            with open(items, encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
            for row in rows:
                row.setdefault("first_seen_at", row.get("collected_at"))
            self.append_items(rows, times_seen=1)
            items.rename(items.with_suffix(".jsonl.imported"))
        cache = self.root / "feed_cache.json"
        if cache.exists():
            self.save_feed_cache(json.loads(cache.read_text(encoding="utf-8")))
            cache.rename(cache.with_suffix(".json.imported"))

    # history ---------------------------------------------------------------
    def known_urls(self) -> set[str]:
        return {u for (u,) in self.con.execute("select url from news.news_items").fetchall()}

    def append_items(self, items: list[dict], times_seen: int = 0) -> None:
        from warehouse.postgres import clean_text as t
        rows = [(t(i["url"]), t(i.get("id")), t(i.get("title")), t(i.get("summary")), i.get("published_at"),
                 t(i.get("source")), i.get("group"), [t(x) for x in i.get("topics") or []],
                 [t(x) for x in i.get("tech_terms") or []], t(i.get("image")),
                 i.get("first_seen_at") or i.get("collected_at"), i.get("collected_at"), times_seen) for i in items]
        if not rows or self.dry_run:
            return
        with self.con.transaction():
            self.con.cursor().executemany("""insert into news.news_items (url, id, title, summary, published_at, source,
                group_code, topics, tech_terms, image, first_seen_at, last_seen_at, times_seen)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (url) do nothing""", rows)

    # snapshots -------------------------------------------------------------
    def save_snapshot(self, snapshot: dict) -> Path:
        from warehouse.postgres import clean_text as t
        run_at = snapshot["run_at"]
        tot = snapshot["totals"]
        run_items = [(run_at, t(i["url"]), t(i["source"]), i.get("rank")) for s in snapshot["sources"] for i in s["items"]]
        if not self.dry_run:
            con = self.con
            with con.transaction():
                con.execute("""insert into news.news_runs values (%s, %s, %s, %s, %s, %s, %s)
                    on conflict (run_at) do update set finished_at = excluded.finished_at,
                        item_limit = excluded.item_limit, sources = excluded.sources, sources_ok = excluded.sources_ok,
                        headlines = excluded.headlines, new_headlines = excluded.new_headlines""",
                            [run_at, snapshot.get("finished_at"), snapshot.get("limit"), tot["sources"], tot["ok"],
                             tot["headlines"], tot["new"]])
                cur = con.cursor()
                cur.executemany("insert into news.news_source_runs values (%s, %s, %s, %s, %s, %s, %s, %s)", [
                    (run_at, t(s["name"]), s.get("group"), s["status"], s.get("via"), t(s.get("feed_url")),
                     t(s.get("error")), len(s["items"])) for s in snapshot["sources"]])
                if run_items:
                    cur.executemany("insert into news.news_run_items values (%s, %s, %s, %s)", run_items)
                    urls = sorted({u for _, u, _, _ in run_items})
                    con.execute("""update news.news_items set last_seen_at = %s, times_seen = times_seen + 1
                                   where url = any(%s)""", [run_at, urls])
        path = self.root / "runs" / f"{run_at.replace(':', '-')}.json"
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def latest_snapshot(self) -> dict | None:
        runs = sorted((self.root / "runs").glob("*.json"))
        return json.loads(runs[-1].read_text(encoding="utf-8")) if runs else None

    # discovered feeds ------------------------------------------------------
    def feed_cache(self) -> dict[str, str]:
        return dict(self.con.execute("select source, feed_url from news.feed_cache").fetchall())

    def save_feed_cache(self, cache: dict[str, str]) -> None:
        if cache and not self.dry_run:
            with self.con.transaction():
                self.con.cursor().executemany("""insert into news.feed_cache values (%s, %s, now())
                    on conflict (source) do update set feed_url = excluded.feed_url, updated_at = now()""",
                                              list(cache.items()))
