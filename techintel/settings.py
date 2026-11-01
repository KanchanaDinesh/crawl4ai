"""Settings and paths.

Config lives in techintel/config/ (settings.json, fingerprints.json, seeds.txt).
Pipeline state goes to data/techintel/ and reports to output/techintel/, both
git-ignored. Override with TECHINTEL_DATA_DIR / TECHINTEL_OUTPUT_DIR, or pass
--config to use a different settings file.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
CONFIG_DIR = PACKAGE_DIR / "config"


@dataclass
class Settings:
    raw: dict[str, Any]
    config_dir: Path
    data_dir: Path
    output_dir: Path

    def section(self, name: str) -> dict[str, Any]:
        return self.raw.get(name, {})

    def source(self, name: str) -> dict[str, Any]:
        return self.raw.get("sources", {}).get(name, {})

    def config_file(self, name: str) -> Path:
        path = Path(name)
        return path if path.is_absolute() else self.config_dir / path

    def load_json(self, name: str) -> Any:
        with open(self.config_file(name), encoding="utf-8") as f:
            return json.load(f)


def load_settings(path: str | Path | None = None) -> Settings:
    settings_path = Path(path) if path else CONFIG_DIR / "settings.json"
    with open(settings_path, encoding="utf-8") as f:
        raw = json.load(f)
    return Settings(
        raw=raw,
        config_dir=settings_path.resolve().parent,
        data_dir=Path(os.environ.get("TECHINTEL_DATA_DIR", REPO_ROOT / "data" / "techintel")),
        output_dir=Path(os.environ.get("TECHINTEL_OUTPUT_DIR", REPO_ROOT / "output" / "techintel")),
    )
