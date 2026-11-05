# Moving from DuckDB to PostgreSQL: the plan

_Written 2026-11-05. Status: **planned, not started.** Agreed with Kanchana: PostgreSQL only, no DuckDB, no sync._

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
- Server passwords go in `.local/database.env` (git-ignored, read by `env.sh` and `project_paths.apply_local_env`),
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

- Each tool gets `<tool>/schema.sql` with its tables, indexes and views (`create ... if not exists`).
- Later changes are numbered files `<tool>/migrations/NNN_what.sql`; a `public.schema_versions` table remembers
  which ran. `python -m warehouse migrate` applies what's new (also run by `warehouse init` and at the start of
  `run_all.sh`).
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

### Step 1: Create the database (on the Mac)

1. `create role crawl4ai login;` `create role crawl4ai_reader login;` `create database crawl4ai owner crawl4ai;`
2. In `crawl4ai`: `create extension pg_trgm; create extension unaccent;` the six schemas; grants (reader: `usage` on
   the schemas, `select` on all tables, and the same for future tables via `alter default privileges`).
3. All of this as a script, `warehouse/setup.sql`, run by `python -m warehouse setup` (safe to run again).
4. `doctor.py` checks: PostgreSQL reachable, version, our database and schemas exist, the reader can't write,
   free disk space.

**Done when:** `uv run python -m warehouse setup` and `uv run python doctor.py` say OK, and
`psql crawl4ai -c '\dn'` lists the six schemas.

### Step 2: Copy all the data (DuckDB stays the live database)

1. Write every tool's `schema.sql` (tables as they are today, plus the indexes above).
2. `python -m warehouse copy-from-duckdb [--tool companies] [--replace]`:
   - opens each DuckDB file read-only (says so and stops if a run is writing to it), reads each table, and loads it
     with COPY;
   - `--replace` empties the PostgreSQL table first, so it can be run again and again;
   - sets each sequence to the highest copied id;
   - prints a check per table: rows in DuckDB vs PostgreSQL, newest timestamp in each. Any difference = failed.
3. The DuckDB files are only read, never changed.

**Done when:** the copy reports equal counts for every table, and the same question gives the same answer in both,
e.g. companies per country, latest market prices, newest headlines.

### Step 3: Switch each tool, then the dashboard (one tool at a time)

For each tool, in this order: **companies** (most lock trouble), **world news**, **markets**, **vehicles**,
**tech intel** (most code: the watch listener, JSON records), **scrapers**, then **briefing** (only reads).

1. Rewrite the tool's `store.py` for PostgreSQL (same methods, so the rest of the tool doesn't change).
2. Rewrite that tool's part of `dashboard/queries.py` and switch its API in `server.py` to the reader pool.
3. Tests pass (see below).
4. Run `copy-from-duckdb --tool <tool> --replace` right before the switch, so the newest data comes along.
5. Commit, push; Kanchana: `git pull origin main`, restart the dashboard, run the tool once, check its tab.

While this is going on, the dashboard reads PostgreSQL for tools already switched and DuckDB for the rest
(`_query` picks by tool). No tool ever writes to both.

**Done when (each tool):** a run of the tool and the dashboard tab work at the same time with no "busy" message;
`warehouse sql` gives the same numbers as before the switch plus the new run.

### Step 4: Clean up

- `warehouse sql` / `repl` connect to PostgreSQL (or just use `psql crawl4ai`).
- Remove the DuckDB code paths: `warehouse.connect` lock waiting, `attach`, the vehicles history import, techintel's
  FileStore import, the dashboard's busy fallback.
- Move the DuckDB files to `data/archive/duckdb-2026-11/` (kept as a backup, not deleted).
- Remove `duckdb` from `pyproject.toml` unless we keep it for analysis (it can read PostgreSQL directly).
- New run_all step `backup`: `pg_dump -Fc crawl4ai` to `data/backups/`, keep the last 7.
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
  `pg_ctl start` on a free port with its socket in that folder (Homebrew's binaries; about 2-3 s), then runs every
  `schema.sql`. Each test gets clean tables (truncate) and `CRAWL4AI_DATABASE_URL` pointing at it.
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
