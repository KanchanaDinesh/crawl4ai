"""Storage: DuckDB database data/worldnews/worldnews.duckdb (+ a JSON snapshot file per run).

Tables
    news_items         one row per headline ever seen (unique URL): title, summary, published_at,
                       source, group_code, topics, first_seen_at, last_seen_at, times_seen
    news_runs          one row per run: when, limit, sources OK, headlines, new
    news_source_runs   one row per source per run: status, method used, error, how many headlines
    news_run_items     which headlines each run collected, and their rank on the source
    feed_cache         feed URLs found by auto-discovery, reused on the next run
Files
    runs/<time>.json   full snapshot of each run (raw copy; the report is built from it)

Older versions stored history in items.jsonl and feed_cache.json; they're imported once
automatically and renamed to *.imported.
"""

import json
from pathlib import Path

SCHEMA = """
create table if not exists news_items (
    url varchar primary key, id varchar, title varchar, summary varchar, published_at timestamptz,
    source varchar, group_code varchar, topics varchar[], tech_terms varchar[], image varchar,
    first_seen_at timestamptz, last_seen_at timestamptz, times_seen integer default 0);
create table if not exists news_runs (
    run_at timestamptz primary key, finished_at timestamptz, item_limit integer,
    sources integer, sources_ok integer, headlines integer, new_headlines integer);
create table if not exists news_source_runs (
    run_at timestamptz, source varchar, group_code varchar, status varchar, via varchar,
    feed_url varchar, error varchar, headlines integer);
create table if not exists news_run_items (run_at timestamptz, url varchar, source varchar, rank integer);
create table if not exists feed_cache (source varchar primary key, feed_url varchar, updated_at timestamptz);
"""


class NewsStore:
    def __init__(self, root: Path):
        from warehouse import connect
        self.root = Path(root)
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        self.con = connect(self.root / "worldnews.duckdb")
        self.con.execute(SCHEMA)
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
        return {u for (u,) in self.con.execute("select url from news_items").fetchall()}

    def append_items(self, items: list[dict], times_seen: int = 0) -> None:
        rows = [(i["url"], i.get("id"), i.get("title"), i.get("summary"), i.get("published_at"), i.get("source"),
                 i.get("group"), i.get("topics") or [], i.get("tech_terms") or [], i.get("image"),
                 i.get("first_seen_at") or i.get("collected_at"), i.get("collected_at"), times_seen) for i in items]
        if rows:
            self.con.executemany("""insert into news_items (url, id, title, summary, published_at, source, group_code,
                topics, tech_terms, image, first_seen_at, last_seen_at, times_seen)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) on conflict (url) do nothing""", rows)

    # snapshots -------------------------------------------------------------
    def save_snapshot(self, snapshot: dict) -> Path:
        run_at = snapshot["run_at"]
        t = snapshot["totals"]
        self.con.execute("insert or replace into news_runs values (?, ?, ?, ?, ?, ?, ?)",
                         [run_at, snapshot.get("finished_at"), snapshot.get("limit"), t["sources"], t["ok"],
                          t["headlines"], t["new"]])
        self.con.executemany("insert into news_source_runs values (?, ?, ?, ?, ?, ?, ?, ?)", [
            (run_at, s["name"], s.get("group"), s["status"], s.get("via"), s.get("feed_url"), s.get("error"),
             len(s["items"])) for s in snapshot["sources"]])
        run_items = [(run_at, i["url"], i["source"], i.get("rank")) for s in snapshot["sources"] for i in s["items"]]
        if run_items:
            self.con.executemany("insert into news_run_items values (?, ?, ?, ?)", run_items)
            urls = sorted({u for _, u, _, _ in run_items})
            self.con.execute("""update news_items set last_seen_at = ?, times_seen = times_seen + 1
                                where list_contains(?, url)""", [run_at, urls])
        path = self.root / "runs" / f"{run_at.replace(':', '-')}.json"
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def latest_snapshot(self) -> dict | None:
        runs = sorted((self.root / "runs").glob("*.json"))
        return json.loads(runs[-1].read_text(encoding="utf-8")) if runs else None

    # discovered feeds ------------------------------------------------------
    def feed_cache(self) -> dict[str, str]:
        return dict(self.con.execute("select source, feed_url from feed_cache").fetchall())

    def save_feed_cache(self, cache: dict[str, str]) -> None:
        if cache:
            self.con.executemany("insert or replace into feed_cache values (?, ?, current_timestamp)", list(cache.items()))
