# Moving from DuckDB to PostgreSQL: the plan

_Written 2026-11-05. Status: **done (steps 1-4, 2026-11-05/06).** Step 5 (the server, more users) is for later. Agreed with Kanchana: PostgreSQL only, no DuckDB, no sync._

## Why

DuckDB allows one writer per file, and while a program writes, nobody else can even read the file. Our runs keep
their file open for the whole run (companies update: 10 minutes to hours). So:

- the dashboard can't read during a run: it waits 1.5 s, then shows old cached numbers or "A run is writing to the
  database" (`dashboard/server.py` `_query`);
- Kanchana can't run analysis queries while a collector runs;
- only one person, on this Mac, can use the data.

PostgreSQL lets many programs write and many people read **at the same time**: a query sees the data as it was when
it started, and nobody waits for anybody. Data a collector saves is visible straight away, so there's **no sync
step** and no second copy. Collectors keep their own schedules, separately per section.

What we give up: DuckDB is faster for big "count everything" queries (0.05-0.2 s vs about 0.5-2 s in PostgreSQL for
a few million rows, typical figures, not measured here). Indexed lookups and filters are milliseconds in both, and
heavy summaries can be precomputed (materialized views) if we ever need them.

## Where it runs

**Now: PostgreSQL 18.4 on Kanchana's Mac** (Homebrew `postgresql@18`, already installed and started by
`brew services`, port 5432). That server already has 14 other databases (other projects), so we make our **own
database `crawl4ai`** and never touch the others. Extensions we need are available: `pg_trgm` (fast name search),
`unaccent`, `pg_stat_statements`.

**Later: a server** with 100+ GB. Moving there is `pg_dump` here, `pg_restore` there, and a new connection setting.

> Local disk note: Homebrew keeps PostgreSQL's data on the Mac's own drive (`/opt/homebrew/var/postgresql@18`),
> which had **1.1 GB free** on 2026-11-05. Today's data (~145,000 rows, ~50 MB as DuckDB) fits easily. The GLEIF
> full file (3.5 million companies, a few GB in PostgreSQL) should wait for the server.

## Today's data (2026-11-05)

| DuckDB file | Tables and rows |
|---|---|
| companies | companies 105,777; sites 1,462; refusals 132; cursors 111; runs 5 |
| worldnews | news_items 4,582; news_run_items 12,569; news_source_runs 1,294; news_runs 19; feed_cache 1 |
| markets | quotes 7,608; source_runs 852; runs 18 |
| techintel | candidates 2,978; qualifications 2,978; registrations 2,685; companies 1,900; profiles 353; runs 170; changes 0 |
| vehicles | ads 545; runs 35; meta 1 |
| scrapers | scraper_rows 75 |

Small: copying everything takes seconds.

## Target design

### One database, one schema per tool

```
database crawl4ai
  schema news       (worldnews)      schema markets
  schema tech       (techintel)      schema vehicles
  schema companies                   schema scrapers
```

The schema names are the prefixes we already use in `warehouse sql` (`companies.companies`, `news.news_items`, ...),
so existing queries and habits keep working, and now one query can join across tools.

### Logins (roles)

| Role | Used by | Can |
|---|---|---|
| `crawl4ai` | collectors, `warehouse migrate`, the migration | create tables, read, write |
| `crawl4ai_reader` | dashboard, Kanchana's analysis, later other users | read only (`default_transaction_read_only = on`), `statement_timeout` 30 s so one heavy query can't hog the server |

Locally the Mac's PostgreSQL uses socket login without a password. On the server: passwords, kept out of git.

### Connection settings

- `CRAWL4AI_DATABASE_URL` (writers) and `CRAWL4AI_READER_URL` (dashboard). Defaults:
  `postgresql:///crawl4ai` (local socket, current user), so it works on the Mac with no setup.
- Server passwords go in `.local/database.env` (git-ignored, `KEY=value` lines, read by `project_paths.apply_local_env`),
  never in `automation.conf` or the code.

### Python side

- Driver: **psycopg 3** (`psycopg[binary]`, plus `psycopg_pool` for the dashboard). Added with `uv add`, so
  `uv.lock` is updated by uv, not by hand.
- `warehouse/` becomes the one place that knows the database: `connect(role="writer"|"reader")`, a `transaction()`
  helper, `copy_rows()` for fast bulk inserts (PostgreSQL COPY), and `migrate()`.
