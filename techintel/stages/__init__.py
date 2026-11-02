"""The pipeline stages, in order: discover -> qualify -> enrich -> profile -> monitor -> analyze -> report.

Each stage module has `async def run(settings, store, **options) -> dict` that
reads what earlier stages stored, appends its own results, and returns a
summary (which the CLI also records in the `runs` table).
"""

from datetime import datetime, timezone

STAGE_ORDER = ("discover", "qualify", "enrich", "profile", "monitor", "analyze", "report")


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


# ── running many sites with progress, saving as we go ─────────────────────
import asyncio  # noqa: E402
import logging  # noqa: E402
import time  # noqa: E402

_log = logging.getLogger("techintel")


def _fmt_secs(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


async def process_items(items: list, worker, *, stage: str, describe, on_result=None,
                        concurrency: int = 4, max_minutes: float | None = None, heartbeat: float = 20) -> dict:
    """Run `worker(item)` for every item, at most `concurrency` at a time.

    - logs each result as it finishes:  "qualify  12/30  example.ai  qualified …  (eta 1m20s)"
    - logs a "still working" line every `heartbeat` seconds
    - calls `on_result(result)` straight away, so results are saved one by one (Ctrl+C keeps them)
    - after `max_minutes`, starts nothing new; the rest stays due and is picked up by the next run
    Returns {"results": [...], "skipped": n, "seconds": s}.
    """
    total = len(items)
    started = time.monotonic()
    deadline = started + max_minutes * 60 if max_minutes else None
    sem = asyncio.Semaphore(max(1, concurrency))
    results: list = []
    state = {"done": 0, "active": 0, "skipped": 0}

    async def run_one(item):
        async with sem:
            if deadline and time.monotonic() > deadline:
                state["skipped"] += 1
                return
            state["active"] += 1
            try:
                result = await worker(item)
            finally:
                state["active"] -= 1
        state["done"] += 1
        results.append(result)
        if on_result:
            on_result(result)
        elapsed = time.monotonic() - started
        eta = elapsed / state["done"] * (total - state["done"] - state["skipped"])
        _log.info("%-8s %*d/%d  %s  (eta %s)", stage, len(str(total)), state["done"], total, describe(result), _fmt_secs(eta))

    async def beat():
        while True:
            await asyncio.sleep(heartbeat)
            _log.info("%-8s … still working: %d/%d done, %d in progress, %s elapsed", stage, state["done"], total,
                      state["active"], _fmt_secs(time.monotonic() - started))

    _log.info("%-8s %d site(s) to process, %d at a time%s", stage, total, concurrency,
              f", time limit {max_minutes:g} min" if max_minutes else "")
    ticker = asyncio.create_task(beat())
    try:
        await asyncio.gather(*(run_one(i) for i in items))
    finally:
        ticker.cancel()
    if state["skipped"]:
        _log.warning("%-8s time limit reached: %d site(s) left for the next run", stage, state["skipped"])
    return {"results": results, "skipped": state["skipped"], "seconds": round(time.monotonic() - started, 1)}
