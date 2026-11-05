"""Health check for this machine:  uv run python doctor.py   (--verbose: also show Chromium's own messages)

To check it the way scheduled runs see it (different environment and permissions):  ./schedule.sh doctor

Checks what the browser-based tools need (vehicles, tech intel profiling, the news browser
fallback) and prints a clear verdict with the fix for anything wrong:
  - Python / package versions
  - where the project, browser and caches live, and the file system of that drive
  - whether that drive supports symbolic links (Chromium on macOS needs them; exFAT/FAT don't have them)
  - whether Chromium starts and opens a page: directly with Playwright, then through crawl4ai
    (plain and with the page settings the vehicles scraper uses). No internet needed.
  - whether it survives a page that needs graphics (WebGL + a screenshot). Simple pages don't start
    Chromium's separate GPU process; real websites do, and on one Mac that process crashed and took
    the browser with it. Tried with the tools' Chromium flags (CHROMIUM_ARGS) and without them.
  - PostgreSQL (the database the tools are moving to): reachable, schemas and extensions there, the reader
    login can't write, free disk space. Same as:  uv run python -m warehouse pg-check
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


async def try_crawl4ai(vehicles_settings: bool = False) -> str | None:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
    extra = {}
    if vehicles_settings:                       # the same page settings the vehicles scraper uses
        try:
            from vehicles.scrape import load_sites
            extra = dict(load_sites()[0].get("crawl", {}))
        except Exception:  # noqa: BLE001
            extra = {"delay_before_return_html": 2.0}
    try:
        async with AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False,
                                                        extra_args=project_paths.chromium_args())) as c:
            r = await c.arun(url="raw://<html><body><h1>ok</h1><p>" + "word " * 50 + "</p></body></html>",
                             config=CrawlerRunConfig(verbose=False, **extra))
            return None if r.success and "ok" in (r.html or "") else (r.error_message or "no content")[:300]
    except Exception as e:  # noqa: BLE001
        return str(e).strip().splitlines()[0][:300]


GRAPHICS_PAGE = ("raw://<html><body style='margin:0'><canvas id='c' width='400' height='300'></canvas>"
                 "<div style='transform:rotate3d(1,1,0,30deg);will-change:transform;width:200px;height:100px;"
                 "background:linear-gradient(red,blue)'>3d</div><h1>ok</h1><script>"
                 "var g=document.getElementById('c').getContext('webgl');"
                 "if(g){g.clearColor(0,0.5,1,1);g.clear(g.COLOR_BUFFER_BIT);}"
                 "document.querySelector('h1').textContent=g?'ok webgl':'ok no-webgl';</script></body></html>")


async def try_graphics_page(args: list[str]) -> str | None:
    """A page that makes Chromium start its GPU process (WebGL, 3D CSS, a screenshot)."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
    try:
        async with AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False, extra_args=args)) as c:
            for _ in range(2):                  # the crash came on the 2nd or 3rd page: try more than one
                r = await c.arun(url=GRAPHICS_PAGE, config=CrawlerRunConfig(verbose=False, screenshot=True))
                if not (r.success and "ok" in (r.html or "")):
                    return (r.error_message or "no content").strip().splitlines()[0][:300]
            return None
    except Exception as e:  # noqa: BLE001
        lines = [ln for ln in str(e).splitlines() if ln.strip()]
        why = [ln.strip() for ln in lines if "FATAL" in ln or "GPU process" in ln]
        return " | ".join(why[-2:])[:300] if why else (lines[0][:300] if lines else type(e).__name__)


def check_postgres() -> bool:
    """Print the PostgreSQL checks; True when one failed. Its own fixes are in each line, not in "What to do"."""
    from warehouse.postgres import check
    failed = False
    for r in check():
        line(WARN if r.ok and r.warn else (OK if r.ok else BAD), r.what, r.detail)
        failed |= not r.ok
    return failed


def main() -> None:
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    if verbose:
        os.environ["DEBUG"] = "pw:browser"      # Playwright then prints Chromium's own output (why it stops)
    os.environ.setdefault("CRAWL4AI_NO_CLOUD_NOTICE", "1")
    print(f"Project: {project_paths.REPO_ROOT}")
    print(f"Started from: {'a Terminal' if sys.stdin and sys.stdin.isatty() else 'the background (no Terminal)'}")
    for name in ("USER", "HOME", "TMPDIR", "PATH", "PLAYWRIGHT_BROWSERS_PATH"):
        print(f"  {name}={os.environ.get(name, '(not set)')}")
    print()
    line(OK, "Python", f"{sys.version.split()[0]} on {platform.system()} {platform.machine()}")
    for pkg in ("crawl4ai", "playwright", "duckdb", "psycopg"):
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
    for veh in (False, True):
        err = asyncio.run(try_crawl4ai(veh))
        label = "crawl4ai opens a page" + (" (the vehicles page settings)" if veh else "")
        line(OK if not err else BAD, label, err or "")
        if err:
            problems.append("crawl4ai-vehicles" if veh else "crawl4ai")

    args = project_paths.chromium_args()
    shown = " ".join(args) or "none"
    err = asyncio.run(try_graphics_page(args))
    line(OK if not err else BAD, f"crawl4ai opens a graphics page (tools' flags: {shown})", err or "")
    if err:
        problems.append("graphics")
    if args:                                    # for information: is the flag actually needed here?
        plain = asyncio.run(try_graphics_page([]))
        line(OK if not plain else WARN, "  same page without those flags",
             "also works (the flags aren't needed here)" if not plain else f"fails: {plain}")

    print("\nPostgreSQL...")
    pg_failed = check_postgres()

    print()
    if not problems and not pg_failed:
        print("All good: the browser and PostgreSQL work here.")
        return
    if not problems:
        print("The browser works here. PostgreSQL: see the FAIL lines above (each says what to do).")
        sys.exit(1)
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
    if problems == ["crawl4ai-vehicles"]:
        print("  - Only the vehicles page settings fail: check the \"crawl\" options in vehicles/config/sites.json.")
    if "graphics" in problems:
        if args:
            print("  - Chromium crashes on pages that need graphics even with CHROMIUM_ARGS. In automation.conf try\n"
                  "      CHROMIUM_ARGS=\"--in-process-gpu --disable-gpu-sandbox\"\n"
                  "    then run this check again, and send the output with --verbose.")
        else:
            print("  - Chromium crashes on pages that need graphics. Remove CHROMIUM_ARGS from automation.conf\n"
                  "    (the default, --in-process-gpu, is meant for this), then run this check again.")
    if not verbose:
        print("  - For Chromium's own messages (why it stopped), run again with:  uv run python doctor.py --verbose")
    sys.exit(1)


if __name__ == "__main__":
    main()
