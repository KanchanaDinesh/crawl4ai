# Web scrapers

The parts:

- **`scrapers/`**: news, products, IMDb and Colombo Stock Exchange scrapers (this page).
- **`techintel/`**: a pipeline that discovers new tech websites, profiles and monitors them, and
  writes analytics reports. See [techintel/README.md](techintel/README.md).
  Quick start: `uv run python -m techintel run`.
- **`worldnews/`**: the latest headlines from major news sites in the US, India, Sri Lanka, Australia,
  Canada, China, plus world and technology news, in one daily report.
  See [worldnews/README.md](worldnews/README.md). Quick start: `uv run python -m worldnews run`.
- **`markets/`**: Sri Lankan and US stocks and indices, gold, silver, oil, exchange rates and the crypto top 100,
  with price history. See [markets/README.md](markets/README.md). Quick start: `uv run python -m markets run`.
- **`vehicles/`**: the latest ads from ikman.lk and riyasewana.com, with new-ad and price-change history.
  See [vehicles/README.md](vehicles/README.md). Quick start: `uv run python -m vehicles run`.
- **`companies/`**: a directory of companies of every kind (US, UK, Australia, India, Sri Lanka first, then the
  world) from open data (Wikidata, GLEIF, the CSE), and what each company's own website says about it.
  See [companies/README.md](companies/README.md). Quick start: `uv run python -m companies collect --max-minutes 5`.
- **`briefing/`**: the day's text summary and a 30-second video made from all the data, offline (no AI, no tokens).
  See [briefing/README.md](briefing/README.md). Quick start: `uv run python -m briefing run`.
