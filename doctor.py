"""Health check for this machine:  uv run python doctor.py

Checks what the browser-based tools need (vehicles, tech intel profiling, the news browser
fallback) and prints a clear verdict with the fix for anything wrong:
  - Python / package versions
  - where the project, browser and caches live, and the file system of that drive
  - whether that drive supports symbolic links (Chromium on macOS needs them; exFAT/FAT don't have them)
  - whether Chromium starts and opens a page: directly with Playwright, then through crawl4ai
    (plain and with the stealth settings the vehicles scraper uses). No internet needed.
"""

import asyncio
import os
import platform
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

import project_paths

project_paths.apply_local_env()
OK, BAD, WARN = "OK  ", "FAIL", "WARN"
problems: list[str] = []


def line(state: str, what: str, detail: str = "") -> None:
    print(f"[{state}] {what}{(': ' + detail) if detail else ''}")


def filesystem_of(path: Path) -> str:
    """'apfs', 'exfat', 'ext4', ... for the mount that holds `path` ('' if unknown)."""
    path = path.resolve()
    try:
        if platform.system() == "Darwin":
            out = subprocess.run(["mount"], capture_output=True, text=True, timeout=10).stdout
            best = ("", "")
            for row in out.splitlines():   # "/dev/disk4s1 on /Volumes/MAC-DISK2 (exfat, local, ...)"
                if " on " in row and " (" in row:
                    mnt = row.split(" on ", 1)[1].rsplit(" (", 1)[0]
                    fs = row.rsplit(" (", 1)[1].split(",")[0]
                    if str(path).startswith(mnt) and len(mnt) > len(best[0]):
                        best = (mnt, fs)
            return best[1]
        best = ("", "")
        for row in Path("/proc/mounts").read_text().splitlines():
            parts = row.split()
            if len(parts) >= 3 and str(path).startswith(parts[1]) and len(parts[1]) > len(best[0]):
                best = (parts[1], parts[2])
        return best[1]
    except Exception:  # noqa: BLE001
        return ""


def supports_symlinks(folder: Path) -> bool:
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        target, link = Path(tmp) / "t", Path(tmp) / "l"
        target.write_text("x")
        try:
            os.symlink(target, link)
            return link.read_text() == "x"
        except OSError:
            return False


async def try_playwright() -> str | None:
    from playwright.async_api import async_playwright
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto("data:text/html,<h1>ok</h1>")
            text = await page.inner_text("h1")
            await browser.close()
            return None if text == "ok" else f"unexpected page content {text!r}"
    except Exception as e:  # noqa: BLE001
        return str(e).strip().splitlines()[0][:300]


async def try_crawl4ai(stealth: bool) -> str | None:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
    try:
        async with AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False, enable_stealth=stealth)) as c:
            r = await c.arun(url="raw://<html><body><h1>ok</h1><p>" + "word " * 50 + "</p></body></html>",
                             config=CrawlerRunConfig(verbose=False))
            return None if r.success and "ok" in (r.html or "") else (r.error_message or "no content")[:300]
    except Exception as e:  # noqa: BLE001
        return str(e).strip().splitlines()[0][:300]


def main() -> None:
    os.environ.setdefault("CRAWL4AI_NO_CLOUD_NOTICE", "1")
    print(f"Project: {project_paths.REPO_ROOT}\n")
    line(OK, "Python", f"{sys.version.split()[0]} on {platform.system()} {platform.machine()}")
    for pkg in ("crawl4ai", "playwright", "duckdb"):
        try:
            line(OK, pkg, metadata.version(pkg))
        except metadata.PackageNotFoundError:
            line(BAD, pkg, "not installed (run: uv sync --frozen)"); problems.append(f"{pkg} missing")

    browsers = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""))
    print()
    line(OK, "Browser folder", str(browsers))
    installed = sorted(p.name for p in browsers.glob("chromium*")) if browsers.exists() else []
    if installed:
        line(OK, "Chromium installed", ", ".join(installed))
    else:
        line(BAD, "Chromium installed", "none found (run: source env.sh && uv run crawl4ai-setup)")
        problems.append("no browser")

    fs = filesystem_of(browsers if browsers.exists() else project_paths.REPO_ROOT)
    links = supports_symlinks(project_paths.LOCAL_DIR)
    fat = fs.lower() in ("exfat", "msdos", "fat32", "vfat", "ntfs", "fuseblk")
    line(WARN if fat else OK, "File system of that drive", fs or "unknown")
    line(OK if links else BAD, "Symbolic links on that drive", "supported" if links else "NOT supported")
    if not links or (fat and platform.system() == "Darwin"):
        problems.append("filesystem")

    print("\nStarting Chromium (no internet needed)...")
    err = asyncio.run(try_playwright())
    line(OK if not err else BAD, "Playwright opens a page", err or "")
    if err:
        problems.append("playwright")
    for stealth in (False, True):
        err = asyncio.run(try_crawl4ai(stealth))
        label = "crawl4ai opens a page" + (" (stealth, as vehicles uses)" if stealth else "")
        line(OK if not err else BAD, label, err or "")
        if err:
            problems.append("crawl4ai-stealth" if stealth else "crawl4ai")

    print()
    if not problems:
        print("All good: the browser works here.")
        return
    print("What to do:")
    if "filesystem" in problems:
        print("  - This drive can't hold the browser properly (no symbolic links, which Chromium on macOS needs).\n"
              "    Keep the browser on the Mac's own drive instead (about 500 MB), then reinstall it:\n"
              "      1. In automation.conf add:   BROWSERS_ON_SYSTEM_DRIVE=1\n"
              "      2. Run:  source env.sh && uv run crawl4ai-setup\n"
              "    (Or reformat the external drive as APFS / Mac OS Extended.) Data and databases can stay where they are.")
    if "no browser" in problems and "filesystem" not in problems:
        print("  - Install the browser:  source env.sh && uv run crawl4ai-setup")
    if "playwright" in problems and "filesystem" not in problems and "no browser" not in problems:
        print("  - Chromium doesn't start. Reinstall it:  source env.sh && rm -rf \"$PLAYWRIGHT_BROWSERS_PATH\"/chromium* "
              "&& uv run crawl4ai-setup")
    if problems == ["crawl4ai-stealth"]:
        print("  - Only stealth mode fails: in automation.conf set  VEHICLES_ARGS=\"--limit 20 --no-stealth\"")
    sys.exit(1)


if __name__ == "__main__":
    main()
