import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Tests never touch the real data/ or output/ folders: by default everything goes to a temporary folder
# (individual tests point these somewhere else again with monkeypatch).
_TMP = Path(tempfile.mkdtemp(prefix="crawl4ai-tests-"))
os.environ["PROJECT_DATA_DIR"] = str(_TMP / "data")
os.environ["SCRAPERS_OUTPUT_DIR"] = str(_TMP / "output")
# crawl4ai keeps a robots.txt cache by host:port. Test sites run on 127.0.0.1 with random ports, and one that
# says "Disallow: /" was cached for days, so a later test that got the same port was refused. Own folder per run.
os.environ["CRAWL4_AI_BASE_DIRECTORY"] = str(_TMP / "crawl4ai")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from techintel.settings import CONFIG_DIR, load_settings  # noqa: E402
from techintel.store import DuckStore  # noqa: E402


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Default settings, but data/output in a temp folder and fast, local-friendly crawling."""
    monkeypatch.setenv("TECHINTEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TECHINTEL_OUTPUT_DIR", str(tmp_path / "out"))
    raw = json.loads((CONFIG_DIR / "settings.json").read_text())
    raw["crawl"].update(delay_seconds=0, concurrency=4, page_timeout_ms=15000)
    raw["profile"]["fetch_mode"] = "http"           # no browser needed in tests
    raw["qualify"]["browser_fallback"] = False
    raw["enrich"]["rdap_bootstrap_url"] = ""          # no internet in tests: use rdap_url (faked per test)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "settings.json").write_text(json.dumps(raw))
    (cfg_dir / "fingerprints.json").write_text((CONFIG_DIR / "fingerprints.json").read_text())
    (cfg_dir / "seeds.txt").write_text("")
    return load_settings(cfg_dir / "settings.json")


@pytest.fixture
def store(settings):
    s = DuckStore(settings.data_dir)      # tests run on the real database backend
    yield s
    s.close()


# --- PostgreSQL ----------------------------------------------------------------------------------------------
# Tests never touch the real PostgreSQL either: the first test that asks for it starts a throwaway server
# (initdb into a temp folder, its own socket, no TCP port), stopped again when the tests finish. ~2-3 s.

def _pg_bin(name: str) -> str | None:
    import shutil
    import subprocess
    found = shutil.which(name)
    if found:
        return found
    try:
        bindir = subprocess.run(["pg_config", "--bindir"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        bindir = ""
    candidates = [Path(bindir)] if bindir else []
    candidates += sorted(Path("/opt/homebrew/opt").glob("postgresql@*/bin"), reverse=True)
    candidates += sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True)
    for folder in candidates:
        if (folder / name).exists():
            return str(folder / name)
    return None


@pytest.fixture(scope="session")
def pg_server():
    """A throwaway PostgreSQL: yields the admin conninfo (superuser `admin`, database `postgres`)."""
    import random
    import shutil
    import subprocess
    initdb, pg_ctl = _pg_bin("initdb"), _pg_bin("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.fail("PostgreSQL's programs (initdb, pg_ctl) aren't installed. On the Mac:  brew install postgresql@18")
    # the socket path must stay short (about 100 characters at most), so not under pytest's long tmp paths
    sock = Path(tempfile.mkdtemp(prefix="c4pg-"))
    data = sock / "data"
    port = random.randint(20000, 60000)
    # macOS: with no valid LC_ALL the server stops at once ("postmaster became multithreaded during startup")
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    # --locale=C: the shell's LANG/LC_* can be unset or unusable (initdb then refuses), and tests don't need one
    made = subprocess.run([initdb, "-D", str(data), "-U", "admin", "--auth=trust", "-E", "UTF8", "--locale=C",
                           "--no-sync", "--no-instructions"], capture_output=True, text=True, env=env)
    if made.returncode != 0:
        pytest.fail(f"initdb failed for the test PostgreSQL: {made.stderr or made.stdout}")
    opts = f"-p {port} -k {sock} -c listen_addresses='' -c fsync=off -c full_page_writes=off"
    started = subprocess.run([pg_ctl, "-D", str(data), "-o", opts, "-l", str(sock / "server.log"), "-w", "start"],
                             capture_output=True, text=True, env=env)
    if started.returncode != 0:
        log = (sock / "server.log").read_text() if (sock / "server.log").exists() else ""
        pytest.fail(f"The test PostgreSQL didn't start: {started.stderr or started.stdout}\n{log[-2000:]}")
    try:
        yield f"host={sock} port={port} user=admin dbname=postgres"
    finally:
        subprocess.run([pg_ctl, "-D", str(data), "-m", "immediate", "stop"], capture_output=True, env=env)
        shutil.rmtree(sock, ignore_errors=True)


@pytest.fixture
def pg_env(pg_server, monkeypatch):
    """Points CRAWL4AI_*_URL at the test server, with a database and logins of this test's own (new names
    per test, so tests never see each other's tables). Returns the three conninfo strings."""
    import uuid
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    tag = uuid.uuid4().hex[:8]
    base = conninfo_to_dict(pg_server)
    urls = {
        "admin": pg_server,
        "writer": make_conninfo(host=base["host"], port=base["port"], dbname=f"c4_{tag}", user=f"w_{tag}"),
        "reader": make_conninfo(host=base["host"], port=base["port"], dbname=f"c4_{tag}", user=f"r_{tag}"),
    }
    monkeypatch.setenv("CRAWL4AI_ADMIN_URL", urls["admin"])
    monkeypatch.setenv("CRAWL4AI_DATABASE_URL", urls["writer"])
    monkeypatch.setenv("CRAWL4AI_READER_URL", urls["reader"])
    return urls
