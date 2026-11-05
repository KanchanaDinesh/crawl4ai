"""Countries: ISO code, English name and Wikidata id (config/countries.json, made from Wikidata once)."""

import json
from functools import lru_cache
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "config" / "countries.json"


@lru_cache(maxsize=1)
def _load(path: str) -> tuple:
    return tuple(json.loads(Path(path).read_text(encoding="utf-8")))


def load_countries(path: Path | None = None) -> list[dict]:
    return [dict(c) for c in _load(str(path or CONFIG))]


def country_names() -> dict[str, str]:
    return {c["code"]: c["name"] for c in load_countries()}
