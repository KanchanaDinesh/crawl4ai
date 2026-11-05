# Guide for Claude (read automatically at the start of every session)

This is Kanchana's personal data-collection project, built on the crawl4ai library. It started as a fork
of crawl4ai and is now a standalone project with only our own code. **STATUS.md has the full history,
the current state of every part, open issues and next steps. Read it before doing anything:**

@STATUS.md

## How to work with Kanchana

- Keep explanations short and plain, with copy-paste commands for the Mac Terminal (zsh). Kanchana often
  writes by voice, so messages can have typos ("ekman" = ikman.lk, "rear7" = riyasewana).
- **Commit and push straight to `main`** when a change is done and tested. This is Kanchana's standing
  instruction ("just push the changes to the main branch"). No pull requests unless asked.
- Before pushing, run the tests: `uv run --frozen pytest -q` (about 20 seconds, needs no internet).
- After a change, tell Kanchana exactly what to run, e.g. `git pull origin main`, and to restart the
  dashboard if it's running.
- **Update STATUS.md** whenever something meaningful changes: what was done, new issues, next steps.
  Then the next session can continue without anything being explained again.
- Never get around bot protection: no browser impersonation (curl_cffi was refused and removed), no CAPTCHA
  solving, no proxy rotation. Use an honest user agent, back off on 401/403/429, and use public APIs or
  other sources instead (e.g. Stooq when Yahoo blocks).

## Where it runs

- Kanchana's Mac, project folder on the external drive: `/Volumes/MAC-DISK2/crawl4ai`.
  GitHub: `KanchanaDinesh/crawl4ai`, branch `main`.
- Everything stays inside the project folder (packages, browser, caches, data). In each new Terminal
  window: `cd /Volumes/MAC-DISK2/crawl4ai && source env.sh`. `env.sh` and `project_paths.py` set
  this up.
- Python packages are managed with uv: `uv sync --frozen`; run things with `uv run python -m <tool> ...`.
  Dependencies are in `pyproject.toml` (crawl4ai pinned to 0.9.5); don't hand-edit `uv.lock`.

## Layout and conventions

```
techintel/  worldnews/  markets/  vehicles/   one package per tool, each with its own DuckDB in data/<tool>/:
                                               __init__.py (calls project_paths.apply_local_env()), __main__.py,
                                               cli.py, store.py, config/, README.md. Run: python -m <tool> <command>
scrapers/        older single-file scrapers (Sri Lankan news, products, IMDb, CSE) + common.py helpers
warehouse/       DuckDB helpers: DB_FILES, connect() (waits if another process is writing), python -m warehouse
dashboard/       local web dashboard (aiohttp + plain JS, no build step): python -m dashboard --open
run_all.sh       run steps/profiles once, with per-tool locks and logs in logs/
schedule.sh      launchd (Mac) / cron (Linux) schedules and the always-on tech listener (watch.sh)
automation.conf  user settings for run_all.sh / schedule.sh (Kanchana edits this; see STATUS.md "Open issues")
doctor.py        browser health check: uv run python doctor.py
tests/           pytest, offline (local HTTP servers, sample data in tests/sample_data.py)
data/  output/  logs/  .local/  .venv/       git-ignored
```

- **Databases:** DuckDB, one file per tool under `data/` (worldnews, techintel, markets, vehicles, scrapers), because DuckDB
  allows only one writer per file. The dashboard opens them read-only, one query at a time, so it never
  blocks a run. **Being replaced by PostgreSQL** (decided 2026-11-05, no sync, one schema per tool): follow
  `docs/postgres-migration.md` and tick off its steps there and in STATUS.md as they're done. Already moved:
  `warehouse.PG_TOOLS` (companies, news, markets, vehicles). Tests for those use the `pg_env` fixture (a throwaway PostgreSQL).
- **Vehicle ads** are in `data/vehicles/vehicles.duckdb` (`vehicles/store.py`): tables `ads` and `runs`,
  views `v_vehicle_ads*` used by the dashboard. The latest run is also written to `output/vehicles_<site>.json`.
  `data/scrapers/scrapers.duckdb` holds only the `scrapers/` tools' output (generic `scraper_rows`).
- **Tests never touch the real `data/` or `output/`** (`tests/conftest.py` points them at a temp folder).
- **New step for automation:** add a default `STEP_<name>` / `TOOL_<name>` in `run_all.sh` (the tool
  name is the lock), and mention it in `automation.conf` and the README.
- Code style: small functions, docstrings that explain the why, friendly log and error messages that say
  what to do next. Tests for every fix.
