"""A full copy of the database: `uv run python -m warehouse backup [--keep 7]` (run_all.sh step `backup`).

Uses PostgreSQL's own pg_dump (custom format: compressed, and pg_restore can bring back everything or a single
table). Files go to data/backups/crawl4ai-<YYYYMMDD-HHMMSS>.dump on the project drive; only the newest `keep`
are kept. A dump is written under a temporary name and renamed when it's complete, so a half-written file
(drive full, Ctrl+C) never counts as a backup and never pushes a good one out.

It reads with the writer login (the owner of every table and sequence; the read-only login can't read
sequences), takes a consistent snapshot and doesn't block the tools while it runs.

Restore (into the same database; it replaces what's there):
    pg_restore --clean --if-exists -d crawl4ai data/backups/crawl4ai-20261106-030000.dump
"""

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import project_paths

from . import postgres

PREFIX = "crawl4ai-"
SUFFIX = ".dump"


class BackupError(RuntimeError):
    """A problem with a message that says what to do next."""


def backup_dir() -> Path:
    return project_paths.DATA_DIR / "backups"


def find_pg_dump() -> str:
    """pg_dump on the PATH, else next to pg_config, else Homebrew's (launchd jobs have a short PATH)."""
    found = shutil.which("pg_dump")
    if found:
        return found
    candidates = []
    pg_config = shutil.which("pg_config")
    if pg_config:
        try:
            bindir = subprocess.run([pg_config, "--bindir"], capture_output=True, text=True, check=True).stdout.strip()
            candidates.append(Path(bindir) / "pg_dump")
        except (OSError, subprocess.CalledProcessError):
            pass
    candidates += sorted(Path("/opt/homebrew/opt").glob("postgresql@*/bin/pg_dump"), reverse=True)
    candidates += sorted(Path("/usr/lib/postgresql").glob("*/bin/pg_dump"), reverse=True)
    for path in candidates:
        if path.exists():
            return str(path)
    raise BackupError("pg_dump isn't installed. On the Mac it comes with PostgreSQL:  brew install postgresql@18")


def backups(folder: Path) -> list[Path]:
    """Finished backups, newest first (the names sort by time)."""
    return sorted((p for p in folder.glob(f"{PREFIX}*{SUFFIX}") if p.is_file()), reverse=True)


def backup(folder: Path | None = None, keep: int = 7, now: datetime | None = None) -> tuple[Path, list[Path]]:
    """Write one backup; then delete all but the newest `keep`. Returns (the new file, the files removed)."""
    folder = Path(folder or backup_dir())
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    final = folder / f"{PREFIX}{stamp}{SUFFIX}"
    partial = folder / f".{final.name}.partial"
    run = subprocess.run([find_pg_dump(), "--format=custom", "--no-password", f"--file={partial}",
                          f"--dbname={postgres.url('writer')}"], capture_output=True, text=True)
    if run.returncode != 0 or not partial.exists() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        detail = (run.stderr or run.stdout or "no output").strip().splitlines()
        hint = ""
        if any("No space left" in line for line in detail):
            hint = " The drive is full: free some space, or use --dir to write somewhere else."
        elif any("version mismatch" in line for line in detail):
            hint = " pg_dump must be as new as the server: brew upgrade postgresql@18"
        raise BackupError(f"pg_dump said: {detail[-1] if detail else 'nothing'}.{hint}")
    partial.rename(final)
    removed = backups(folder)[max(keep, 1):]
    for old in removed:
        old.unlink()
    return final, removed
