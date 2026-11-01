"""Storage for pipeline state.

For now everything is append-only JSON Lines files, one file per table:

    data/techintel/
        candidates.jsonl      discover: one row per newly found domain
        qualifications.jsonl  qualify:  one row per check (re-checks append a new row)
        profiles.jsonl        profile/monitor: one snapshot per crawl (history kept)
        changes.jsonl         monitor:  one row per detected change
        runs.jsonl            every stage: one row per run (counts, errors), for health checks
        raw/pages/<domain>/   page text (Markdown) as crawled, for re-processing later

The "current" state of a domain is its latest row in a table. This mirrors the
raw/clean split we'll use with a database later: swap FileStore for a DuckDB
implementation with the same methods and the stages don't change.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

TABLES = ("candidates", "qualifications", "profiles", "changes", "runs")


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
