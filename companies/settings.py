"""Settings: companies/config/settings.json (COMPANIES_SETTINGS=path to use another file)."""

import json
import os
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent / "config"


def load_settings(path: str | Path | None = None) -> dict:
    path = Path(path or os.environ.get("COMPANIES_SETTINGS") or CONFIG_DIR / "settings.json")
    return json.loads(path.read_text(encoding="utf-8"))
