"""Storage (files for now; a database can replace this module later).

    data/worldnews/
        items.jsonl           every headline ever collected, once (first time seen), for history/analytics
        runs/<time>.json      full snapshot of each run: every source, its status and its latest headlines
        feed_cache.json       feed URLs found by auto-discovery, reused on the next run
"""

import json
from pathlib import Path


class NewsStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "runs").mkdir(parents=True, exist_ok=True)

    # history ---------------------------------------------------------------
    def known_urls(self) -> set[str]:
        path = self.root / "items.jsonl"
        if not path.exists():
            return set()
        with open(path, encoding="utf-8") as f:
            return {json.loads(line)["url"] for line in f if line.strip()}

    def append_items(self, items: list[dict]) -> None:
        with open(self.root / "items.jsonl", "a", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    # snapshots -------------------------------------------------------------
    def save_snapshot(self, snapshot: dict) -> Path:
        stamp = snapshot["run_at"].replace(":", "-")
        path = self.root / "runs" / f"{stamp}.json"
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def latest_snapshot(self) -> dict | None:
        runs = sorted((self.root / "runs").glob("*.json"))
        return json.loads(runs[-1].read_text(encoding="utf-8")) if runs else None

    # discovered feeds ------------------------------------------------------
    def feed_cache(self) -> dict[str, str]:
        path = self.root / "feed_cache.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def save_feed_cache(self, cache: dict[str, str]) -> None:
        (self.root / "feed_cache.json").write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")
