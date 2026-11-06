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
- After a change, tell Kanchana exactly what to run, e.g. `git pull origin main`, and `uv run python -m webapp migrate`
  if there's a migration (the web app's server restarts by itself).
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
techintel/  worldnews/  markets/  vehicles/  companies/   one package per tool, each with its own PostgreSQL schema:
                                               __init__.py (calls project_paths.apply_local_env()), __main__.py,
                                               cli.py, store.py, config/, README.md. Run: python -m <tool> <command>
scrapers/        older single-file scrapers (Sri Lankan news, products, IMDb, CSE) + common.py helpers
warehouse/       PostgreSQL: postgres.py (connect, setup, migrate, check), schema/<schema>.sql, python -m warehouse;
                 edits.py = people's corrections and rule A (what can be edited, and how collectors respect it)
webapp/          the user platform (Django + HTMX): python -m webapp runserver 8060; its tables in schema `app`.
                 catalog/ = automatic list of datasets and fields; explore/ = one generic grid page for all of them;
                 insights/ = the dashboard's five tabs (queries.py is the SQL, also used by the tools' `show` commands)
run_all.sh       run steps/profiles once, with per-tool locks and logs in logs/
schedule.sh      launchd (Mac) / cron (Linux) schedules and the always-on tech listener (watch.sh)
automation.conf  user settings for run_all.sh / schedule.sh (Kanchana edits this; see STATUS.md "Open issues")
doctor.py        browser health check: uv run python doctor.py
tests/           pytest, offline (local HTTP servers, sample data in tests/sample_data.py)
data/  output/  logs/  .local/  .venv/       git-ignored
```

- **Database: PostgreSQL** (since 2026-11-06; it replaced one DuckDB file per tool, which allowed only one
  program at a time). Database `crawl4ai` on the Mac's Homebrew PostgreSQL 18, one schema per tool: news, tech,
  markets, vehicles, companies, scrapers. Logins `crawl4ai` (writer) and `crawl4ai_reader` (read-only, 30 s query
  limit); settings `CRAWL4AI_DATABASE_URL` / `CRAWL4AI_READER_URL` / `CRAWL4AI_ADMIN_URL` or `.local/database.env`.
  Each store opens a writer connection, runs `postgres.migrate` for its schema (only when the .sql file changed)
  and saves in short transactions; the dashboard tabs and CLIs read with the reader login. Placeholders are `%s`,
  table names always with the schema, text through `postgres.clean_text`/`json_text` (no NUL). New tables or
  views go in `warehouse/schema/<schema>.sql` (keep every statement safe to repeat). History of the move and
  what's left (step 4, clean up): `docs/postgres-migration.md`.
- **Next big piece: the user platform** (`docs/user-platform-plan.md`): Django + HTMX + Plotly + WeasyPrint, Python
  only (Kanchana decided against React), the Tabulator grid behind `datagrid.js`, roles and menu
  rights, edit history with rule A. Follow its phases and update it as decisions change.
- **Corrections:** a collector whose data people can edit must save through `warehouse/edits.py` (`protect_rows` or
  `protect_staged`), or the next run undoes their work. Schema `audit` isn't a data schema: the reader login can't see it.
- **Vehicle ads** are in schema `vehicles` (`vehicles/store.py`): tables `ads` and `runs`, views
  `v_vehicle_ads*` used by the dashboard. The latest run is also written to `output/vehicles_<site>.json`.
  Schema `scrapers` holds only the `scrapers/` tools' output (generic `scraper_rows`).
- **Tests never touch the real `data/`, `output/` or PostgreSQL** (`tests/conftest.py`: a temp folder, and every
  database address points nowhere unless a test takes the `pg_env` fixture, a throwaway PostgreSQL server).
- **New step for automation:** add a default `STEP_<name>` / `TOOL_<name>` in `run_all.sh` (the tool
  name is the lock), and mention it in `automation.conf` and the README.
- Code style: small functions, docstrings that explain the why, friendly log and error messages that say
  what to do next. Tests for every fix.
