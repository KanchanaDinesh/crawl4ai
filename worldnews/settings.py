"""Settings, source list and paths.

Config:  worldnews/config/settings.json  (limit, groups/priority order, fetch options, tech keywords)
         worldnews/config/sources.json   (the news sites; add your own here or with `add`)
Data:    data/worldnews/      (history of all headlines + one snapshot per run; git-ignored)
Reports: output/worldnews/    (HTML / Markdown / CSV / JSON; git-ignored)
Override folders with WORLDNEWS_DATA_DIR / WORLDNEWS_OUTPUT_DIR.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
CONFIG_DIR = PACKAGE_DIR / "config"


@dataclass
class Settings:
    raw: dict
    sources_path: Path
    data_dir: Path
    output_dir: Path

    def section(self, name: str) -> dict:
        return self.raw.get(name) or {}

    @property
    def groups(self) -> list[dict]:
        return self.raw.get("groups", [])

    @property
    def group_names(self) -> dict[str, str]:
        return {g["code"]: g["name"] for g in self.groups}

    def load_sources(self) -> list[dict]:
        with open(self.sources_path, encoding="utf-8") as f:
            return json.load(f)

    def save_sources(self, sources: list[dict]) -> None:
        lines = ",\n".join("  " + json.dumps(s, ensure_ascii=False) for s in sources)
        self.sources_path.write_text(f"[\n{lines}\n]\n", encoding="utf-8")


def load_settings(config_dir: str | Path | None = None) -> Settings:
    folder = Path(config_dir) if config_dir else CONFIG_DIR
    with open(folder / "settings.json", encoding="utf-8") as f:
        raw = json.load(f)
    return Settings(
        raw=raw,
        sources_path=folder / "sources.json",
        data_dir=Path(os.environ.get("WORLDNEWS_DATA_DIR", REPO_ROOT / "data" / "worldnews")),
        output_dir=Path(os.environ.get("WORLDNEWS_OUTPUT_DIR", REPO_ROOT / "output" / "worldnews")),
    )
