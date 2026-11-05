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