- **`dashboard/`**: a local web dashboard for World News, Tech Intel, Markets and Vehicles, reading live from the databases.
  `uv run python -m dashboard --open` (see [Dashboard](#dashboard)).

All of them use
[crawl4ai](https://github.com/unclecode/crawl4ai) ([docs](https://docs.crawl4ai.com/)) as an
installed library, pinned in `pyproject.toml`.

## Setup (everything stays inside this folder)

Everything lives inside the project folder, so it can sit on an external drive such as `MAC-DISK2`:

| Folder | What's in it |
|---|---|
| `.venv/` | Python packages (crawl4ai, psycopg, ...) |
| `.local/` | uv's download cache, the Chromium browser, crawl4ai's cache, and Python if uv downloads one |
| `data/` | Collected files (raw pages, run snapshots, downloads), `backups/` (daily database copies) and `archive/` (the old DuckDB files); the data itself is in PostgreSQL |
| `output/` | Reports and exported files |

One-time setup (needs [uv](https://docs.astral.sh/uv/): `curl -LsSf https://astral.sh/uv/install.sh | sh`):

```bash
bash setup_local.sh
```

Then, in each new Terminal window:

```bash
source env.sh
```

`env.sh` points uv, the browser and crawl4ai at `.local/`. The Python tools also apply the crawl4ai and
browser settings on their own, so runs still use the project folder if you forget it. Only uv's own
small program (`~/.local/bin/uv`) stays on the main drive.

## Automation: run everything with one command

```bash
./run_all.sh                       # run the default steps once (STEPS in automation.conf)
./run_all.sh --profile frequent    # a profile: tech_quick + vehicles
./run_all.sh markets vehicles      # only some steps
./run_all.sh --list                # steps, profiles and the exact commands
./run_all.sh --status              # the last runs
```

**Steps:** `markets`, `news`, `news_analyze` (biggest stories, topics, names, countries and tone from the saved headlines), `tech_collect` (find and store new tech sites, fast, no browser), `tech_company`
(company facts from each site's own pages: social links, people, employees, GitHub), `tech_update`
(profile new sites and re-check known ones, with the browser), `tech` (both in one step), `tech_quick` (new domains → qualify →
registration dates), `vehicles` (ikman.lk + riyasewana), `companies_collect` (add companies from open data, continues
where the last run stopped), `companies_update` (read company websites: social links, people, employees), `lknews`,
`briefing` (summary + video, last). **Profiles** group steps that run at
the same speed; they're defined in `automation.conf`.

### Recommended schedule

Different parts change at different speeds, so they run at different speeds:

One command sets up all three schedules below:

```bash
./schedule.sh recommended                    # = the three lines below, then shows the status
```

```bash
./schedule.sh every 15m --profile frequent   # new tech domains + new vehicle ads
./schedule.sh every 1h  --profile hourly     # markets + news + full tech intel (time-limited)
./schedule.sh watch on                       # live certificate listener: new domains within seconds
./schedule.sh status                         # everything at a glance
```

| What | How often | Why |
|---|---|---|
| Certificate listener (`watch`) | always on | New domains appear every second; the stream pushes them, so nothing is polled |
| `tech_quick` | every 10–15 min | Adds the listener's domains + Hacker News launches and qualifies them while they're new; quick because it only touches new domains |
| `vehicles` | every 10–15 min | New ads are caught soon after they're posted; every run adds to the history and price-change tracking |
| `markets`, `news` | hourly (or more often for markets) | Prices and headlines |
| `tech` (full run) | hourly | crt.sh, profiling (a browser opens up to 6 pages per site), monitoring known sites every 7 days; time-limited |

Other schedule commands: `every 2h` (default steps), `daily 07:30 19:30 [--profile NAME]`,
`off --profile frequent`, `off all`, `watch off`, `run-now [--profile NAME]`.

### How runs behave

- **Each step runs even if an earlier one failed.** The run is "ok" only if every step succeeded; a step fails if
  it crashed or collected nothing at all (e.g. no internet).
- **One lock per tool:** jobs can overlap safely. If a step's tool is still busy (say the hourly tech run is
  profiling when the 15-minute run starts), that step is skipped for this run, not failed, and the other steps run.
- **Logs:** `logs/run-<date>-<time>-<profile>.log` (`logs/latest.log` is the newest), one summary line per run in
  `logs/history.log`, listener output in `logs/watch.log`. The dashboard header shows the last run; hover for the
  history. Run logs older than 30 days are deleted.
- **The listener never opens the database.** It queues new domains in `data/techintel/stream/pending.jsonl`, and
  the next `tech_quick` adds them. If the stream drops, it reconnects (waiting up to 10 minutes between attempts).
- **Settings** are in `automation.conf`: profiles, the default steps, and options per step (for example vehicles
  takes 20 ads per site per run, and the full tech run is limited to 50 sites and 20 minutes per stage).

**On the Mac** schedules are launchd LaunchAgents (`~/Library/LaunchAgents/com.crawl4ai.*.plist`):

- They run while you're logged in and the Mac is awake; a run missed during sleep happens when it wakes. The
  listener restarts by itself if it stops. The external drive must be connected.
- **Give `/bin/bash` Full Disk Access once**, or macOS blocks scheduled runs from the external drive
  ("Operation not permitted"): open **System Settings → Privacy & Security → Full Disk Access**, click **+**,
  press ⌘⇧G, type `/bin/bash`, add it and make sure its switch is on. Then run `./schedule.sh doctor` to test.
- launchd's own messages (problems before a run can even start) go to `~/Library/Logs/crawl4ai/` on the Mac's
  drive, because launchd can't start a job whose log is on an external drive. `./schedule.sh status` reads them
  and says what to fix. The runs and the listener still write their logs to `logs/`.
- Schedules set up before 2026-11-03 kept their log on the external drive and never started: run
  `./schedule.sh recommended` once to set them up again.
- Each job starts as `bash -c "cd <project>; exec /bin/bash ./run_all.sh ..."`. A job that named the script on
  the external drive directly was blocked even with Full Disk Access for `/bin/bash` (macOS judges the script).
  Schedules set up before this change: run `./schedule.sh recommended` again, from your own Terminal.

**On a Linux server** the same commands use your crontab (other cron jobs are left untouched); the listener is
kept running by a cron check every 5 minutes and restarted at boot.

## Dashboard

```bash
uv run python -m dashboard --open        # http://localhost:8050
```

- **Full screen:** the page uses the whole width of the window; the **⛶** button (or the **F** key) switches the
  browser to real full-screen mode. Charts are plain SVG drawn by the page (no library, works offline); hover
  a line chart to see the values.
- **World News:** headlines newest first with NEW and tech badges; filters for period, country/group, source,
  tech only, new in the last run, and search. Side panels show headlines per day, **topics per day** (tech,
  crypto, markets), **when news is published** (by hour, your time), per country, top sources, **source health**
  (which sites failed, and how many runs in a row) and recent runs.
- **Markets:** cards with sparklines; **Compare**: any instruments on one chart as % change over 1 week, 1 month
  or 3 months, with quick picks (world indices, Sri Lanka, gold/silver/oil, safe havens vs risk, crypto);
  **Performance**: 1 day, 1 week, 1 month and 3 months for every index, commodity, exchange rate and the top 5
  coins, coloured green/red, with a 3-month line; sortable tables or a **map** (tiles coloured by the day's change,
  sized by market value) for indices, US stocks, all CSE companies, crypto top 100, gold/silver/oil and exchange
  rates; top gainers/losers; market and crypto news. Click any row for its chart (1D, 1W, 1M, 3M).
  History = Yahoo's daily closes (6 months, saved with each price) + every run's saved prices; CSE and crypto
  history builds up from our own runs.
- **Vehicles:** ikman.lk and riyasewana ads, newest first with photos and NEW badges; price drops ("was Rs …");
  filters for site, period, new in the last run and search; new ads per day, **price ranges**, **makes with their
  median price**, and when each site was last collected.
- **Companies:** the company directory: counts per country and source, companies added per day, website status,
  social accounts and industries; filters for country, source, website status (read, has LinkedIn, names its people,
  ...) and search. The list scrolls inside its panel and loads more by itself; **click a row** for the company's
  details from its source (registration number, sector, address, ...) and what its own website says (social
  accounts, people, employees, emails, phones). Side panels show the sources, collection progress and recent runs.
- **Tech Intel:** a compact list, one line per site (name, domain, description, status, category, score bar,
  **domain age** with a NEW badge for domains under 30 days old, source), sortable by latest seen, highest score,
  **newest domain** or name; it scrolls inside its panel with the headings kept in view. Charts on top: found and
  qualified per day, **how new the qualified sites' domains are**, categories, and the status split. Filters for
  period, status, category, source and search. **Click a row** for the full profile: tech stack, signals, key
  pages, changes and check history. Side panels show tech stack, sources, recent changes and the pipeline's runs.
- Website links open in a new tab. Optional auto-refresh every minute. After an update the page always loads the
  new script (no stale browser cache), but the server itself must be restarted.
- The databases are opened only for each query, so the dashboard never blocks a collection run. While a run
  is writing, it shows the last loaded data with a notice.
- It listens on `127.0.0.1` (this computer only). Use `--port` to change the port.

## Testing everything

```bash
source env.sh                                        # each new Terminal window

uv run pytest                                        # automated tests (no internet needed)

uv run python -m worldnews check                     # 1. test every news source (3 headlines each)
uv run python -m worldnews run                       # 2. collect the latest 10 per source into the database
uv run python -m techintel collect                  # 3. find and store new tech sites (no browser)
uv run python -m techintel update --limit 30         #    profile them and re-check known ones (browser)
uv run python -m markets run                         #    stocks, indices, gold/silver, FX, crypto top 100
uv run python -m warehouse info                      # 4. check the databases filled up
uv run python -m dashboard --open                    # 5. look at it all in the browser
```

Or run steps 2–3 (and markets) together with `./run_all.sh`. They can also run in separate Terminal windows
at the same time. Leave the
dashboard running and press **Refresh** (or turn on auto-refresh) to see new data as runs finish.

## Database (PostgreSQL)

Everything is in one PostgreSQL database, `crawl4ai` (on the Mac: Homebrew's PostgreSQL 18), with one schema per
tool. Collectors write and the dashboard and anyone else read at the same time; nobody waits. (Until 2026-11-06
each tool had its own DuckDB file, which allowed only one program at a time; those files are kept in
`data/archive/duckdb-2026-11/`. See `docs/postgres-migration.md`.) Tables, indexes and views are in
`warehouse/schema/<schema>.sql`.

| Schema | Contents |
|---|---|
| `news` | `news_items` (every headline, first/last seen, times seen), `news_runs`, `news_source_runs` (source health), `news_run_items`, `feed_cache` |
| `tech` | `candidates`, `qualifications`, `registrations`, `profiles`, `changes`, `companies`, `runs`, plus typed views `v_candidates`, `v_qualifications`, `v_registrations`, `v_profiles`, `v_changes`, `v_companies` |
| `markets` | `quotes` (every price from every run), views `v_latest`, `v_daily`; `runs`, `source_runs` |
| `vehicles` | `ads` (every ad from every run), `runs` (per site: ok?, ads, seconds, error), views `v_vehicle_ads`, `v_vehicle_ads_latest`, `v_vehicle_price_changes` |
| `companies` | `companies` (one row per company per source: name, country, website, industry), `sites` (what each company website says), `refusals`, `cursors` (where each source stopped), `runs`, view `v_companies` |
| `scrapers` | `scraper_rows` (every output of the `scrapers/` tools, every run), views `v_news_headlines`, `v_products` |

Two logins: `crawl4ai` (the collectors) and `crawl4ai_reader` (read-only, queries stop after 30 s). Set up or check:
`uv run python -m warehouse setup`, `uv run python -m warehouse pg-check`. Query with the `warehouse` command
(read-only login), prefixing tables with the schema, or `psql -U crawl4ai_reader crawl4ai`:

```bash
uv run python -m warehouse info                    # sizes, tables, row counts
uv run python -m warehouse examples                # ready-made queries
uv run python -m warehouse sql "select published_at, source, title from news.news_items order by published_at desc limit 20"
uv run python -m warehouse sql "select * from vehicles.v_vehicle_price_changes" --csv price_changes.csv
uv run python -m warehouse sql                     # interactive prompt
```

**Backups:** the `backup` step (last in the default `./run_all.sh` steps) saves a full copy with `pg_dump` in
`data/backups/crawl4ai-<date>-<time>.dump` and keeps the newest 7 (`BACKUP_ARGS` in automation.conf). By hand:
`uv run python -m warehouse backup`. To restore one (it replaces what's in the database):
`pg_restore --clean --if-exists -d crawl4ai data/backups/crawl4ai-<date>-<time>.dump`

Scraper output files (`output/*.json`, `.csv`) are still written as before; each save is also added to the
database, so results build up into a history (for example vehicle price changes). Data saved by older
versions as `.jsonl` files is imported automatically the first time.

## Running

Run everything from the repository root as modules:

| Command | What it does | Output (in `output/`) |
|---|---|---|
| `python -m scrapers.news` | Headlines from the enabled sites in `scrapers/config/news_sites.json` | `news.json` |
| `python -m scrapers.news --list` | Show the configured sites and whether each is on | – |
| `python -m scrapers.news --site "Daily Mirror" --site CBSL` | Only these sites, even if disabled | `news.json` |
| `python -m scrapers.news --all` | Include disabled sites (broadcom, veeam, fastcompany) | `news.json` |
| `python -m scrapers.products` | Product listings from `scrapers/config/product_sites.json` | `products_*.json` |
| `python -m scrapers.imdb` | IMDb Top 250 + release calendar, merged into the existing file | `imdb.json` |
| `python -m scrapers.cse summary` | CSE daily market summary (ASPI, S&P SL20, turnover) | `cse_market_summary.json` |
| `python -m scrapers.cse trades` | CSE per-company trade summary | `cse_trade_summary.csv` |
| `python -m scrapers.inspect_site <url\|site name\|file.html>` | Find or test CSS selectors for a new site | `html_dumps/` with `--save-html` |

Prefix each command with `uv run` (e.g. `uv run python -m scrapers.news`), or activate the
virtualenv first with `source .venv/bin/activate`. Run `source env.sh` first in each new Terminal window.

`output/` is git-ignored. Set `SCRAPERS_OUTPUT_DIR` to write somewhere else.

## Layout

```
pyproject.toml           dependencies (crawl4ai version pinned here)
env.sh, setup_local.sh   keep tools and caches inside this folder (see Setup)
run_all.sh, schedule.sh  run everything once / on a schedule (see Automation); settings in automation.conf
watch.sh                 the live tech-intel listener (normally via ./schedule.sh watch on)
project_paths.py         where data/, .local/ and the databases live
warehouse/               PostgreSQL: connections, setup and checks (postgres.py), tables (schema/), the `python -m warehouse` query tool
webapp/                  the user platform (Django): sign-in, roles, menus, audit log, grids (docs/user-platform-plan.md)
dashboard/               local web dashboard (python -m dashboard)
techintel/ worldnews/ markets/ vehicles/ companies/   the other tools (python -m <name>), each with its own README
data/                    databases and collected data (git-ignored)
output/                  everything the scrapers write (git-ignored)
scrapers/
├── common.py            shared paths, logging, JSON/CSV saving
├── config/
│   ├── news_sites.json  news sites + selectors (toggle with "enabled")
│   └── product_sites.json  product sites + crawl4ai extraction schemas
├── news.py              news headline scraper
├── products.py          product listing scraper
├── imdb.py              IMDb Top 250 + calendar
├── cse.py               Colombo Stock Exchange API client
├── inspect_site.py      selector-finding helper
└── examples/            small crawl4ai feature demos (markdown, PDF/screenshot, raw HTML)
```

## Adding a site

**News site**
1. Find the selectors:
   `python -m scrapers.inspect_site https://example.lk/ --save-html`
   With no selectors, it lists the likely headline links and the most common parent elements.
2. Check your guess:
   `python -m scrapers.inspect_site https://example.lk/ --base "div.story" --title "h3 a" --link "h3 a"`
3. Add an entry to `scrapers/config/news_sites.json` and run `python -m scrapers.news --site <name>`.
   The `--base`/`--title`/`--link` options map to `base_selector`/`title_selector`/`url_selector` in the config.

**Vehicle ad site**: see [vehicles/README.md](vehicles/README.md#adding-a-site).

**Product site**: add an entry with a crawl4ai `schema` to `scrapers/config/product_sites.json`
([schema docs](https://docs.crawl4ai.com/extraction/no-llm-strategies/)).

## News output format

Each item in `output/news.json`:

```json
{
  "title": "Headline text",
  "url": "https://www.dailymirror.lk/full/article/link",
  "name": "Daily Mirror",
  "source_url": "https://www.dailymirror.lk/",
  "domain": "dailymirror.lk",
  "fetch_date": "2026-10-31 09:00:00",
  "score": 0,
  "item_number": 1,
  "country": "LK",
  "category": "tech"
}
```

`url` is the article link, made absolute. The old scripts overwrote it with the homepage URL. The
homepage is now in `source_url`. Duplicate article URLs are dropped. `country` and `category` are set
per site in the config.

## Updating crawl4ai

Change the version in `pyproject.toml` (for example `"crawl4ai==0.9.6"`), then:

```bash
source env.sh
uv sync
uv run crawl4ai-setup        # in case the new version needs a newer browser
```

Check the [release notes](https://github.com/unclecode/crawl4ai/releases) for breaking changes first.

## Troubleshooting: "browser has been closed"

If a browser-based step (vehicles, tech intel profiling, the news browser fallback) fails with
`Target page, context or browser has been closed`, run the health check:

```bash
source env.sh
uv run python doctor.py
```

It checks that Chromium starts (plain, with the vehicles page settings, and on a page that needs graphics) and that the
drive holding it supports symbolic links, then tells you the fix. `--verbose` also shows Chromium's own messages.

**Works by hand but fails in scheduled runs?** Scheduled runs start from launchd, which has a different
environment and different permissions than Terminal. Run the same check inside the scheduler; it takes about
a minute:

```bash
./schedule.sh doctor
```

It prints the verdict and any Chromium crash messages, and saves everything to `logs/doctor-scheduled.log`. The usual ones are below.

- **External drive is exFAT/FAT:** Chromium on macOS can't run from it. Set `BROWSERS_ON_SYSTEM_DRIVE=1` in
  `automation.conf`, then run `source env.sh && uv run crawl4ai-setup`. Only the browser (about 500 MB) moves to
  the Mac's own drive; data and databases stay where they are.
- **Broken or mismatched browser:** run
  `source env.sh && rm -rf "$PLAYWRIGHT_BROWSERS_PATH"/chromium* && uv run crawl4ai-setup`.
- **"GPU process isn't usable. Goodbye."** (the doctor's "graphics page" check fails): Chromium's separate
  graphics process crashes and takes the browser with it. Simple pages don't start that process; real
  websites do. Every tool starts Chromium with `--in-process-gpu` (graphics inside the browser process) to
  avoid it. If the check still fails, try `CHROMIUM_ARGS="--in-process-gpu --disable-gpu-sandbox"` in
  `automation.conf`.

The scrapers also restart a crashed browser and retry once by themselves.

**Vehicles seems stuck:** a site gets at most 120 seconds (the log says `gave up after 120s`), and the
browser gets 60 seconds to start, so a run can't hang forever. Each site logs how long it took
(`ikman: 20 ads in 14.2s`). A normal run takes about 10 to 30 seconds per site. If ikman keeps
giving up, try a shorter limit (`--max-seconds 60`). The browser is plain (no stealth or "magic" mode):
if a site starts blocking it, run less often rather than working around it.
