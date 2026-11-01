import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from techintel.settings import CONFIG_DIR, load_settings  # noqa: E402
from techintel.store import FileStore  # noqa: E402


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Default settings, but data/output in a temp folder and fast, local-friendly crawling."""
    monkeypatch.setenv("TECHINTEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TECHINTEL_OUTPUT_DIR", str(tmp_path / "out"))
    raw = json.loads((CONFIG_DIR / "settings.json").read_text())
    raw["crawl"].update(delay_seconds=0, concurrency=4, page_timeout_ms=15000)
    raw["profile"]["fetch_mode"] = "http"           # no browser needed in tests
    raw["qualify"]["browser_fallback"] = False
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "settings.json").write_text(json.dumps(raw))
    (cfg_dir / "fingerprints.json").write_text((CONFIG_DIR / "fingerprints.json").read_text())
    (cfg_dir / "seeds.txt").write_text("")
    return load_settings(cfg_dir / "settings.json")


@pytest.fixture
def store(settings):
    return FileStore(settings.data_dir)