- **Short transactions:** collectors commit after each batch, as they already save after each batch. No connection
  holds locks for a whole run. The per-tool run locks (`logs/.lock-<tool>`) stay: they stop two runs of the *same*
  tool, which is still wanted.
- Dashboard: an async connection pool (aiohttp stays). The "busy" cache fallback in `_query` goes away.

### Schema files and migrations

- One file per schema, `warehouse/schema/<schema>.sql`, with its tables, sequences and indexes (`create ... if not
  exists`); views are added in step 3 together with each tool's queries. `python -m warehouse migrate` applies them
  (as the writer login, so the tables belong to `crawl4ai`); `warehouse setup` runs it too.
- A schema file runs again only when it changed: its SHA-256 is kept in `public.schema_versions`, and an advisory
  lock stops two programs running it at once. Each store calls `migrate` for its own schema when it opens (one
  quick query when nothing changed). Statements must stay safe to repeat (`if not exists`, `create or replace
  view` adding columns only at the end). A change that can't be written that way (renaming or retyping a column)
  needs its own one-off statement added to the file, guarded so it only runs once.
- Times: `timestamptz` everywhere, session time zone UTC (as the dashboard already does).
- JSON: `jsonb` (techintel keeps its "whole record as JSON + typed views" design for now).

### Indexes (first set)

| Schema | Indexes |
|---|---|
| companies | `companies(country)`, `(source)`, `(domain)`, trigram GIN on `lower(name)` for search; `sites(domain)` primary key, `(status)`, `(checked_at)` |
| news | `news_items(published_at)`, `(source)`, `(group_code)`; trigram GIN on `title` for search |
| markets | `quotes(symbol, run_at)` |
| vehicles | `ads(site, saved_at)`, `(url)` |
| tech | `(table key)` and `created_at` on each table; GIN on `data` only if queries need it |

More are added when a real query is slow (`pg_stat_statements` shows which).

### DuckDB SQL that must change

Found by searching the code (22 Python files import or use DuckDB; most SQL is in the five `store.py` files and
`dashboard/queries.py`, 547 lines):

