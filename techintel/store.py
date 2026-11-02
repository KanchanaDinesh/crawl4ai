"""Storage for pipeline state.

DuckStore (default) keeps every table in one DuckDB file, data/techintel/techintel.duckdb:

    candidates       discover: one row per newly found domain
    qualifications   qualify:  one row per check (re-checks add a new row)
    registrations    enrich:   domain registration date / registrar (RDAP)
    profiles         profile/monitor: one snapshot per crawl (history kept)
    changes          monitor:  one row per detected change
    runs             every stage: one row per run (counts, errors), for health checks

Each row keeps the full record as JSON (so the stages stay flexible), and typed views
(v_candidates, v_qualifications, v_registrations, v_profiles, v_changes) make it easy to
query with SQL. Page text stays as files under raw/pages/<domain>/.

FileStore is the original JSON Lines version, with the same methods. Existing .jsonl
files are imported into DuckDB automatically the first time DuckStore opens the folder.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

TABLES = ("candidates", "qualifications", "registrations", "profiles", "changes", "runs")


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


VIEWS = {
    "v_candidates": """
        select row_id, key as domain, data->>'source' as source, data->>'url' as url,
               try_cast(data->>'discovered_at' as timestamptz) as discovered_at,
               try_cast(data->>'signal_at' as timestamptz) as signal_at, data->'detail' as detail
        from candidates""",
    "v_qualifications": """
        select key as domain, data->>'status' as status, data->>'reason' as reason, data->>'category' as category,
               try_cast(data->>'score' as double) as score, data->>'title' as title, data->>'final_url' as final_url,
               try_cast(data->>'checked_at' as timestamptz) as checked_at
        from qualifications qualify row_number() over (partition by key order by row_id desc) = 1""",
    "v_registrations": """
        select key as domain, try_cast(data->>'registered_at' as timestamptz) as registered_at,
               data->>'registrar' as registrar, data->>'error' as error
        from registrations qualify row_number() over (partition by key order by row_id desc) = 1""",
    "v_profiles": """
        select key as domain, data->>'status' as status, data->>'name' as name, data->>'description' as description,
               data->>'category' as category, data->'tech_stack' as tech_stack, data->'signals' as signals,
               data->'keywords' as keywords, try_cast(data->>'profiled_at' as timestamptz) as profiled_at
        from profiles where data->>'status' = 'ok'
        qualify row_number() over (partition by key order by row_id desc) = 1""",
    "v_changes": """
        select key as domain, data->>'change_type' as change_type, data->>'field' as field,
               data->'before' as before, data->'after' as after,
               try_cast(data->>'detected_at' as timestamptz) as detected_at
        from changes""",
}


class DuckStore:
    """Same methods as FileStore, backed by DuckDB."""

    def __init__(self, root: Path, db_path: Path | None = None):
        from warehouse import connect
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path) if db_path else self.root / "techintel.duckdb"
        self.con = connect(self.db_path)
        self.con.execute("create sequence if not exists row_seq")
        for table in TABLES:
            self.con.execute(f"""create table if not exists {table} (
                row_id bigint primary key default nextval('row_seq'),
                key varchar, created_at timestamp default current_timestamp, data json)""")
        for name, sql in VIEWS.items():
            self.con.execute(f"create or replace view {name} as {sql}")
        self._import_jsonl()

    def _import_jsonl(self) -> None:
        """One-time import of the old JSON Lines files (renamed to *.jsonl.imported afterwards)."""
        for table in TABLES:
            path = self.root / f"{table}.jsonl"
            if not path.exists():
                continue
            if self.con.execute(f"select count(*) from {table}").fetchone()[0] == 0:
                with open(path, encoding="utf-8") as f:
                    self.append(table, (json.loads(line) for line in f if line.strip()))
            path.rename(path.with_suffix(".jsonl.imported"))

    @staticmethod
    def _key(record: dict) -> str | None:
        return record.get("domain") or record.get("stage")

    def append(self, table: str, records: Iterable[dict]) -> int:
        if table not in TABLES:
            raise ValueError(f"Unknown table {table!r}; expected one of {TABLES}")
        rows = [(self._key(r), json.dumps(r, ensure_ascii=False, default=str)) for r in records]
        if rows:
            self.con.executemany(f"insert into {table} (key, data) values (?, ?)", rows)
        return len(rows)

    def read(self, table: str) -> Iterator[dict]:
        for (data,) in self.con.execute(f"select data from {table} order by row_id").fetchall():
            yield json.loads(data)

    def latest(self, table: str, key: str = "domain") -> dict[str, dict]:
        if key not in ("domain", "stage"):
            return {row[key]: row for row in self.read(table) if row.get(key)}
        rows = self.con.execute(f"""select key, data from {table} where key is not null
            qualify row_number() over (partition by key order by row_id desc) = 1 order by row_id""").fetchall()
        return {k: json.loads(d) for k, d in rows}

    def keys(self, table: str, key: str = "domain") -> set[str]:
        if key not in ("domain", "stage"):
            return {row[key] for row in self.read(table) if row.get(key)}
        return {k for (k,) in self.con.execute(f"select distinct key from {table} where key is not null").fetchall()}

    write_raw_page = FileStore.write_raw_page

    def close(self) -> None:
        self.con.close()


def open_store(root: Path) -> DuckStore:
    return DuckStore(root)
