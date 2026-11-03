"""Tech intel when the browser crashes or a service refuses us, and the collect / update split.

A crashed browser or a refusing RDAP server says nothing about a site, so nothing may be saved
as if it did: the sites stay due for the next run. No real browser or internet is used here.
"""

import asyncio
import logging
import os
import subprocess
from pathlib import Path

import pytest

from techintel import cli, rdap
from techintel.fetch import Fetcher, FetchResult
from techintel.stages import enrich, profile

ROOT = Path(__file__).resolve().parent.parent
CRASH = ("BrowserContext.new_page: Target page, context or browser has been closed\nBrowser logs:\n"
         "<launching> /x/chrome-headless-shell --no-sandbox\n"
         "[1103/033823.392296:FATAL:gpu_data_manager_impl_private.cc:389] GPU process isn't usable. Goodbye.")


class CrashingCrawler:
    """Stands in for crawl4ai's AsyncWebCrawler: every page crashes the browser (or hangs)."""

    def __init__(self, hang: bool = False):
        self.calls, self.hang = 0, hang

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def arun(self, url, config):
        self.calls += 1
        if self.hang:
            await asyncio.sleep(60)
        raise RuntimeError(CRASH)


def crashing_fetcher(monkeypatch, crawler: CrashingCrawler, **config) -> Fetcher:
    async def fake_start(self):
        self._crawler = crawler
    monkeypatch.setattr(Fetcher, "_start", fake_start)
    return Fetcher({"delay_seconds": 0, "max_browser_restarts": 2, **config}, "browser")


def test_a_browser_that_keeps_crashing_is_given_up_with_chromiums_reason(monkeypatch, caplog):
    crawler = CrashingCrawler()

    async def go():
        async with crashing_fetcher(monkeypatch, crawler) as f:
            results = [await f.fetch(f"https://site{i}.dev/") for i in range(6)]
            return f, results

    with caplog.at_level(logging.WARNING):
        f, results = asyncio.run(go())
    assert all(r.browser_failed and not r.success for r in results)
    assert f.restarts == 2 and f.dead and "GPU process isn't usable" in f.dead
    assert crawler.calls == 5                          # first browser + 2 restarts crashed, then nothing is tried
    assert results[-1].error.startswith("not tried: the browser crashed")
    assert "Chromium said: [1103/033823.392296:FATAL" in caplog.text
    assert caplog.text.count("full browser error (first crash only)") == 1


def test_the_reason_names_what_failed_not_just_crawl4ais_wrapper():
    from techintel.fetch import chromium_reason
    error = ("Unexpected error in _crawl_web at line 636 in wrap_api_call (.venv/.../_connection.py):\n"
             "Error: Page.goto: Target page, context or browser has been closed\nCode context:\n 631  )")
    assert chromium_reason(error) == "Error: Page.goto: Target page, context or browser has been closed"


def test_a_hanging_page_hits_the_hard_time_limit(monkeypatch):
    async def go():
        async with crashing_fetcher(monkeypatch, CrashingCrawler(hang=True), fetch_hard_timeout_seconds=0.2) as f:
            return await asyncio.wait_for(f.fetch("https://slow.dev/"), 5)

    r = asyncio.run(go())
    assert not r.success and not r.browser_failed and "timed out" in r.error


class DeadBrowserFetcher:
    """A Fetcher whose browser is already down."""

    def __init__(self, *args, **kwargs):
        self.dead = "the browser crashed 4 times (Chromium: FATAL)"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def fetch(self, url):
        return FetchResult(url=url, mode="browser", success=False, browser_failed=True, error=f"not tried: {self.dead}")


def _qualified(store, *domains):
    store.append("qualifications", [{"domain": d, "url": f"https://{d}/", "status": "qualified", "category": "ai_ml",
                                     "score": 9, "checked_at": "2026-11-03T00:00:00Z"} for d in domains])


def test_profile_saves_nothing_when_the_browser_is_down_and_forgets_old_crash_rows(settings, store, monkeypatch):
    _qualified(store, "vercel.com", "ietf.org", "gone.dev")
    store.append("profiles", [  # written by the old version during a crash, and one real "unreachable"
        {"domain": "vercel.com", "status": "unreachable", "profiled_at": "2026-11-03T05:14:05Z",
         "error": "browser closed unexpectedly (run: uv run python doctor.py)"},
        {"domain": "ietf.org", "status": "unreachable", "profiled_at": "2026-11-03T05:13:45Z",
         "error": "Browser is not available. It may have been closed, crashed, or not yet"},
        {"domain": "gone.dev", "status": "unreachable", "profiled_at": "2026-11-03T05:13:45Z",
         "error": "domain does not resolve (no website yet)"}])
    monkeypatch.setattr(profile, "Fetcher", DeadBrowserFetcher)

    summary = asyncio.run(cli.run_stage("profile", settings, store))
    assert summary["browser_down"] and summary["profiled"] == 0
    assert summary["left_for_next_run"] == 2                       # vercel.com + ietf.org, due again
    assert set(store.latest("profiles")) == {"gone.dev"}           # the real answer stays
    assert "profile" in cli.FAILED_STAGES
    cli.FAILED_STAGES.clear()