| DuckDB (what we use now) | PostgreSQL |
|---|---|
| `?` parameters | `%s` (psycopg) |
| `insert or replace into t ...` | `insert into t ... on conflict (key) do update set ...` |
| `... qualify row_number() over (...) = 1` | `select distinct on (...)` or a subquery |
| `list(x)`, `list_contains(l, v)` | `array_agg(x)`, `v = any(l)` |
| `json` + `json_extract_string(x, '$.a')` | `jsonb` + `x ->> 'a'` |
| `strftime(ts, '%Y-%m-%d')` | `to_char(ts, 'YYYY-MM-DD')` |
| `epoch(ts)` | `extract(epoch from ts)` |
| `median(x)` | `percentile_cont(0.5) within group (order by x)` |
| `arg_min(a, b)` | `(array_agg(a order by b))[1]` or `distinct on` |
| bulk insert via `unnest($1, ... $9)` | COPY into a temp table, then `insert ... on conflict` |
| `attach 'x.duckdb' as name` (warehouse, vehicles' old-history import) | not needed: schemas in one database |
| `duckdb.connect(path, read_only=True)` | `connect(role="reader")` |

## The steps

The order Kanchana asked for: create the database, move all the data, then switch the collectors and the dashboard.
To lose nothing written in between, each tool's data is copied **again** at the moment that tool switches.

### Step 1: Create the database (on the Mac) ✅ done 2026-11-05

Built in `warehouse/postgres.py` (Python rather than a .sql file, so every step checks first and can run again):

1. `uv run python -m warehouse setup` creates what's missing: logins `crawl4ai` and `crawl4ai_reader`, database
   `crawl4ai` (owner `crawl4ai`, UTF8), extensions `pg_trgm` and `unaccent`, the six schemas (owned by `crawl4ai`),
   and the grants: the reader may use the schemas and read every table, including tables made later (default
   privileges); it is read-only (`default_transaction_read_only`) and its queries stop after 30 s. A password in
   `CRAWL4AI_DATABASE_URL` / `CRAWL4AI_READER_URL` is set on that login (for the server later).
2. `uv run python -m warehouse pg-check` and `uv run python doctor.py` check: PostgreSQL reachable (version),
   schemas, extensions, database size, the reader can't write, free disk space where PostgreSQL keeps its files.
3. Tests (`tests/test_postgres.py`, 7) run on a throwaway server (`tests/conftest.py`: `pg_server`, `pg_env`), and
   include "a reader is never blocked by a writer's open transaction".
4. Found on the way: on macOS a fresh PostgreSQL server stops at once without a valid `LC_ALL` ("postmaster became
   multithreaded during startup"); the test server sets `LC_ALL=C`.

Result on the Mac: all checks OK (database 7.8 MB, empty). Warning: 1.2 GB free on the Mac's own drive.

### Step 2: Copy all the data (DuckDB stays the live database) ✅ done 2026-11-06

1. `warehouse/schema/{news,tech,markets,vehicles,companies,scrapers}.sql`: the same tables and columns as the DuckDB
   files (VARCHAR → text, DOUBLE → double precision, JSON → jsonb, VARCHAR[] → text[]), plus the indexes above.
   techintel's plain `created_at TIMESTAMP` (the Mac's local time) became `timestamptz`.
2. `uv run python -m warehouse copy-from-duckdb [--tool companies] [--replace] [--check]`
   (`warehouse/copy_duckdb.py`):
   - opens each DuckDB file read-only (if a run is writing to it, says so after 10 s and skips that tool);
   - loads each table with COPY, one transaction per tool: if anything fails or doesn't match, nothing of that tool
     is kept;
   - refuses to copy into a table that already has rows, unless `--replace` (empties it first, safe to repeat);
     `--check` only compares;
   - removes what PostgreSQL can't store (the NUL character in text, `\u0000` in JSON) and counts it in the report;
   - sets each sequence to the highest copied id;
   - prints a check per table: rows and the newest time in DuckDB vs PostgreSQL.
3. The DuckDB files are only read, never changed.
4. Tests: `tests/test_copy_duckdb.py` (6). They make each tool's DuckDB file with the tool's own store, so a table
   or column added to a tool later without adding it to `warehouse/schema/` makes the test fail.

**Result on the Mac (2026-11-06):** all 25 tables copied, every count and newest time the same, ~9 s in all
(companies 105,777 rows in 7 s). No NUL characters needed removing. The same questions gave the same answers in
both: companies per country and per source, websites by status, newest headlines, headlines per country, cheapest
vehicle ads, tech qualifications by status (a JSON field), latest prices. Each took 1-46 ms in PostgreSQL; name
search "%bank%" over 105,777 companies: 10 ms.

**Note for step 3:** text sorts differently. The database sorts like a dictionary (`en_US.UTF-8`: `^GSPC` comes with
the G's), DuckDB by character code (`^GSPC` after `Z`). The same rows, another order. Where exact order matters, use
`order by x collate "C"`.

**Until a tool switches (step 3), its PostgreSQL copy gets older:** the tools still write to DuckDB. That's why
step 3 copies each tool again (`--replace`) right before it switches.

### Step 3: Switch each tool, then the dashboard (one tool at a time)

For each tool, in this order: **companies** (most lock trouble), **world news**, **markets**, **vehicles**,
**tech intel** (most code: the watch listener, JSON records), **scrapers**, then **briefing** (only reads).

1. Rewrite the tool's `store.py` for PostgreSQL (same methods, so the rest of the tool doesn't change).
2. Rewrite that tool's part of `dashboard/queries.py` and switch its API in `server.py` to the reader pool.
3. Tests pass (see below).
4. Run `copy-from-duckdb --tool <tool> --replace` right before the switch, so the newest data comes along.
5. Commit, push; Kanchana: `git pull origin main`, restart the dashboard, run the tool once, check its tab.

While this is going on, the dashboard reads PostgreSQL for tools already switched and DuckDB for the rest
(`_query` picks by tool, from `warehouse.PG_TOOLS`). No tool ever writes to both. `warehouse sql` and `info`
skip the DuckDB files of switched tools; `uv run python -m warehouse pg "..."` queries PostgreSQL.

| Tool | Switched | Notes |
|---|---|---|
| companies | ✅ 2026-11-06 | store: COPY into a temp table + one upsert per batch; view `v_companies` in the schema file; dashboard summary over 110k companies 0.4-0.6 s while collect writes |
| world news | ✅ 2026-11-06 | executemany in one transaction per run; `check` became a dry run (reads, saves nothing); `analyze` and the briefing read PostgreSQL |
| markets | ✅ 2026-11-06 | `v_latest` = `distinct on` + index (asset_class, symbol, run_at desc); `v_daily` per UTC day; `try_cast(extra->>x)` became a jsonb number check |
| vehicles | ✅ 2026-11-06 | views with window functions in subqueries (no `qualify`); `median` → `percentile_cont(0.5)`; the one-time scrapers.duckdb import dropped (its rows came with the copy) |
| tech intel | ✅ 2026-11-06 | `DuckStore` → `TechStore`; views use `distinct on` and `tech.try_ts/try_num/try_int` (`pg_input_is_valid`) for DuckDB's `try_cast`; the dashboard has no DuckDB code left |
| scrapers | ✅ 2026-11-06 | `record_output` in one transaction; views in the schema file; `warehouse sql` now runs on PostgreSQL (`pg` is the same) |
| briefing | ✅ 2026-11-06 | only reads: switched part by part with each tool |

How the switch is done (companies as the example): the store keeps its methods and `self.con` (a writer
connection, autocommit, `timezone` UTC); placeholders `%s`; every table name with its schema; jsonb comes back
decoded (dicts/lists), so code that did `json.loads` accepts both; values go through `postgres.clean_text` /
`postgres.json_text` (no NUL). Tests take the `pg_env` fixture and call `postgres.setup()`. The tool's DuckDB
layout is kept in `tests/test_copy_duckdb.py` so the copy test still covers it.

**Done when (each tool):** a run of the tool and the dashboard tab work at the same time with no "busy" message;
`warehouse sql` gives the same numbers as before the switch plus the new run.

### Step 4: Clean up ✅ done 2026-11-06

- `warehouse sql`, `info`, `examples` and the prompt run on PostgreSQL with the read-only login (`pg` = `sql`).
- DuckDB code removed: `warehouse.connect` and its lock waiting, `attach`, `DB_FILES`/`PG_TOOLS` (now
  `warehouse.DATA_DIRS`, folders only), the vehicles history import, the dashboard's busy fallback, the briefing's
  DuckDB path, and the copy tool `copy-from-duckdb` with its tests (its job is done). `duckdb` removed from
  pyproject.toml / uv.lock. (techintel's FileStore stays: it reads old JSON Lines files and has no DuckDB in it.)
- The DuckDB files moved to `data/archive/duckdb-2026-11/<tool>/` (kept, not deleted; README.txt there says how to
  open one with `uvx duckdb -readonly`).
- New run_all step `backup` (last in the default STEPS): `uv run python -m warehouse backup --keep 7`, a `pg_dump`
  custom-format file in `data/backups/`. Checked: a restore into a temporary database matched every table.
- README, CLAUDE.md, STATUS.md describe PostgreSQL.

### Step 5: Later

- Move to the server: `pg_dump` / `pg_restore`, `CRAWL4AI_DATABASE_URL` points there, collectors run there.
- Logins for other users (each their own read-only role), access only over a private network or SSH tunnel.
- GLEIF full file (3.5 million companies) once on the server.
- Materialized views for heavy dashboard summaries, refreshed `concurrently` after runs, if a page gets slow.
- Schedules per section (tech quick and vehicles every 15 min, markets/news hourly, companies daily, ...).

## Tests

Tests stay offline and never touch the real database:

- `tests/conftest.py` starts a **throwaway PostgreSQL** once per test session: `initdb` into a temp folder,
  `pg_ctl start` with its socket in that folder (Homebrew's binaries; about 1-2 s). Each test gets a database and
  logins of its own (`pg_env`: new names per test) and `CRAWL4AI_*_URL` pointing at them.
- If PostgreSQL's programs aren't found, the tests stop with one line saying how to install them.
- New tests: the DuckDB → PostgreSQL copy (counts and timestamps match, `--replace` is safe to repeat); a reader
  query works while a writer transaction is open (the whole point); the reader role can't write.
- Goal: the full suite still runs in about 30 s.

## Rollback

Until step 4, each tool's DuckDB file stays exactly as it was when that tool switched. If a switched tool goes wrong:
`git revert` its commit, and it uses its DuckDB file again (missing only the runs made after the switch, which can be
copied back from PostgreSQL if needed). Before step 4 starts, a `pg_dump` backup is made.

## Rough size of the work

| Step | Work |
|---|---|
| 1 | setup script, roles, doctor check: small |
| 2 | schema files for 6 tools + copy command with checks: medium |
| 3 | 5 `store.py` files, 547 lines of dashboard queries, `warehouse`, tests: the biggest part, one tool per commit |
| 4 | cleanup and docs: small |
