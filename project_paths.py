"""Keep everything inside this project folder (e.g. on an external drive).

By default some tools write to the user's home folder on the main drive:
crawl4ai (~/.crawl4ai), the Playwright browser (~/Library/Caches/ms-playwright)
and tldextract (~/.cache). apply_local_env() points them at <project>/.local/
instead. It only sets variables that aren't already set, so env.sh or your own
settings always win. Every package here calls it on import, before crawl4ai loads.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
LOCAL_DIR = Path(os.environ.get("PROJECT_LOCAL_DIR", REPO_ROOT / ".local"))
DATA_DIR = Path(os.environ.get("PROJECT_DATA_DIR", REPO_ROOT / "data"))

LOCAL_ENV = {
    "CRAWL4_AI_BASE_DIRECTORY": LOCAL_DIR / "crawl4ai",       # crawl4ai cache, robots.txt cache, settings
    "PLAYWRIGHT_BROWSERS_PATH": LOCAL_DIR / "ms-playwright",  # Chromium used by crawl4ai
    "TLDEXTRACT_CACHE": LOCAL_DIR / "tldextract",
}


def apply_local_env() -> None:
    for name, path in LOCAL_ENV.items():
        if not os.environ.get(name):
            os.environ[name] = str(path)
            path.mkdir(parents=True, exist_ok=True)