def test_update_skips_monitor_when_the_browser_is_down_and_collect_never_profiles(settings, store, monkeypatch):
    calls = []

    async def fake_run_stage(name, settings, store, **options):
        calls.append(name)
        return {"browser_down": True} if name == "profile" else {}
    monkeypatch.setattr(cli, "run_stage", fake_run_stage)

    asyncio.run(cli.collect(settings, store, {}, []))
    assert calls == ["discover", "qualify", "enrich"]
    calls.clear()
    asyncio.run(cli.update(settings, store, {}, []))
    assert calls == ["profile", "report"]


def test_enrich_backs_off_when_rdap_refuses_and_saves_nothing(settings, store, monkeypatch):
    _qualified(store, "a.dev", "b.dev", "c.dev", "d.dev")
    store.append("registrations", [{"domain": "d.dev", "checked_at": "2026-11-03T05:13:35Z",  # old version saved this
                                    "error": "403, message='Forbidden', url='https://rdap.org/domain/d.dev'"}])
    asked = []

    async def refusing_lookup(session, domain, url_template, timeout):
        asked.append(domain)
        raise rdap.Refused(403, "rdap.org")
    monkeypatch.setattr(rdap, "lookup", refusing_lookup)
    settings.section("enrich")["concurrency"] = 1

    summary = asyncio.run(enrich.run(settings, store))
    assert asked == ["a.dev"]                                    # stopped after the first refusal
    assert summary["looked_up"] == 0 and summary["left_for_next_run"] == 4
    assert "rdap.org refused the request (HTTP 403)" in summary["refused"]
    assert [r["domain"] for r in store.read("registrations")] == ["d.dev"]   # nothing new saved


def test_run_all_has_the_two_tech_steps():
    out = subprocess.run(["bash", str(ROOT / "run_all.sh"), "--list"], capture_output=True, text=True, timeout=60,
                         env={**os.environ})
    assert out.returncode == 0, out.stderr
    assert "tech_collect" in out.stdout.splitlines()[0]          # new sites are always collected by default
    assert "techintel collect" in out.stdout and "techintel update" in out.stdout   # update can be run by hand


def test_registration_dates_come_from_each_registry():
    servers = rdap.servers_from_bootstrap({"services": [[["com", "net"], ["https://rdap.verisign.com/com/v1/"]],
                                                        [["dev"], ["https://pubapi.registry.google/rdap/"]]]})
    assert rdap.url_template_for("vercel.com", servers, "x") == "https://rdap.verisign.com/com/v1/domain/{domain}"
    assert rdap.url_template_for("web.dev", servers, "x") == "https://pubapi.registry.google/rdap/domain/{domain}"
    assert rdap.url_template_for("openai.io", servers, "x") is None          # .io has no RDAP: don't ask anyone
    assert rdap.url_template_for("openai.io", None, "https://rdap.org/domain/{domain}").startswith("https://rdap.org")


def test_one_refusing_registry_doesnt_stop_the_others(settings, store, monkeypatch):
    _qualified(store, "a.com", "b.com", "c.dev", "d.io")
    servers = {"com": "https://rdap.verisign.com/com/v1/", "dev": "https://pubapi.registry.google/rdap/"}

    async def fake_servers(*a, **k):
        return servers
    asked = []

    async def lookup(session, domain, url_template, timeout):
        asked.append(domain)
        if "verisign" in url_template:
            raise rdap.Refused(429, "rdap.verisign.com")
        return {"registered_at": "2026-10-20T08:15:00Z", "expires_at": None, "updated_at": None, "registrar": "X"}
    monkeypatch.setattr(rdap, "load_servers", fake_servers)
    monkeypatch.setattr(rdap, "lookup", lookup)
    settings.section("enrich").update(concurrency=1, rdap_bootstrap_url="https://iana.example/dns.json", delay_seconds=0)

    summary = asyncio.run(enrich.run(settings, store))
    assert sorted(asked) == ["a.com", "c.dev"]                 # b.com: verisign already refused; d.io: no RDAP
    saved = {r["domain"]: r for r in store.read("registrations")}
    assert saved["c.dev"]["registered_at"] and "no RDAP service for .io" in saved["d.io"]["error"]
    assert "a.com" not in saved and "b.com" not in saved      # refusals aren't answers: tried next run
    assert "rdap.verisign.com" in summary["refused"]


def test_crtsh_runs_every_few_hours_and_waits_after_a_failure():
    from datetime import datetime, timedelta, timezone
    from techintel.sources.crtsh import wait_reason
    ago = lambda **kw: (datetime.now(timezone.utc) - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%S")
    cfg = {"min_hours_between_runs": 6, "retry_after_minutes": 60, "lookback_days": 3}
    assert wait_reason({}, cfg) is None                                            # never asked: due
    assert "last checked" in wait_reason({"last_ok_at": ago(hours=2)}, cfg)
    assert wait_reason({"last_ok_at": ago(hours=7)}, cfg) is None
    assert "failed" in wait_reason({"last_ok_at": ago(hours=9), "last_failed_at": ago(minutes=10)}, cfg)
    assert wait_reason({"last_ok_at": ago(hours=9), "last_failed_at": ago(minutes=90)}, cfg) is None
