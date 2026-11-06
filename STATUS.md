# Project status

_Last updated: 2026-11-06. Update this file whenever something meaningful changes._

## In one paragraph

A personal data-collection system on Kanchana's Mac (`/Volumes/MAC-DISK2/crawl4ai`). It collects:
- **Tech intel:** new tech websites, found from certificate logs and Hacker News, then classified, dated,
  profiled and monitored.
- **World news:** the latest headlines from about 80 news sites.
- **Markets:** CSE and US stocks, indices, gold and silver, FX, and the crypto top 100.
- **Vehicles:** the latest ads from ikman.lk and riyasewana.com.
- **Companies:** a directory of companies of every kind (US, UK, AU, IN, LK first, then the world) from open data,
  plus what each company's own website says (social links, people, employees, contacts).

Everything is saved in one PostgreSQL database, `crawl4ai` (one schema per tool; since 2026-11-06, before that
DuckDB files), backed up by the `backup` step, and shown in a web app with sign-in (dashboard, grids). Collectors write
while the web app and others read. It runs by hand (`./run_all.sh`) or on a schedule (`./schedule.sh recommended`).
All 564 tests pass.

## Parts and how to run them

Run every command from the project folder, after `source env.sh`.

| Part | Command | Status |
|---|---|---|
| Tech intel | Two parts: `uv run python -m techintel collect` (discover, qualify, enrich: fast, no browser) and `update` (profile, monitor, report: browser). `run` = both. `company` = company facts (social links, people, employees, GitHub). `quick` is the fast run; `watch` is the live listener | Working, but the browser crashes in Kanchana's Terminal runs (see Open issues). run_all.sh steps `tech_collect` + `tech_update`. **PostgreSQL** (schema `tech`, since 2026-11-06) |
| World news | `uv run python -m worldnews run`, `check`, `list`, `add HOMEPAGE --name N --group CODE`, `analyze [--days N] [--group LK]` | Working. Groups: US, IN, LK (priority), then AU, CA, CN, GLOBAL, TECH, MARKETS, CRYPTO. Sources: `worldnews/config/sources.json`. **PostgreSQL** (schema `news`, since 2026-11-06) |
| Foresight | `uv run python -m foresight run`, `test`, `show`, `score` | New 2026-11-06, phase 1: Markets only, no AI. Forecasts with odds, saved first and scored afterwards. On the past year StochRSI zones gave no edge. **PostgreSQL** (schema `foresight`) |
| Markets | `uv run python -m markets run`, `check`, `show`, `history SYMBOL`; `closes` (daily closes + Stochastic RSI), `stochrsi [--class] [--zone]` | Working: 50 of 50 sources, 2s. Yahoo first; Stooq now shows a bot check, so only FX has a backup (open.er-api.com). CoinGecko first, CoinPaprika as backup. Stochastic RSI for the CSE and crypto (item 59), run_all step `markets_closes`. **PostgreSQL** (schema `markets`, since 2026-11-06) |
| Vehicles | `uv run python -m vehicles run`, `show [--new] [--search X]`, `changes`, `sites` | Working: Kanchana confirmed 5 ads from ikman. Became its own module on 2026-11-02 (was `scrapers.vehicles`). **PostgreSQL** (schema `vehicles`, since 2026-11-06), with all the older history |
| Companies | `uv run python -m companies collect [--max-minutes 5] [--country LK]`, `update`, `show`, `stats`, `sources` | New 2026-11-05. Sources on: Wikidata, GLEIF, CSE; off: Companies House (UK), ASIC (AU, manual download), SEC (needs a contact email). **PostgreSQL** (schema `companies`, since 2026-11-06). run_all steps `companies_collect`, `companies_update` |
| Sri Lankan news | `uv run python -m scrapers.news` (step `lknews`) | Working. The original scraper |
| Web app (new) | `uv run python -m webapp runserver 8060` → http://localhost:8060; `migrate`, `createadmin`, `catalog_scan` | The user platform, phases 1-7: sign-in, users, roles, menu rights, audit log, automatic catalog, every dataset in the grid with filters, saved views and CSV/Excel, the dashboard, correcting companies and vehicle ads (history, rule A, hide), own tables, own fields, links, lookups, rollups and imports, charts, dashboards and PDF reports, and the AI assistant (needs an API key). Django; schema `app`. The server restarts by itself after a code change |
| Dashboard | In the web app: http://localhost:8060/dashboard/ (`webapp/insights/`) | Tabs: World News (with analysis), Tech Intel, Markets, Vehicles, Companies. Charts in Plotly. `python -m dashboard` (the old aiohttp page on port 8050) only says where it went |
| Database queries | `uv run python -m warehouse info`, `sql "..."` (read-only login), `examples`; `setup`, `pg-check`, `backup` | PostgreSQL. Prefix tables with the schema: `news.`, `tech.`, `markets.`, `vehicles.`, `companies.`, `scrapers.`. Or `psql -U crawl4ai_reader crawl4ai` |
| Health check | `uv run python doctor.py [--verbose]`; `./schedule.sh doctor` runs it inside the scheduler | Checks that Chromium starts and that the drive supports symlinks |

## Automation (the agreed plan)

The parts are scheduled at different speeds. Tech intel runs all the time, in three layers:

| Layer | What runs | How often | What it does |
|---|---|---|---|
| Live listener | `techintel watch` (via `watch.sh`) | always on | Listens to the certificate stream and queues new domains within seconds to `data/techintel/stream/pending.jsonl`. It never opens the database |
| Profile `frequent` | `tech_quick` + `vehicles` | every 15 min | `techintel quick`: takes the queued domains plus Hacker News, then qualifies them and finds their registration dates (RDAP). Also gets new vehicle ads |
| Profile `hourly` | `markets` + `news` + `tech` | every hour | The full tech run (crt.sh, profiling, monitoring) plus prices and headlines |

Set all three up with one command, then check them:

```bash
./schedule.sh recommended
./schedule.sh status
```

- **Mac:** schedules are launchd LaunchAgents (`~/Library/LaunchAgents/com.crawl4ai.*`). They run while
  Kanchana is logged in and the external drive is connected; a run missed during sleep happens on wake.
- **Needs Full Disk Access for `/bin/bash`** (System Settings → Privacy & Security), or macOS blocks
  background jobs from the external drive ("Operation not permitted"). `./schedule.sh status` says so.
- launchd's own log is in `~/Library/Logs/crawl4ai/` (on the Mac's drive), and the jobs start in `$HOME`:
  launchd can't start a job whose folder or log is on an external drive (exit 78, no message anywhere).
- **Turning them off:** `./schedule.sh off all` and `./schedule.sh watch off`.
- **Logs:** `logs/latest.log`, `logs/history.log`, `logs/watch.log`; also `./run_all.sh --status`.
- **Overlapping runs:** each tool has a lock (`logs/.lock-<tool>`). If a tool is still busy, that step is
  skipped for that run, not failed.

**State on the Mac (2026-11-03):** Kanchana gave `/bin/bash` Full Disk Access, and `./schedule.sh doctor` (run from
their Terminal) said "All good" inside launchd. The listener and run jobs were still blocked, because they named
the script on the drive directly (see history item 20).

**Decision (2026-11-03): Kanchana doesn't want scheduling for now.** They run everything by hand with
`./run_all.sh` (default steps: markets news tech vehicles). Don't push scheduling; the launchd fix (item 20) is
in the code but was never confirmed on the Mac. To turn off the jobs still installed:
`./schedule.sh off all && ./schedule.sh watch off`.

## History (what was done, oldest first)

1. Analysed the fork, merged upstream crawl4ai v0.9.5, then made it a standalone project with only our
   code. crawl4ai is now an installed library.
