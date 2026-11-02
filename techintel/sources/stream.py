"""Domains queued by the live listener (`python -m techintel watch`).

The listener never touches the database (DuckDB allows one writer at a time). It appends each
new domain as a JSON line to data/techintel/stream/pending.jsonl. This source takes that file
over atomically (rename), turns the lines into candidates, and keeps the taken file for two days
in stream/taken/ before deleting it.
"""

import json
import os
import time
from pathlib import Path

from ..models import Candidate


def stream_dir(settings) -> Path:
    return settings.data_dir / "stream"


def take_pending(folder: Path) -> list[dict]:
    pending = folder / "pending.jsonl"
    if not pending.exists():
        return []
    taken_dir = folder / "taken"
    taken_dir.mkdir(parents=True, exist_ok=True)
    taken = taken_dir / f"taken-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.jsonl"
    os.replace(pending, taken)                 # atomic: the listener starts a fresh pending file
    rows = []
    for line in taken.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue                           # a half-written last line
    cutoff = time.time() - 2 * 86400
    for old in taken_dir.glob("taken-*.jsonl"):
        if old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)
    return rows


async def collect(ctx) -> list[Candidate]:
    rows = take_pending(stream_dir(ctx.settings))
    seen: dict[str, Candidate] = {}
    for r in rows:
        if r.get("domain") and r["domain"] not in seen:
            seen[r["domain"]] = Candidate(domain=r["domain"], url=r.get("url") or f"https://{r['domain']}/",
                                          source=r.get("source", "certstream"), signal_at=r.get("signal_at"),
                                          detail=r.get("detail") or {})
    return list(seen.values())
