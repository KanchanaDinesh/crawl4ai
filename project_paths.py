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


def browsers_on_system_drive() -> bool:
    """BROWSERS_ON_SYSTEM_DRIVE=1 in automation.conf (for drives without symbolic links, e.g. exFAT)."""
    import re
    try:
        text = (REPO_ROOT / "automation.conf").read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(re.search(r'^\s*BROWSERS_ON_SYSTEM_DRIVE="?1"?(\s|#|$)', text, re.M))


# Chromium's separate graphics (GPU) process crashed on Kanchana's Mac ("GPU process isn't usable. Goodbye."),
# and took the whole browser with it: tech intel profiling failed and the news fallback hung. With
# --in-process-gpu the graphics code runs inside the browser process, so there's no GPU process to crash.
DEFAULT_CHROMIUM_ARGS = ["--in-process-gpu"]


def _conf_value(name: str) -> str | None:
    """NAME="value" from automation.conf, or None if it isn't there."""
    import re
    try:
        text = (REPO_ROOT / "automation.conf").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(rf'^\s*{name}="([^"]*)"', text, re.M)
    return m.group(1) if m else None


def chromium_args() -> list[str]:
    """Extra Chromium flags for every browser the tools start (tech intel, news fallback, vehicles, scrapers).

    CHROMIUM_ARGS in the environment or automation.conf replaces the default; CHROMIUM_ARGS="" means none.
    `uv run python doctor.py` shows whether they're needed on this machine."""
    value = os.environ.get("CHROMIUM_ARGS")
    if value is None:
        value = _conf_value("CHROMIUM_ARGS")
    return DEFAULT_CHROMIUM_ARGS[:] if value is None else value.split()


def load_database_env(path: Path | None = None) -> None:
    """KEY=value lines from .local/database.env (git-ignored): PostgreSQL addresses and passwords stay out of git.

    Only sets variables that aren't already set, so the environment always wins."""
    path = path or LOCAL_DIR / "database.env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and not os.environ.get(key):
            os.environ[key] = value


def apply_local_env() -> None:
    load_database_env()
    if browsers_on_system_drive() and not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        import platform
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(
            Path.home() / ("Library/Caches/ms-playwright" if platform.system() == "Darwin" else ".cache/ms-playwright"))
    for name, path in LOCAL_ENV.items():
        if not os.environ.get(name):
            os.environ[name] = str(path)
            path.mkdir(parents=True, exist_ok=True)