2. Fixed a `uv.lock` with merge-conflict markers (twice), and the riyasewana selectors (new `li.v-card` layout).
3. Vehicle ads scraper for ikman.lk and riyasewana.com: the latest N ads, with riyasewana sorted by ad id.
4. Tech intel pipeline, a prototype without any AI API. Reports list the latest sites first, with dates
   and links; RDAP registration dates were added.
5. World news collector: the latest N headlines per source, a priority order, and an `add` command for sites.
6. DuckDB storage for everything, kept inside the project folder on the external drive (`env.sh`,
   `setup_local.sh`).
7. Dashboard for news and tech, later with Markets and Vehicles tabs.
8. Sped up tech intel: live progress, faster qualify (73s down to 18s), saves after each site, safe Ctrl+C.
9. Markets module.
   - Yahoo returned blank data: browser impersonation was refused, so we use an honest user agent,
     back off, and fall back to Stooq and open.er-api.com.
10. `run_all.sh` + `schedule.sh` for manual and scheduled runs.
    - Profiles, per-tool locks, logs, the live listener and the vehicles tab.
11. Browser errors ("Target page, context or browser has been closed").
    - Added `doctor.py` and the `BROWSERS_ON_SYSTEM_DRIVE` switch (for exFAT drives).
    - The vehicles and tech scrapers now restart the browser and retry once.
12. The vehicles scraper looked stuck. It now has hard time limits: 120s per site, 60s for the
    browser to start. Each site logs how long it took.
13. The dashboard says to restart the server when the page is newer than the server (an HTTP 404 from `/api/`).
14. Vehicles became its own module, `python -m vehicles`, laid out like the others.
15. Added `./schedule.sh recommended`, plus this STATUS.md and CLAUDE.md for handing over between sessions.
16. Vehicles got its own database, `data/vehicles/vehicles.duckdb`, like the other tools.
    - Before, its ads were in `data/scrapers/scrapers.duckdb` and Kanchana couldn't find them in `data/`.
    - The old history is copied in once, automatically. The dashboard and `warehouse sql` (`vehicles.` prefix) use it.
    - Tests now always use a temp folder.
17. A scheduled `frequent` run showed `vehicles FAILED(exit 1)` after about 3s (tech_quick was fine).
    Failure reasons are now visible:
    - `run_all.sh` adds the step's last error line to the summary and to `logs/history.log`.
    - Vehicles says in one line why the browser couldn't start, or why each site gave no ads.
