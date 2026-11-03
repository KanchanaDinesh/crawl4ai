# Project status

_Last updated: 2026-11-03. Update this file whenever something meaningful changes._

## In one paragraph

A personal data-collection system on Kanchana's Mac (`/Volumes/MAC-DISK2/crawl4ai`). It collects:
- **Tech intel:** new tech websites, found from certificate logs and Hacker News, then classified, dated,
  profiled and monitored.
- **World news:** the latest headlines from about 80 news sites.
- **Markets:** CSE and US stocks, indices, gold and silver, FX, and the crypto top 100.
- **Vehicles:** the latest ads from ikman.lk and riyasewana.com.

Everything is saved in DuckDB databases inside the project folder and shown in a local web dashboard.
It runs by hand (`./run_all.sh`) or on a schedule (`./schedule.sh recommended`). All 92 tests pass.

## Parts and how to run them

Run every command from the project folder, after `source env.sh`.

| Part | Command | Status |
|---|---|---|
| Tech intel | Two parts: `uv run python -m techintel collect` (discover, qualify, enrich: fast, no browser) and `update` (profile, monitor, report: browser). `run` = both. `quick` is the fast run; `watch` is the live listener | Working, but the browser crashes in Kanchana's Terminal runs (see Open issues). run_all.sh steps `tech_collect` + `tech_update` |
| World news | `uv run python -m worldnews run`, `check`, `list`, `add HOMEPAGE --name N --group CODE` | Working. Groups: US, IN, LK (priority), then AU, CA, CN, GLOBAL, TECH, MARKETS, CRYPTO. Sources: `worldnews/config/sources.json` |
| Markets | `uv run python -m markets run`, `check`, `show`, `history SYMBOL` | Working: 50 of 50 sources, 2s. Yahoo first; Stooq now shows a bot check, so only FX has a backup (open.er-api.com). CoinGecko first, CoinPaprika as backup |
| Vehicles | `uv run python -m vehicles run`, `show [--new] [--search X]`, `changes`, `sites` | Working: Kanchana confirmed 5 ads from ikman. Became its own module on 2026-11-02 (was `scrapers.vehicles`). Own database `data/vehicles/vehicles.duckdb`; older history is copied in from scrapers.duckdb automatically |
| Sri Lankan news | `uv run python -m scrapers.news` (step `lknews`) | Working. The original scraper |
| Dashboard | `uv run python -m dashboard --open` → http://localhost:8050 | Tabs: World News, Tech Intel, Markets, Vehicles. **Restart it after every `git pull`** |
| Database queries | `uv run python -m warehouse info`, `sql "..."`, `examples` | Prefix tables with the database name: `news.`, `tech.`, `scrapers.`, `markets.` |
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

## Open issues and things to watch


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
- **Dashboard after updates:** it must be restarted after `git pull` (Ctrl+C, then `uv run python -m dashboard --open`).
- **Yahoo Finance** can rate-limit (401/429). Fallbacks cover it; don't add impersonation.

## Ideas for next steps (not started)

- `automation.local.conf` for personal settings (see above).
- Alerts, e.g. a price drop on a watched car model, a new tech site in a chosen category, or a big market move.
- Vehicles: more sites, or a details page per ad (mileage and year for ikman).
- Tech intel: better categories, more discovery sources.
