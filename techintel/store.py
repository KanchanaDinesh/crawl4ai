"""Storage for pipeline state.

TechStore (default) keeps every table in PostgreSQL, schema `tech` (database crawl4ai; moved from
data/techintel/techintel.duckdb on 2026-11-06):

    candidates       discover: one row per newly found domain
    qualifications   qualify:  one row per check (re-checks add a new row)
    registrations    enrich:   domain registration date / registrar (RDAP)
    profiles         profile/monitor: one snapshot per crawl (history kept)
    changes          monitor:  one row per detected change
    companies        company:  what each company publishes about itself (social links, people, employees)
    runs             every stage: one row per run (counts, errors), for health checks

Each row keeps the full record as JSON (so the stages stay flexible), and typed views
(v_candidates, v_qualifications, v_registrations, v_profiles, v_changes, v_companies) make it easy to
query with SQL. Page text stays as files under raw/pages/<domain>/.

FileStore is the original JSON Lines version, with the same methods. Existing .jsonl
files are imported automatically the first time TechStore opens the folder.

JSON records come back as dicts with their keys in PostgreSQL's jsonb order (not the order they were written in).
"""

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

TABLES = ("candidates", "qualifications", "registrations", "profiles", "changes", "companies", "runs")


class FileStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, table: str) -> Path:
        if table not in TABLES:
            raise ValueError(f"Unknown table {table!r}; expected one of {TABLES}")
        return self.root / f"{table}.jsonl"

    def append(self, table: str, records: Iterable[dict]) -> int:
        count = 0
        with open(self._path(table), "a", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                count += 1
        return count

    def read(self, table: str) -> Iterator[dict]:
        path = self._path(table)
        if not path.exists():
            return
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def latest(self, table: str, key: str = "domain") -> dict[str, dict]:
        """Latest row per key (rows are appended in time order)."""
        return {row[key]: row for row in self.read(table) if row.get(key)}

    def keys(self, table: str, key: str = "domain") -> set[str]:
        return {row[key] for row in self.read(table) if row.get(key)}

    def write_raw_page(self, domain: str, url: str, text: str) -> str:
        """Save page text; returns its path relative to the store root."""
        safe_domain = re.sub(r"[^\w.-]+", "_", domain)
        digest = hashlib.sha1(url.encode()).hexdigest()[:12]
        path = self.root / "raw" / "pages" / safe_domain / f"{digest}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"<!-- {url} -->\n{text}", encoding="utf-8")
        return str(path.relative_to(self.root))


class TechStore:
    """Same methods as FileStore, backed by PostgreSQL (schema `tech`; tables and typed views in
    warehouse/schema/tech.sql). Each append is one short transaction, so the dashboard reads while stages run."""

    def __init__(self, root: Path):
        from warehouse import postgres
        self.root = Path(root)                  # raw page text and old JSON Lines files live here
        self.root.mkdir(parents=True, exist_ok=True)
        self.con = postgres.connect("writer", autocommit=True)
        self.con.execute("set timezone = 'UTC'")
        postgres.migrate(self.con, ("tech",))
        self._import_jsonl()

    @staticmethod
    def _table(table: str) -> str:
        if table not in TABLES:
            raise ValueError(f"Unknown table {table!r}; expected one of {TABLES}")
        return f"tech.{table}"

    def _import_jsonl(self) -> None:
        """One-time import of the old JSON Lines files (renamed to *.jsonl.imported afterwards)."""
        for table in TABLES:
            path = self.root / f"{table}.jsonl"
            if not path.exists():
                continue
            if self.con.execute(f"select count(*) from {self._table(table)}").fetchone()[0] == 0:
                with open(path, encoding="utf-8") as f:
                    self.append(table, (json.loads(line) for line in f if line.strip()))
            path.rename(path.with_suffix(".jsonl.imported"))

    @staticmethod
    def _key(record: dict) -> str | None:
        return record.get("domain") or record.get("stage")

    def append(self, table: str, records: Iterable[dict]) -> int:
        from warehouse.postgres import clean_text, json_text
        target = self._table(table)
        rows = [(clean_text(self._key(r)), json_text(r)) for r in records]
        if rows:
            with self.con.transaction():
                self.con.cursor().executemany(f"insert into {target} (key, data) values (%s, %s)", rows)
        return len(rows)

    def read(self, table: str) -> Iterator[dict]:
        for (data,) in self.con.execute(f"select data from {self._table(table)} order by row_id").fetchall():
            yield data

    def latest(self, table: str, key: str = "domain") -> dict[str, dict]:
        if key not in ("domain", "stage"):
            return {row[key]: row for row in self.read(table) if row.get(key)}
        rows = self.con.execute(f"""select key, data from (
                select distinct on (key) key, data, row_id from {self._table(table)} where key is not null
                order by key, row_id desc) latest
            order by row_id""").fetchall()
        return {k: d for k, d in rows}

    def keys(self, table: str, key: str = "domain") -> set[str]:
        if key not in ("domain", "stage"):
            return {row[key] for row in self.read(table) if row.get(key)}
        return {k for (k,) in self.con.execute(
            f"select distinct key from {self._table(table)} where key is not null").fetchall()}

    def remove_where(self, table: str, condition: str) -> int:
        """Delete rows matching a SQL condition on `data`. Only for undoing rows written by mistake;
        `condition` comes from our own code, never from outside."""
        target = self._table(table)
        with self.con.transaction():
            return self.con.execute(f"delete from {target} where {condition}").rowcount

    write_raw_page = FileStore.write_raw_page

    def close(self) -> None:
        self.con.close()


def open_store(root: Path) -> TechStore:
    return TechStore(root)
