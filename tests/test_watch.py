"""Live listener (techintel watch) against a fake CertStream server, and the quick run picking up its queue."""

import asyncio
import json
import threading

from aiohttp import web

from techintel.cli import run_stage


def cert(*domains):
    return {"message_type": "certificate_update",
            "data": {"seen": 1762041600.0, "source": {"name": "Fake log"},
                     "leaf_cert": {"all_domains": list(domains), "issuer": {"O": "Let's Encrypt"}}}}


class FakeCertStream:
    """WebSocket server sending a few certificate messages to each client, then staying open."""

    def __init__(self, messages):
        self.messages, self.connections = messages, 0
        self.loop = asyncio.new_event_loop()
        started = threading.Event()

        async def handler(request):
            self.connections += 1
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            for m in self.messages:
                await ws.send_str(json.dumps(m))
            await ws.send_str(json.dumps({"message_type": "heartbeat"}))
            async for _ in ws:            # stay open until the client disconnects
                pass
            return ws

        async def start():
            app = web.Application()
            app.router.add_get("/", handler)
            self.runner = web.AppRunner(app)
            await self.runner.setup()
            site = web.TCPSite(self.runner, "127.0.0.1", 0)
            await site.start()
            self.port = site._server.sockets[0].getsockname()[1]
            started.set()

        threading.Thread(target=lambda: (self.loop.run_until_complete(start()), self.loop.run_forever()), daemon=True).start()
        started.wait(5)

    def stop(self):
        asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)


def test_watch_queues_new_domains_and_quick_picks_them_up(settings, store):
    from techintel.watch import watch
    server = FakeCertStream([cert("*.agentlabs.io", "www.agentlabs.io"), cert("grandmasbakery.com"),
                             cert("api.vectorstack.ai"), cert("known.ai"), cert("x.vercel.app")])
    settings.raw["sources"]["certstream"]["url"] = f"ws://127.0.0.1:{server.port}/"
    store.append("candidates", [{"domain": "known.ai", "url": "https://known.ai/", "source": "seeds",
                                 "discovered_at": "2026-11-01T00:00:00Z", "detail": {}}])
    store.close()                                              # like a real run: the listener runs on its own

    asyncio.run(watch(settings, batch_minutes=0.03, batches=2))
    folder = settings.data_dir / "stream"
    queued = [json.loads(l)["domain"] for l in (folder / "pending.jsonl").read_text().splitlines()]
    assert queued == ["agentlabs.io", "vectorstack.ai"]        # interesting names only; known + duplicates skipped
    status = json.loads((folder / "status.json").read_text())
    assert status["batches"] == 2 and status["queued"] == 2 and status["certificates"] >= 10 and status["errors"] == 0
    server.stop()

    from techintel.store import DuckStore
    s = DuckStore(settings.data_dir)
    summary = asyncio.run(run_stage("discover", settings, s, sources=["stream"]))
    assert summary["new_candidates"] == 2 and summary["sources"]["stream"] == {"found": 2, "new": 2}
    assert {c["source"] for c in s.read("candidates") if c["domain"] != "known.ai"} == {"certstream"}
    assert not (folder / "pending.jsonl").exists() and list((folder / "taken").glob("taken-*.jsonl"))
    again = asyncio.run(run_stage("discover", settings, s, sources=["stream"]))
    assert again["new_candidates"] == 0                       # queue is consumed once
    s.close()


def test_watch_survives_connection_failures(settings):
    from techintel.watch import watch
    settings.raw["sources"]["certstream"]["url"] = "ws://127.0.0.1:9/"   # nothing listens there
    asyncio.run(watch(settings, batch_minutes=0.01, batches=3, retry_delay=0.05))
    status = json.loads((settings.data_dir / "stream" / "status.json").read_text())
    assert status["errors"] == 3 and status["batches"] == 0 and status["last_error"]
