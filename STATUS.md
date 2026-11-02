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
It runs by hand (`./run_all.sh`) or on a schedule (`./schedule.sh recommended`). All 72 tests pass.

## Parts and how to run them

Run every command from the project folder, after `source env.sh`.

| Part | Command | Status |
|---|---|---|
| Tech intel | `uv run python -m techintel run`. Stages: `discover`, `qualify`, `enrich`, `profile`, `monitor`, `report`, `status`. `quick` is the fast run; `watch` is the live listener | Working. Full run is time-limited (`--limit 50 --max-minutes 20` in automation.conf) |
| World news | `uv run python -m worldnews run`, `check`, `list`, `add HOMEPAGE --name N --group CODE` | Working. Groups: US, IN, LK (priority), then AU, CA, CN, GLOBAL, TECH, MARKETS, CRYPTO. Sources: `worldnews/config/sources.json` |
| Markets | `uv run python -m markets run`, `check`, `show`, `history SYMBOL` | Working. Yahoo is used first, with fallback to Stooq and open.er-api.com. CoinGecko is used first, with fallback to CoinPaprika |
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
the script on the drive directly (see history item 20). **Not confirmed yet:** Kanchana runs
`./schedule.sh recommended` from Terminal (Claude isn't allowed to install launchd jobs), then checks that
`tail -n 3 ~/Library/Logs/crawl4ai/watch.log` ends with "listener started".

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

## Open issues and things to watch

- **Scheduled runs: confirm they work (2026-11-03).** See history items 19 and 20. Full Disk Access is on. Next:
  Kanchana runs `./schedule.sh recommended` from Terminal, then
  `launchctl kickstart gui/$(id -u)/com.crawl4ai.runall.frequent` to run the 15-minute job at once. `./schedule.sh status` should show no
  "Problem" lines, the listener running, and a `(scheduled)` run. If a scheduled vehicles run still fails,
  `tail -3 logs/history.log` shows why.
  - If "icudtl.dat not found" or "GPU process isn't usable" comes back, the browser install is broken again:
    `source env.sh && rm -rf "$PLAYWRIGHT_BROWSERS_PATH"/chromium* && uv run crawl4ai-setup`.
- **Browser on the external drive:** `doctor.py` passed on the Mac (2026-11-02): Chromium starts and the drive
  supports symlinks, so `BROWSERS_ON_SYSTEM_DRIVE` isn't needed. If "browser has been closed" errors come
  back, run `uv run python doctor.py` again.
- **ikman speed:** each run now logs `ikman: N ads in Xs`. If it often logs `gave up after 120s`, try
  `--no-stealth`. If that doesn't help, ikman may be blocking the browser. Then don't work around it:
  lower the frequency, or accept fewer runs.
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