18. Added `./schedule.sh doctor` to run the health check inside the scheduler.
    - `doctor.py` now prints its environment, has `--verbose` (Chromium's own output), and also tests the
      vehicles page settings (magic mode).
19. Found why scheduled runs never worked (2026-11-03). No scheduled job had ever run:
    - launchd can't start a job whose WorkingDirectory or log file is on the external drive: exit 78 and no
      message (`launchctl print gui/$(id -u)/com.crawl4ai.techwatch` showed it). Jobs now start in `$HOME` and
      launchd's log is in `~/Library/Logs/crawl4ai/`. `run_all.sh` and `watch.sh` write their own logs to `logs/`.
    - Once started, bash is blocked from the drive ("Operation not permitted") until `/bin/bash` has Full Disk
      Access. `run_all.sh` and `watch.sh` now say so in one line, and `./schedule.sh status` / `doctor` print the fix.
    - Runs now write a "started" line to launchd's log, so `status` stops warning once one gets in.
    - The "vehicles FAILED after 3s" run (03:38) was actually a manual run with a broken Chromium install
      (`icudtl.dat not found`, then "GPU process isn't usable"). The browser was reinstalled at 03:47, and vehicles
      has worked by hand since.
20. Even with Full Disk Access for `/bin/bash`, jobs started as `/bin/bash /Volumes/.../watch.sh` got "Operation
    not permitted" reading their own script, while the doctor job (`bash -c "cd ...; uv run ..."`) worked. macOS
    seems to judge a script named in the job instead of `/bin/bash`. All jobs now start as
    `bash -c "cd <project>; exec /bin/bash ./script ..."`, and `status` flags jobs in the old format.
    - Claude's shell can't install launchd jobs (blocked by Claude Code's permission check); Kanchana runs
      `./schedule.sh` from Terminal.
21. Tech intel split into two parts (Kanchana's idea), plus browser-crash handling (2026-11-03):
    - `techintel collect` (discover → qualify → enrich) and `techintel update` (profile → monitor → report);
      run_all.sh steps `tech_collect` and `tech_update` are the default instead of `tech`.
    - Fetcher: a crashed browser gives `browser_failed` results, which profile/monitor/qualify never save; after
      `crawl.max_browser_restarts` it is `dead` and the stage stops with one error (`update` then skips monitor).
      Each page has a hard time limit. The log shows Chromium's own reason.
    - Old "unreachable" rows saved during crashes are removed automatically (profiles + their site_down changes).
    - Enrich stops for the run when rdap.org refuses (401/403/429), saves nothing for those domains, and sends
      an honest user agent.
22. Browser crash fix for all modules: Chromium's GPU process crashed on the Mac ("GPU process isn't usable").
    - `project_paths.chromium_args()` (default `--in-process-gpu`, override CHROMIUM_ARGS in automation.conf)
      is passed by tech intel (and so the news fallback), vehicles and the `scrapers/` tools.
    - doctor.py checks a page that needs graphics (WebGL + screenshot), with and without the flags.
    - Plan agreed with Kanchana: fix the shared browser problem first, then go through each module's own errors
      one at a time (news: Reuters/news.com.au robots, HN feed 419, Yahoo feed 404; tech: crt.sh 502, rdap.org 429).
23. `tech_update` paused (Kanchana, 2026-11-03): default STEPS = markets news tech_collect vehicles. Priority order
    now: world news, tech intel collect, then markets, vehicles. Run update by hand: `./run_all.sh tech_update`.
24. World news reviewed source by source (timed every source, nothing saved):
    - It used a fake Chrome user agent, against the project rule. Now honest: `worldnews-reader/1.0 (...)`.
      That fixed Hacker News (HTTP 419 before).
    - Switched off (`"enabled": false` + `disabled_because`): Reuters and news.com.au (robots.txt lets only named
      crawlers in), USA Today (feed gone, HTTP 402; homepage gives nothing).
    - Time limits: 30s for article dates, 90s per source. A run went from 157s to 90s (Washington Post alone took 157s).
    - Kanchana chose to switch off the 6 sources that refuse our honest user agent: AP News, The Verge, Toronto Star
      (403), VentureBeat (429), Washington Post and CBC (drop the connection). The browser fallback is never used
      after 401/403/429.
    - **Result: 71 of 71 sources OK in 25s** (was 72 of 80 in 157s+). World news is done; next: tech intel collect.
25. Tech intel collect reviewed stage by stage (run in a test folder, not the real database):
    - qualify sent crawl4ai's made-up Windows browser user agent, and retried in the browser after 403/429/503.
      Now an honest user agent, and the browser only for pages that load empty (JavaScript sites).
    - crt.sh: a `%.lk` query takes ~110s (the 60s limit made it fail every run) and crt.sh often answers 502.
      Now 150s, at most every 6h, and 60 min wait after a failure (state in data/techintel/crtsh_state.json).
      Discover went from 1m02s to 1s when crt.sh isn't due.
    - Registration dates: rdap.org refused after ~12 lookups (429). Now straight from each registry via IANA's
      list: 50 of 55 got a date in 19s, none refused. A refusing registry is skipped for that run only.
    - Hacker News showed an empty error on a timeout; errors now always say what happened.
26. Markets reviewed (2026-11-03): all 50 sources OK in 2s, honest user agent.
    - Bug fixed: when Yahoo's daily closes didn't include today yet, the day's change used the day before
      yesterday. Now the previous close is the last one from an earlier day (by date). USD/LKR 332.58 is real:
      EconomyNext reported "rupee closes at 332.55/65" on 2 Nov.
    - Stooq now answers with a "verify your browser" JavaScript puzzle: recognised, not solved, and not asked
      again that run. **No backup for US stocks/indices/metals if Yahoo blocks us** (FX still has open.er-api.com).
    - CSE requests no longer pretend to come from cse.lk's website (Origin/Referer); the API answers anyway.
27. Vehicles reviewed (2026-11-03): ikman and riyasewana, 20 ads each in ~7s; robots.txt allows both pages.
    - It used crawl4ai's stealth settings and "magic" mode (made to hide automation from bot detection),
      against the project rule. Both sites work the same without them, so they're gone (`--no-stealth` is
      accepted but does nothing). doctor.py no longer tests stealth.
    - ikman photos: the <img> is a placeholder until scrolled, so only 1 of 20 ads had one. The photo URLs are in
      the page with the ad's slug: new site option `image_by_slug`. Now 20 of 20.
    - riyasewana "Negotiable" prices have no number: correct, not a bug.
    - **All four modules reviewed.** Still open: tech_update (paused, browser GPU fix not confirmed on the Mac)
      and no backup for US market prices if Yahoo blocks.
28. Dashboard made full screen and "more intelligent" (Kanchana's request, 2026-11-03):
    - Full width (no 1400px limit), ⛶ button / F key for browser full screen, side column 400-460px.
    - Tech Intel: compact one-line-per-site list, sort (latest seen, score, newest domain, name), domain age with a
      NEW badge (< 30 days), score bars, sticky headings; charts: found/qualified per day, domain age, categories, status.
    - Markets: Compare chart (% change, 1W/1M/3M, quick picks, choices remembered), Performance table (1d/1w/1m/3m),
      map view, bigger price chart with ranges. API: /api/markets/compare, /api/markets/performance.
    - History: Yahoo now returns 6 months of daily closes per symbol (extra["daily"], same one request), merged with
      saved runs in queries.history_points(). CSE and crypto history only builds up from our own runs.
    - News: topics per day, publishing hour (local time). Vehicles: price ranges, makes with median price,
      photos load (no referrer sent).
    - index.html links app.js/style.css with ?v=<mtime>, so browsers don't mix old and new files after an update.
    - Ideas: an "Overview" tab with the highlights of all four; alerts (price drop on a watched model, big market move).
29. Telegram channel planning (no code): plan + 7 post mockups on a Design canvas
    (claude.ai/artifact/S9QJz8ddc5jZtgQeAvGd23), business plan PDF (output/business-plan/), "Insight Engine" plan
    (claude.ai/artifact/6GRP8v7TvjYLTtGyUxLgLf): publish our own analysis, never sources' raw data; daily/weekly/monthly
    summaries; wealth-building lessons; quotes only verified and public domain; astrology only as labelled culture content.
    Ideas agreed but not built: cost-of-living index from CBSL price reports + inflation + fuel/gas.
30. `briefing` module (2026-11-03): `python -m briefing run` makes the day's summary text, facts JSON and a 30-second
    vertical video (Pillow + ffmpeg) in output/briefing/, offline, in about 7 seconds. Step `briefing` is last in the
    default STEPS. Built-in check: a day's change over 10x the typical daily move is held back (USD/LKR +3.1% was 18x,
    a gap in Yahoo's history; Brent's real -11% was 5.8x). Markets now save 6 months of daily closes.
    - Next: channel name/link via BRIEFING_ARGS; a Telegram bot to post it (after the legal checks in the business plan).

31. `techintel company` (Kanchana's request, 2026-11-03): company facts for each qualified site, from the company's
    own homepage + about/team/contact pages (HTTP, no browser): social account links, leadership names, employees
    (schema.org or "team of N"), public emails/phones, founded, address, and GitHub's public numbers via its API.
    Table `companies`, view `tech.v_companies`, run_all step `tech_company` (in the default STEPS, 10 min limit).
    - LinkedIn and Facebook are **not** scraped (their terms forbid it, and it needs a login): links only. Agreed rule.
    - 60 real sites in 35s: 55 with social links, 31 LinkedIn, 8 with named leadership, 34 GitHub numbers.
    - GitHub allows 60 lookups/hour without a token; refused lookups are filled in on later runs. `GITHUB_TOKEN` raises it.
    - Dashboard (Tech Intel tab): "Company" column with social-account links (in, f, X, ig, yt, gh, tt), people
      count and employees; a filter (has social links / LinkedIn / names its people / gives employees); KPIs and a
      "Social accounts" chart; a Company section in each site's details (people, contacts, GitHub, all accounts).
      Works with older databases that have no companies table yet.
    - Kanchana saw "rejected 780" but only ~14 rows: the site list is a scroll box showing ~14 of the first 100 loaded.
      Counts now say "780 · showing 100", and the Tech Intel list loads the next 100 when scrolled to the bottom.
32. `worldnews analyze` (Kanchana's request, 2026-11-03, ~1,800 headlines saved): our own analysis of the saved
    headlines, offline, ~2s. Biggest stories (same story from several sources: titles sharing ≥3 meaningful words),
    topics (15, word lists in settings.json `analysis.topics`) overall and per country, names and places, countries
    mentioned, rising words vs the period before, rough word-based tone per country, and a Sri Lanka section.
    Report in output/worldnews/analysis/ (html, md, json); run_all step `news_analyze` (in the default STEPS).
    - First run: 19 sources on the Florida jet crash, 16 on the California ballots case, 13 on US strikes on Iran;
      Sri Lanka: Trincomalee shooting, Deduru Oya overflow, Namal Rajapaksa in court.
    - Rising words need a day of earlier data (the news history started on 2026-11-03).
    - Dashboard (World News tab): analysis panels above the headlines, following the period and country filters:
      biggest stories (with all their headlines), topics, names and places, countries, tone per country, what each
      country's news is about, rising words. Clicking a name, country or rising word searches the headlines.
      API: /api/news/analysis?days=N&group=LK (computed live, ~1s for 1,800 headlines).
    - Ideas: use the top stories in the briefing; weekly/monthly views.
33. ASPI was a day behind (Kanchana noticed, 2026-11-05): cse.lk's `dailyMarketSummery` only has finished trading days,
    so during trading it still gave yesterday's close. Now ASPI and S&P SL20 come from `aspiData` / `snpData` (live,
    as on cse.lk's front page, with `marketStatus`), and the summary is the fallback (and gives P/E, turnover).
    - Also found: "S&P SL20" had been saved from the field `spt`, which is the S&P SL20 **total return** index (~12,400);
      the price index is `spp` (~6,000). Fixed; the 15 old rows were relabelled "S&P SL20 TRI" (nothing deleted), and
      the TRI is still saved each day under that name. S&P SL20's own history starts again on 2026-11-05.

34. `companies` module (Kanchana's request, 2026-11-05): a company directory, two steps.
    - `collect`: name, website, country (+ source id, industry, registration no., ...). Sources run side by side; each
      saves a cursor per country after every batch, so a run stopped anywhere continues there. Priority countries
      (US, GB, AU, IN, LK) take turns, then one other country per round. A finished country restarts after 30 days.
    - Sources checked first (terms, limits, robots.txt): **Wikidata** SPARQL (CC0; one query per country and company
      type, 18 types: a whole country timed out at 60s, one type takes 2-35s; we wait as long as each query took,
      for its 60s-per-minute budget; its robots.txt keeps search crawlers off /sparql, but the service is offered to
      programs under the Wikimedia User-Agent policy). **GLEIF** (CC0, cursor paging because page numbers stop at
      10,000; 60/min allowed, we do one per 1.1s; GENERAL + ACTIVE only). **CSE** (tradeSummary + companyProfile:
      website, sector; one company per share class). Off: **Companies House** bulk zips (OGL, ~5.5M rows, no
      websites), **ASIC** (data.gov.au's robots.txt disallows all robots, so Kanchana would download the CSV and set
      `file`), **SEC** (needs a real contact email; never invented, never Kanchana's without asking).
    - `update`: homepage + about/team/contact over plain HTTP with techintel's Fetcher and extractors (merge_pages),
      robots.txt respected, one row per domain in `sites`. 401/403/429 go to `refusals` (not results), retried
      after 14 days. Platform "websites" (facebook.com/..., blogspot) are skipped.
    - Found on the way: crawl4ai reports a 403 in plain-HTTP mode as an error whose code listing mentions "Timeout",
      so techintel's fetcher logged 403s as "timed out". `techintel/fetch.py` now reports "HTTP 403" and sets the
      status. And the tests shared crawl4ai's robots.txt cache (by host:port), so a test site's "Disallow: /" could
      refuse a later test on the same port: tests now use their own crawl4ai folder (47 test entries removed from
      the real cache).
    - Dashboard: Companies tab (KPIs, country/source/website-status filters, search, paged list, details drawer that
      reuses the tech tab's company panel, sources, progress, runs). API: /api/companies/summary, /list, /company/{id}.
    - First real run (Claude's shell): collect 5 min = 58,570 companies (Wikidata 37,053 with websites, GLEIF 21,247,
      CSE 270) in 27 countries; US 25,884 / GB 6,674 / IN 2,218 / AU 2,065 / LK 324 with a website. update 3 min +
      1.5 min = 535 websites read (414 ok; most with social links), 51 refusals kept out of the results. update takes
      turns between countries (otherwise the US's 25,000 websites would keep Sri Lanka waiting).

35. **Decision (2026-11-05): move from DuckDB to PostgreSQL, everything, no sync.** Kanchana's reason: while a run
    writes to a DuckDB file nobody can read it, so the dashboard and analysis wait during runs, and only one person
    can use the data. PostgreSQL lets collectors write and many users read at the same time. Plan, steps and
    checks: **`docs/postgres-migration.md`** (not started). Order: 1) database `crawl4ai` on the Mac's PostgreSQL 18
    (Homebrew, already running, has other projects' databases too); 2) copy all DuckDB data (~145,000 rows);
    3) switch tool by tool (companies first) with its dashboard tab; 4) clean up. Later: a server with 100+ GB,
    per-section schedules, the GLEIF full file (3.5M companies, downloads in ~35 s; no websites in it, only
    ~21,600 get one through Wikidata's LEI links).
36. **PostgreSQL step 1 done (2026-11-05):** database `crawl4ai` on the Mac's PostgreSQL 18 with logins `crawl4ai`
    (writer) and `crawl4ai_reader` (read-only, 30 s query limit), extensions pg_trgm + unaccent, schemas news, tech,
    markets, vehicles, companies, scrapers. `uv run python -m warehouse setup` (safe to repeat), `warehouse
    pg-check`, and doctor.py checks it. Settings: `CRAWL4AI_DATABASE_URL` / `CRAWL4AI_READER_URL` /
    `CRAWL4AI_ADMIN_URL`, or `.local/database.env`. Driver psycopg 3. Tests use a throwaway server. The tools still
    use DuckDB; **next: step 2, copy all the data** (`docs/postgres-migration.md`).
37. **PostgreSQL step 2 done (2026-11-06):** tables in `warehouse/schema/<schema>.sql` (same columns as DuckDB, plus
    indexes and name-search indexes); `uv run python -m warehouse migrate` creates them, and
    `uv run python -m warehouse copy-from-duckdb [--tool X] [--replace] [--check]` copies with a check per table
    (rows + newest time), all or nothing per tool. Real copy: all 25 tables match (~145,000 rows, ~9 s); the same
    questions give the same answers in both, 1-46 ms each in PostgreSQL. The tools still write to DuckDB, so the
    PostgreSQL copy gets older until each tool switches. **Next: step 3, switch companies (store, dashboard tab).**
38. **PostgreSQL step 3a: companies switched (2026-11-06).** `companies/store.py` writes to PostgreSQL (schema
    `companies`): batches go in with COPY into a temp table and one upsert, one short transaction each; the
    dashboard's Companies tab, `companies show/stats/sources` read with the read-only login. `warehouse.PG_TOOLS`
    lists the tools already moved; `warehouse sql/info` skip their DuckDB file, and the new
    `uv run python -m warehouse pg "..."` queries PostgreSQL. `warehouse migrate` runs a schema file only when it
    changed (hash in `public.schema_versions`, advisory lock), so opening a store costs one query.
    - Checked on the Mac: fresh copy (all 5 tables match), then a real 1-minute collect (+3,913 companies, 109,690
      in all) while another program read the summary and a search every 10 s: every read answered (0.4-0.6 s for
      the whole summary), nothing waited. A 1-minute update read 71 LK websites. The Companies tab on the new code
      showed it all, no browser errors.
    - `data/companies/companies.duckdb` is not used any more (kept until step 4).
    - **Next: step 3b, world news** (then markets, vehicles, tech intel, scrapers, briefing).
39. **PostgreSQL step 3b: world news switched (2026-11-06).** `worldnews/store.py` saves to schema `news` (each run
    in one short transaction); the World News tab, `worldnews analyze` and the briefing's news numbers read with
    the read-only login. `worldnews check` is now a dry run: it reads the real history but saves nothing (it used
    to write to a separate `_check` DuckDB). The dashboard shows an empty tab instead of an error when a tool's
    tables don't exist yet. The "busy database" test moved to tech intel (still DuckDB); a new test checks the
    news tab answers while a run's transaction is open.
    - Checked on the Mac: fresh copy (5 tables match), then a real `worldnews run` (71/71 sources, 704 headlines,
      172 new) while summary + analysis were read every 4 s (all answered, ~1 s each); 4,582 + 172 = 4,754 saved;
      `check` saved nothing; `analyze` and the briefing read PostgreSQL; the World News tab showed it, no errors.
    - **Next: step 3c, markets** (then vehicles, tech intel, scrapers, briefing).
40. **PostgreSQL step 3c: markets switched (2026-11-06).** `markets/store.py` saves each run to schema `markets` in
    one transaction; views `v_latest` (distinct on, with an index on asset_class, symbol, run_at desc) and `v_daily`
    (last quote per UTC day) are in `warehouse/schema/markets.sql`. The Markets tab, `markets show/history` and the
    briefing's prices read with the read-only login. DuckDB's `try_cast` on extra fields became a jsonb type check.
    - Checked on the Mac: fresh copy (3 tables match), then a real `markets run` (446 prices, 50/50 sources) while
      the summary + performance table were read (all answered, ~0.2 s); 7,608 + 446 = 8,054 saved. `show`,
      `history ASPI`, the briefing and every Markets API answered; the tab showed it, no browser errors.
    - **Next: step 3d, vehicles** (then tech intel, scrapers, briefing).
41. **PostgreSQL step 3d: vehicles switched (2026-11-06).** `vehicles/store.py` saves each site's run to schema
    `vehicles` in one transaction; the three views are in `warehouse/schema/vehicles.sql` (window functions in
    subqueries instead of DuckDB's `qualify`; `median` became `percentile_cont`). The Vehicles tab, `vehicles
    show/changes` and the briefing read with the read-only login. The one-time copy of ads from scrapers.duckdb was
    dropped from the store: that history came over with the DuckDB copy.
    - **Tests can't reach the real PostgreSQL any more:** `tests/conftest.py` points every `CRAWL4AI_*_URL` at a
      dead address unless a test takes `pg_env`. It caught the doctor tests reading the real database (now a
      stand-in, plus a test of the PostgreSQL check on the throwaway server).
    - Checked on the Mac: fresh copy (3 tables match), then a real `vehicles run` (ikman 10 ads in 4.9s,
      riyasewana 10 in 2.8s) while the tab's queries were read every 3 s: each answered (0.2-0.3 s), and showed
      ikman's ads arriving before riyasewana's. 545 + 20 = 565 ads; 344 unique, 0 price changes, the same as
      DuckDB (334 + 10 new). CLI, briefing and the tab work, no browser errors.
    - **Next: step 3e, tech intel** (the biggest: JSON records, typed views, the live listener; then scrapers,
      briefing).
42. **PostgreSQL step 3e: tech intel switched (2026-11-06).** `techintel/store.py`: `DuckStore` became `TechStore`
    (schema `tech`, same methods; each append one short transaction). The six typed views are in
    `warehouse/schema/tech.sql`, with `tech.try_ts/try_num/try_int` (built on `pg_input_is_valid`) in place of
    DuckDB's `try_cast`. The Tech Intel tab, `techintel status/report`, the live listener's "known domains" and the
    briefing read PostgreSQL. JSON records come back as dicts in jsonb key order.
    - **Every dashboard tab reads PostgreSQL now**, so the dashboard has no DuckDB code left (the "busy" fallback
      and its test are gone). Also fixed: the dashboard's startup printout (since step 3a) reused the name `url`,
      so `--open` would have been given the wrong value.
    - Checked on the Mac: fresh copy (7 tables match), a real `techintel quick` (11 s: 9 new sites, 6 qualified,
      7 dated) and `company --max-minutes 1` (6 sites read), `status`, `report`; the listener knows 2,987 domains.
      Counts match DuckDB plus this run (qualified 1,487 + 6, dated 1,604 + 7, companies 1,464 + 6, profiles 336).
      The tab and a site's details panel showed it, no browser errors.
    - **Next: step 3f, scrapers** (the older single-file scrapers; then briefing, which only reads, then step 4).
43. **PostgreSQL step 3f: scrapers switched, so every tool is on PostgreSQL (2026-11-06).** `warehouse/scrapers_db.py`
    saves each output to schema `scrapers` (views `v_news_headlines`, `v_products` in `warehouse/schema/scrapers.sql`).
    The briefing was already reading PostgreSQL for every part. `warehouse sql` now runs on PostgreSQL with the
    read-only login (`pg` is the same command), and `info`, `examples` and the prompt too; no tool reads or writes
    DuckDB any more (only `copy-from-duckdb` still opens the old files).
    - Checked on the Mac: copy (75 rows match), then a real `python -m scrapers.news` saved 683 headlines; the view
      shows them. `warehouse info` lists the six schemas (87 MB in all).
    - **Next: step 4, clean up**: remove the DuckDB code and the `duckdb` package (or keep it only for
      `copy-from-duckdb`), archive the old .duckdb files in `data/archive/`, add a daily `pg_dump` backup step,
      and update the docs.
44. **PostgreSQL step 4 done: the move is finished (2026-11-06).**
    - No DuckDB left in the code: `warehouse.connect`/`DB_FILES`/`PG_TOOLS` became `warehouse.DATA_DIRS` (only the
      folders for files kept next to the database), the briefing reads PostgreSQL only, the copy tool
      (`copy-from-duckdb`) and its tests were removed (the copy is done), and `duckdb` is out of pyproject/uv.lock.
    - The six .duckdb files were **moved, not deleted**, to `data/archive/duckdb-2026-11/<tool>/` (with a README).
    - **Backups:** `uv run python -m warehouse backup [--keep 7]` (`warehouse/backup.py`): `pg_dump` custom format
      to `data/backups/crawl4ai-<date>-<time>.dump`, written under a temporary name first, the newest 7 kept. New
      run_all step `backup`, last in the default STEPS (`BACKUP_ARGS` in automation.conf). First real backup:
      8.6 MB in 2.5 s; **restored into a temporary database and every table's count matched**, then it was removed.
    - Kanchana's dashboard was still the old code (started before step 3a); it must be restarted.
45. **Plan (2026-11-06): the user platform** (`docs/user-platform-plan.md`, not started). An internal web app for
    ~10 people on top of the collected data: logins with role and menu-based access granted by an administrator,
    an Airtable-like grid (inline and bulk edit, multi-choice, links between records, saved views, own tables),
    charts and dashboards, CSV/Excel exports, PDF reports, and an AI assistant that sees only the catalog.
    Decisions with Kanchana: **Python only (no React): Django + HTMX + Plotly + WeasyPrint**; grid **AG Grid
    Community** (MIT), tested against **Tabulator** (MIT, has free range copy/paste) in phase 1, behind our own
    wrapper so it can be swapped; PostgreSQL with JSONB for flexible fields and an automatic catalog; edits in the
    main tables with history and **rule A** (the newest change wins: the collector overwrites a correction only
    when the source itself changed). Collectors stay as they are. 8 phases; phase 1 = Django foundation (login,
    users, roles, menu rights, admin, audit) + the grid test.
46. **User platform phase 1 built (2026-11-06)** in `webapp/` (Django 5.2 LTS + django-htmx + django-axes + Argon2;
    pinned AG Grid Community 36.2.0, Tabulator 6.7.1, HTMX 2.0.11 in `webapp/static/vendor/`, MIT/0BSD).
    - `warehouse setup` now also makes login `crawl4ai_app` (CRAWL4AI_APP_URL), which owns schema `app` and reads the
      data schemas; the reader can't see `app` (pg-check says so). `python -m webapp migrate` made 19 tables.
    - Roles Administrator / Editor / Analyst / Viewer, menu rights on one page, lockout after 5 wrong passwords,
      append-only audit log (database trigger), `python -m webapp createadmin`. Details and safety rules:
      `docs/user-platform-plan.md` → "Phase 1 results".
    - Grid test at `/grid-test/` (132,742 companies, both grids). Found and fixed on the way: Tabulator's height
      "100%" made it load every page; its paste needed our own handling. Leaning to **Tabulator** (free cell ranges
      and copy/paste); **Kanchana to try both and decide**.
    - Two test accounts (`test-admin`, `test-viewer`) were made for the browser check; their passwords are in
      `.local/webapp-test-accounts.txt` (git-ignored). Deactivate them on the Users page; Kanchana has their own `admin` account now.
    - Tests: 16 web app tests (pytest-django on the throwaway PostgreSQL) + 1 database test; 149 in all.
    - **Next:** Kanchana runs `createadmin`, tries the grid test and picks the grid; then phase 2 (catalog + explore).
47. **Grid chosen: Tabulator (Kanchana, 2026-11-06)**, because selecting a block of cells is needed and is paid-only
    in AG Grid. AG Grid is removed (`datagrid.js`, the page, `static/vendor/`). New: a **searchable multiple-choice
    list** (`DataGrid.pick` in `datagrid.js`): search box on top, ticks, chosen tags as removable chips, keyboard
    (arrows, Enter ticks, Esc cancels, Tab or clicking away saves), used by the Tags cell and the bulk edit bar.
    Our own because Tabulator's list editor can't search and multi-select together. The grid test has 30 test tags.
    Tabulator now follows the app's dark and light themes.
    - **Users can add new tags (Kanchana, 2026-11-06):** type a name that isn't in the list and choose "Add new tag"
      (or press Enter). It's ticked at once and everyone can use it from then on; the same words in other capitals
      count as one; at most 40 characters, no commas; each addition is in the audit log. Model `GridTestChoice`,
      endpoint `/grid-test/api/choice/`, `DataGrid.addChoice`. Phase 5's real choice fields reuse this.
    - **Next: phase 2** (automatic catalog, every dataset in the grid, saved views, exports).
48. **User platform phase 2 built (2026-11-06):** `webapp/catalog/` (automatic scan: 38 datasets, 653 fields, 6 s; run_all
    step `catalog`; screens for names, fields, on/off; rights to see and export per dataset and role) and
    `webapp/explore/` (one page and endpoints for every dataset: heading filters including tick lists, search, multi-sort,
    column chooser, saved views, CSV and Excel). A new table appears after a scan, for administrators until granted.
    Details and limits: `docs/user-platform-plan.md` → "Phase 2 results".
    - Found while checking in the browser: Tabulator read a key released on a filter as typing, which cleared tick-list
      filters; `[hidden]` didn't hide buttons; tables with no rows had no columns to show. All fixed.
    - **Next: phase 3** (today's dashboard tabs in Django, charts in Plotly; then the aiohttp dashboard retires).
49. **User platform phase 3 built, old dashboard retired (2026-11-06):** the five tabs are Django pages in
    `webapp/insights/` (`/dashboard/`), with Plotly charts, every filter in the address and rows swapped by HTMX. `queries.py`
    moved there; the aiohttp server and `app.js` are gone. Details: `docs/user-platform-plan.md` → "Phase 3 results".
    - Compared with the old API on the real data, per tab, with filters, sorting, paging and side panels: 291 checks
      identical. Two more differ only by the first point of a chart, which falls just before the 100-day cut (the old
      page cut it in the browser).
    - New on purpose: a tab follows the rights of its main dataset (so the dashboard can't get around the catalog's
      rights); links from collected pages must be http(s); a line keeps its colour when another is removed from Compare.
    - **Next: phase 4** (editing: inline and bulk edit, history, rule A in the collectors).
50. **User platform phase 4 built (2026-11-06): people can correct collected data.** Companies (name, country, website, industry)
    and vehicle ads (title, price, location, year, mileage), in the grid: double-click, paste, or select rows and set one value;
    hide rows; a row's history with Put back; a Changes page. New schema `audit` (made by the first `warehouse migrate` or
    collector run), `warehouse/edits.py`, `webapp/explore/editing.py`. Details: `docs/user-platform-plan.md` → "Phase 4 results".
    - **Rule A is in `CompanyStore` and `VehicleStore`:** a correction stays while the source repeats itself; if the source
      really changes the value, the new value wins and the correction stays in the history (per field in the catalog: rule B,
      the person wins; rule C, flag for review).
    - The web app's database login can update only those columns. Editors start with Edit and Hide; Analysts and Viewers don't.
    - Checked on a temporary copy of the real database with real CSE and vehicles runs, then deleted. The real history is empty.
    - **Backups had been failing since phase 1** (found while checking them for this): `warehouse backup` used the collectors'
      login, which can't read the web app's schema, so no backup was made after 01:06 on 2026-11-06. It now uses the
      administrator's login (`CRAWL4AI_ADMIN_URL`), and includes users, the audit log and corrections. A new backup was made.
    - **Next: phase 5** (Airtable-like: choice fields, links between tables, lookups, own tables, imports).
51. **User platform phase 5 built (2026-11-06): own tables, own fields, links.** Menu My tables (`webapp/userdata/`): make a table,
    import CSV/Excel, or Save as table from any dataset; private until shared with a role. A Fields page on every dataset whose rows
    have a key: text, number, money, date, yes/no, one or several choices, web address, email, link to another table (the other table
    gets a column too), lookup, rollup. Own fields on collected data are never touched by collectors. The plan's test passes: ads
    linked to a Dealers table, with ad count and total asking price per dealer. Details: `docs/user-platform-plan.md` → "Phase 5 results".
    - New schema `userdata` (owned by the web app's login). **On a new server: `warehouse setup` before `webapp migrate`.**
    - **Bug found, there since phase 2:** a JSON list reached the grid as text and left the whole grid blank (Tech site profiles,
      Company websites, Tech companies). Fixed for the grid and exports.
    - Checked on a temporary copy of the database, then deleted: the real database has none of the test tables.
    - **Next: phase 6** (charts, dashboards, PDF reports).
52. **User platform phase 6 built (2026-11-06): charts, dashboards, PDF reports.** `webapp/charts/` (a builder with a live preview over any
    dataset; columns, bars, line, area, doughnut, one number, table; dashboards that arrange them) and `webapp/reports/` (a dashboard as a
    PDF, on demand or daily/weekly/monthly). New run_all step `reports` (in the default STEPS), writing to `output/reports/`. WeasyPrint needs
    Pango (`brew install pango`, already on this Mac). Details: `docs/user-platform-plan.md` → "Phase 6 results".
    - PDFs draw their own charts as SVG (`charts/svg.py`), since a PDF can't run plotly.js. Same colours, same numbers.
    - Checked on a temporary copy of the database with `./run_all.sh reports`, then deleted: the real database has no test charts.
    - **Next: phase 7** (AI assistant: catalog-only SQL, preview, save as live dataset, limits, logging).

53. **User platform phase 7 built (2026-11-06): the AI assistant.** Menu AI assistant (`webapp/assistant/`): ask in plain words, Claude Opus 5.5 writes one query from a description of the datasets, the server
    checks it and runs it as the new read-only login `crawl4ai_agent`; the person sees the rows, an explanation and the query, and can save it as a live dataset (a view, `userdata.a_*`), which then works in the grid, exports and charts.
    Claude never gets rows, only the count or the error. Limits: 20 questions per person per day, $50 a month, 3 requests per question; every question is logged with its cost. Details: `docs/user-platform-plan.md` → "Phase 7 results".
    - **Not yet run against the real Claude: no API key on the Mac.** Tested with a stand-in (and the real SDK's request with the network swapped out). Kanchana adds `ANTHROPIC_API_KEY=...` to `.local/database.env`, restarts, and tries real questions.
    - Found by the "no rows reach the AI" test: the catalog's value lists can be the rows themselves in a small table. Now only lists of values that repeat, and no addresses.
    - Checked on a temporary copy, then deleted: the plan's question gives 8 real companies in 1.9 s, saved and shown in the grid.
    - **Open question for Kanchana:** the assistant only knows collected datasets, not people's own tables. Say if own tables should be included.
    - **Next: phase 8** (the server: HTTPS, two-step login, backups, monitoring), when Kanchana says go.

54. **Newsletter reports (Kanchana's request, 2026-11-06).** Reports → New newsletter: a PDF like a daily newsletter from any dataset, or ready-made (Daily news, New companies, New vehicle ads, Markets, Daily briefing), on demand or every day, week or month through the `reports` step.
    Sections of a dataset's latest rows: articles or a table, filters, period, sorting, bars of the most common values. `webapp/reports/newsletter.py`, `sections.py`; details: `docs/user-platform-plan.md` → "Newsletter reports".
    - **Decision (Kanchana, 2026-11-06): the AI feature comes last.** Item 53 is in the code and stays off until a key is added. Nothing more on it for now.
    - Checked on a copy of the real data on a temporary PostgreSQL on the external drive (the Mac's drive had only ~350 MB free), then deleted: nothing went into the real database except the new empty table.
    - **Next:** Kanchana tries the PDFs and says what to change.
55. **News text cleaned (Kanchana, 2026-11-06), found through the newsletter's PDFs.** `worldnews/cleaning.py`, applied to every headline before it's saved:
    - **Newsfirst:** its homepage cards stick the date and the article's first words to the title. Now just the headline, and the card's date becomes `published_at` (it had none, so its stories sank to the end of anything sorted by date). Its summaries lose the site's slogan.
    - **Hacker News:** the summary "Comments" is dropped. **CryptoSlate and WordPress sites:** "Read the full story on..." and "The post ... first appeared on..." are cut.
    - `uv run python -m worldnews clean [--dry-run]` applies the rules to what's saved (safe to repeat). Run on the real database after a backup: 197 of 5,239 headlines changed (58 titles, 197 summaries, 58 dates). URLs and counts are unchanged.
    - **Sri Lankan dates were 5½ hours ahead:** Daily Mirror and Daily FT write their own time with no zone, and it was read as UTC. A date with no zone from a `.lk` or LK site now means Sri Lankan time (`collect.source_zone`). The saved rows of those two sources were moved back once by hand.
    - Left as they are: 2 old Newsfirst titles in other wordings ("COLOMBO, SRI LANKA | ...", "By Dr. ..."); The Island gives only a day, so its stories show 05:30; other countries' dates with no zone can't be told apart, and stay UTC.

56. **Deleting asks first, and every report can have sections (Kanchana, 2026-11-06).**
    - Deleting a report went straight through on the first click. Now a page asks "Delete ...? No, keep it / Yes, delete it" (`webapp/core/confirm.py`, `ask_first`). The server asks, so an old page or a double click can't skip it. Also for: a report's section, a chart, a dashboard, a role, an own field.
    - Deleting a dashboard also deletes the reports that print it, without a word. The question now names them. A chart's question names the dashboards it comes off.
    - Kanchana's "News from Oxsora" was made with the "New report from a dashboard" form, and that kind had no sections. Now any report can: it prints the dashboard's charts, then the sections (`_sections.html`, shared with newsletters). A file is given to someone else only if they may see the data of both.
    - Checked in the browser on a temporary copy of the database, then deleted. The real reports weren't touched.
57. **Report templates (Kanchana, 2026-11-06): people design how reports look.** New menu Report templates (`/report-templates/`, whoever has Reports gets it). A form with a live preview on made-up content: six colours, fonts, text size, top of the first page (line, band, centred), section headings, one or two columns of articles, a logo, paper, direction, margins, a line at the top and bottom of each page, page numbers. Four starting designs (Plain, Newsroom, Boardroom, Quiet). Then choose it under Design on any report, or when making a newsletter. Shared templates can be used and copied; only the owner or an administrator changes them.
    - `ReportTemplate` in `webapp/reports/models.py` (an unsaved one is the plain design; the old stylesheet came out line for line the same), `designs.py`, `template_views.py`, `_style.html` and `document.html` follow it.
    - It's a form, not free HTML or CSS: every value is from a list or a checked colour, because it all goes into the stylesheet. Logos must be PNG or JPEG up to 2 MB, and are redrawn as a PNG kept in the database, so a PDF still fetches nothing. SVG is refused.
    - Bug fixed on the way: the footer's report name went through a JavaScript escape, so "Sales-team" printed as "Salesu002Dteam".
    - Migrated on the Mac after a backup. Checked in the browser on a temporary copy of the database, then deleted: no template is in the real database yet.
    - Not done: fonts beyond the two families, free HTML, a template that also carries sections, colours for charts' second series onwards.
58. **Custom CSS in report templates (Kanchana's choice, 2026-11-06).** A box on the template form: CSS rules added after the form's choices, with the live preview and a list of the parts that can be styled.
    - `webapp/reports/customcss.py` (tinycss2): the CSS is parsed, checked and written out again, every rule tied to `.r-doc`. No `url()`, `@import`, `@font-face`, `@media`, unknown functions or `<`, including when spelled with escapes. Refused CSS isn't saved, and the message gives the line. CSS that stops passing later is left out and the report still prints.
    - **On screen, a design with custom CSS shows in a sandboxed frame** (`_sheet.html`), because a rule like `position: fixed` could otherwise cover the app for someone viewing a shared template. Designs without it show as before.
    - Found by a test: a rendered template counts as safe in Django, so the frame's content wasn't being escaped. It now uses `force_escape`.
    - Migrated on the Mac after a backup. Checked in the browser on a temporary copy of the database, then deleted.

59. **Stochastic RSI for the CSE and the crypto top 100 (Kanchana, 2026-11-06).** On the Markets tab (CSE and Crypto: Stoch RSI, %K, %D, last close, sortable, overbought and oversold marks), in each row's side panel, and in the grid as datasets **Stochastic RSI** and **Daily closes**. `uv run python -m markets closes`, `stochrsi`; run_all step `markets_closes`. Numbers only, no buy or sell advice.
    - **The calculation** (`markets/indicators.py`): Wilder's RSI 14, stochastic of RSI over 14, %K mean of 3, %D mean of 3, on a 0-100 scale. RSI matches all 19 of StockCharts' published values within 0.02, and a separate quick implementation agreed on all 365 instruments within 2e-13. **34 closes needed** (the first %D is possible at 32); fewer says "not enough history yet (N of 34 days)". Flat prices, or an RSI that only moved by rounding, say "no value", never a made-up number.
    - **Our own history was only 5 days**, so the closes come from public sources (`markets/closes.py`, table `markets.daily_closes`), never from our runs, which can be mid-day. `markets.stoch_rsi` is a table written after each sync, not a view, because Wilder's RSI carries each day's average into the next: awkward and slow in SQL, and read far more often than the closes change.
    - **CSE: 295 share lines from 274 issuers** (not the 225 or 270 mentioned before; 289 traded on 2026-11-06). cse.lk's chart API, about a year per share. robots.txt allows it. Its terms couldn't be read: the footer's "Terms & Conditions" is plain text with no link. First run 8m20s (one call every 1.5 s); after that 2 calls a day add every share's close. All 289 prices in tradeSummary equal the chart's close. A share needs its own call again only if a whole trading day was missed. A day's close is only saved after 15:00 Sri Lankan time.
    - **Crypto: CoinPaprika**, 364 days, the price at 00:00 UTC. Its API terms ask for attribution (the page says "Powered by CoinPaprika") and forbid competing services and bulk resale. The free plan is personal use only; paid starts at $99 a month if the platform goes to colleagues. **Its real limit is 60 requests an hour (soft) and 25k a month**, from its own 402 answer; the pricing page's "10 a second" and 20,000 a month are not what happens. The first run went 76 coins in and got that 402; the coin it stopped at (RENDER) fetched normally an hour later, so it was the limit and not the coin. So a run now takes at most 50 coins, stalest first (under 2 minutes), and the top 100 needs two runs at least an hour apart. A coin that waits loses nothing: one call fills every day it's behind. Each row shows its last close.
    - **Result on 2026-11-06: 383 of 396 have a value** (CSE 289 of 295, crypto 94 of 101; 102,955 closes). Without a value: `SFIN.R0000` (6 of 34 days, a new rights issue); `INME`, `CINS`, `HUNT`, `SLND`, `SWAD` (all `.N0000`: RSI hardly moved, so "no value"); `PEARL` (24 of 34 days, a young coin); and six coins CoinPaprika doesn't match, mostly tokenised funds: `BUIDL`, `EURSAFO`, `FIGR_HELOC`, `JAAA`, `USTB`, `USYC`. Those are looked for again after a week.
    - Rejected: CoinGecko (see Open issues), Binance (robots.txt disallows the whole API host), Kraken (its API terms page could not be read).
    - On any 401, 402, 403 or 429 the source is left alone for 60 minutes, no retries. Runs continue where they stopped. Coin matches are checked against our own price: same ticker, different coin is common. Our JUP is its "Jupiter Exchange Token" at rank 92, while its coin named "Jupiter" is at 2,302, so rank comes before name.
    - **Reading it (2026-11-06): 238 of 295 CSE shares are marked overbought. That's real, not a bug:** ASPI went 20,080 to 21,240 (+5.8%) from 3 to 6 Nov, and 262 of 295 shares rose over three sessions. Stablecoins show a value too, but it only reflects tiny wobbles.
    - **cse.lk's closes aren't adjusted for splits and rights:** 31 shares have a move of 35% or more in the year (MERC.N0000 8,046 to 39.4, CPRT.N0000 3,497 to 50.6). RSI uses absolute changes, so this distorts it, but it fades as Wilder's average forgets. Compared with using only the closes since the jump, today's %K differs by 0.5 or less for most, 3.7 for CPRT.N0000, and **9.1 for PEG.N0000** (-44% on 2026-09-21; 80.3 vs 71.2, which crosses the overbought mark). A new split will do the same for a month or two. Not flagged in the UI, as only one is recent. SEMB.X0000 and SFIN.R0000 move 50% in one tick because they cost 0.2-2.2 rupees.

60. **Foresight, the prediction module: phase 1 (Kanchana, 2026-11-06).** A separate module, `python -m foresight run | test | show | score`, schema `foresight`, no AI (Kanchana's decision). For each section: scenarios with odds and reasons, saved before the outcome, never changed (a database trigger), scored on their date against a "normal week" guess. Plan, rules and results: `docs/foresight-plan.md`.
    - **Markets is the first analyst:** up, within or down by more than the instrument's own usual 7-day move, in 7 days, from past days in the same class and StochRSI zone. New run_all step `foresight`, after `markets_closes`.
    - **Result: StochRSI zones gave no edge on the past year** (skill -0.6% for both CSE and coins, too small to matter). Oversold wasn't followed by more rises. Variations were deliberately not tried, because that finds luck. `show` tells people to read the odds as a normal week.
    - 343 live forecasts saved on 2026-11-06, first checked on 2026-11-13. **Forecasts can't be deleted, by design**, so a method change means a new `method_version`, never editing old rows.
    - The new tables stay with administrators in the catalog until phase 3 (the page). **On a new server: `warehouse setup` adds the schema.**
    - **Next: phase 2, World news** (does a story keep going?), then phase 3, the page.

## Open issues and things to watch

- **CoinGecko, for Kanchana to decide (found 2026-11-06, nothing changed).** Its API terms discourage storing data and bar deriving from it, except under conditions (refresh at least every 24 hours, strong encryption, delete on request). They also require a "Powered by CoinGecko" credit, and `api.coingecko.com/robots.txt` disallows `/api/v3`. `markets run` has stored its prices since 2026-11-03 and shows no credit. Options: add the credit and accept the risk for personal use, take a free key under their Demo plan and re-read the terms, or make CoinPaprika the main source (fewer calls allowed, but its `/v1/tickers` is one call for all coins).
- **CoinPaprika's free plan is personal use only.** If the platform goes to colleagues, it needs a paid plan or another source.

- **The Mac's own drive is nearly full, and PostgreSQL keeps its data there** (Homebrew,
  `/opt/homebrew/var/postgresql@18`; 1.2 GB free on 2026-11-06). Our database is 87 MB, fine for now, but not for
  the GLEIF full file. Backups go to the external drive (`data/backups/`). The plan is to move to a server.
  - **Again on 2026-11-06 it was down to ~350 MB free.** **2026-11-06 the Mac's drive filled up completely** (0 bytes; our database is only 82 MB, the rest was other
    things on the Mac). Commands and tests failed with "No space left on device", and PostgreSQL (with all of
    Kanchana's databases, not only ours) can stop when that happens. Kanchana freed ~1 GB. Watch it with `df -h /`
    and `uv run python -m warehouse pg-check` (warns under 2 GB). The test PostgreSQL now keeps its files in
    `.local/test-postgres/` on the external drive, drops each test's database, and pytest keeps only the last run's
    temp folder.
  - **Evening of 2026-11-06: about 300 MB free again** (1.2 GB that morning). Our database is only 147 MB and PostgreSQL's whole folder 479 MB, so it's not us; `~/Library/Caches` alone is 6.3 GB. Kanchana to free space.

- **Browser crashes in tech intel (2026-11-03), cause not known yet.** In Kanchana's `./run_all.sh` at 05:11 the
  browser died on the first page of `profile` (and in `news`), so all 50 sites were saved as "unreachable" and the
  run then hung. The same fetcher worked fine from Claude's shell on vercel.com, ietf.org and astral.sh.
  - Now (item 21): crashes aren't saved, the stage stops after 3 restarts, and the log line
    `Chromium said: ...` shows the real reason. First report (2026-11-03) only had crawl4ai's wrapper line, so the
    first crash now also logs "full browser error (first crash only)".
  - **Cause found (2026-11-03, run 05:31):** `GPU process exited unexpectedly: exit_code=5`, 3 times, then
    `FATAL: GPU process isn't usable. Goodbye.` (the 03:38 vehicles crash also had `icudtl.dat not found in bundle`).
    Simple pages never start the GPU process, so doctor.py passed and Claude's test runs worked.
  - **Fix tried (item 22):** every browser starts with `--in-process-gpu`, and doctor.py has a "graphics page"
    check, with and without the flag. **Not confirmed on the Mac yet:** Kanchana runs `uv run python doctor.py`
    and then `./run_all.sh tech_update`. If the graphics check still fails: CHROMIUM_ARGS in automation.conf.

- **Scheduling is on hold (Kanchana's choice, 2026-11-03); runs are by hand with `./run_all.sh`.** If it's
  wanted again: `./schedule.sh recommended` from Terminal, then
  `launchctl kickstart gui/$(id -u)/com.crawl4ai.runall.frequent`, and check `./schedule.sh status` (see items 19, 20).
  - If "icudtl.dat not found" or "GPU process isn't usable" comes back, the browser install is broken again:
    `source env.sh && rm -rf "$PLAYWRIGHT_BROWSERS_PATH"/chromium* && uv run crawl4ai-setup`.
- **Browser on the external drive:** `doctor.py` passed on the Mac (2026-11-02): Chromium starts and the drive
  supports symlinks, so `BROWSERS_ON_SYSTEM_DRIVE` isn't needed. If "browser has been closed" errors come
  back, run `uv run python doctor.py` again.
- **ikman speed:** each run logs `ikman: N ads in Xs` (about 4s now). If it often logs `gave up after 120s`,
  ikman may be blocking the browser. Then don't work around it: lower the frequency, or accept fewer runs.
- **`automation.conf` is tracked by git, but Kanchana edits it**, so `git pull` can fail with "Your local
  changes would be overwritten". For now: `git stash && git pull origin main && git stash pop`.
  - Offered, not built yet: an untracked `automation.local.conf` that overrides automation.conf.
- **Yahoo Finance** can rate-limit (401/429). Fallbacks cover it; don't add impersonation.

- **Companies:** the directory has no cross-source matching yet: the same company from Wikidata and GLEIF is two
  rows (the details panel lists other records with the same website). GLEIF has no websites, so most GLEIF rows
  are never enriched. Turning on Companies House / ASIC adds millions of rows without websites (the database grows
  by a few GB: wait for the server, see the disk note above).

## Ideas for next steps (not started)

- **Foresight** (`docs/foresight-plan.md`): phase 1 is built (item 60). Next phase 2, World news, then the page, tech and vehicles; AI last. Open questions for Kanchana are at the end of the plan.
- **The user platform** (`docs/user-platform-plan.md`): phases 1-7 and newsletter reports are built; the AI assistant is switched on last, and phase 8 (the server) waits for Kanchana's go. Open questions are
  listed at the end of the plan.

- `automation.local.conf` for personal settings (see above).
- Alerts, e.g. a price drop on a watched car model, a new tech site in a chosen category, or a big market move.
- Vehicles: more sites, or a details page per ad (mileage and year for ikman).
- Tech intel: better categories, more discovery sources.
- Companies: match records across sources (Wikidata has the LEI as P1278); find websites for GLEIF/CSE companies
  from Wikidata; more company types or countries' open registers (e.g. India's MCA data if its terms allow).
