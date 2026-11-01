"""The pipeline stages, in order: discover -> qualify -> profile -> monitor -> analyze -> report.

Each stage module has `async def run(settings, store, **options) -> dict` that
reads what earlier stages stored, appends its own results, and returns a
summary (which the CLI also records in the `runs` table).
"""

from datetime import datetime, timezone

STAGE_ORDER = ("discover", "qualify", "profile", "monitor", "analyze", "report")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def age_days(value: str | None) -> float:
    when = parse_time(value)
    if not when:
        return float("inf")
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400


def latest_ok_profiles(store) -> dict[str, dict]:
    """Latest successful profile per domain (skips snapshots where the site was down)."""
    out: dict[str, dict] = {}
    for row in store.read("profiles"):
        if row.get("status") == "ok":
            out[row["domain"]] = row
    return out
