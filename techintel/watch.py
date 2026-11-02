"""Live listener: `python -m techintel watch`.

Listens to the Certificate Transparency stream (CertStream protocol) without stopping and queues
every interesting new root domain in data/techintel/stream/pending.jsonl. The frequent
`python -m techintel quick` run (e.g. every 15 minutes) adds the queue to the database and
qualifies the new domains. The listener itself never opens the database.

- Domains already queued or already in the database (checked once at start, read-only) are skipped.
- On a dropped connection it reconnects, waiting longer after repeated failures (5 s ... 10 min).
- Every batch (default 5 minutes) it logs one line and updates stream/status.json.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import aiohttp

from .domains import DomainFilter
from .sources.certstream import parse_message
from .sources.stream import stream_dir

logger = logging.getLogger("techintel.watch")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def known_domains(settings) -> set[str]:
    """Domains already in the database (read-only, so it never blocks other jobs)."""
    from warehouse import connect
    db = settings.data_dir / "techintel.duckdb"
    if not db.exists():
        return set()
    try:
        con = connect(db, read_only=True, wait_seconds=30)
    except Exception as e:  # noqa: BLE001  busy: start with an empty set, quick-run dedup still applies
        logger.warning("could not read known domains (%s); continuing", e)
        return set()
    try:
        return {k for (k,) in con.execute("select distinct key from candidates where key is not null").fetchall()}
    finally:
        con.close()


async def listen_batch(url: str, minutes: float, domain_filter, seen: set[str], out_path) -> dict:
    """Listen for `minutes`, append new domains to out_path. Returns stats; raises on connection failure."""
    stats = {"certificates": 0, "queued": 0}
    deadline = time.monotonic() + minutes * 60

    async with aiohttp.ClientSession(trust_env=True) as session:
        async with session.ws_connect(url, heartbeat=30, timeout=aiohttp.ClientWSTimeout(ws_close=10)) as ws:
            with open(out_path, "a", encoding="utf-8") as out:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                        raise ConnectionError("stream closed by the server")
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    stats["certificates"] += 1
                    try:
                        data = json.loads(msg.data)
                    except ValueError:
                        continue
                    for cand in parse_message(data, domain_filter):
                        if cand.domain in seen:
                            continue
                        seen.add(cand.domain)
                        out.write(json.dumps(cand.to_dict(), ensure_ascii=False) + "\n")
                        out.flush()
                        stats["queued"] += 1
    return stats


async def watch(settings, batch_minutes: float = 5, batches: int | None = None, retry_delay: float = 5) -> None:
    config = settings.source("certstream")
    url = config.get("url", "wss://certstream.calidog.io/")
    folder = stream_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    domain_filter = DomainFilter(settings.section("domain_filter"))
    seen = known_domains(settings)
    logger.info("watch: listening to %s, batches of %g min; %d known domains skipped", url, batch_minutes, len(seen))
    status = {"url": url, "started_at": _now(), "batches": 0, "certificates": 0, "queued": 0, "errors": 0}
    delay, n = retry_delay, 0
    while batches is None or n < batches:
        n += 1
        t0 = time.monotonic()
        try:
            stats = await listen_batch(url, batch_minutes, domain_filter, seen, folder / "pending.jsonl")
            delay = retry_delay
            status.update(batches=status["batches"] + 1, certificates=status["certificates"] + stats["certificates"],
                          queued=status["queued"] + stats["queued"], last_batch_at=_now(), last_error=None)
            logger.info("watch: batch %d: %d certificates, %d new domains queued (total queued %d)",
                        n, stats["certificates"], stats["queued"], status["queued"])
        except (aiohttp.ClientError, ConnectionError, OSError, asyncio.TimeoutError) as e:
            status.update(errors=status["errors"] + 1, last_error=f"{_now()} {e}"[:300])
            logger.warning("watch: connection problem (%s); reconnecting in %ds", e, delay)
            (folder / "status.json").write_text(json.dumps(status, indent=1))
            if batches is None or n < batches:
                await asyncio.sleep(delay)
            delay = min(delay * 2, 600)
            continue
        finally:
            status["seconds_last_batch"] = round(time.monotonic() - t0, 1)
        (folder / "status.json").write_text(json.dumps(status, indent=1))
