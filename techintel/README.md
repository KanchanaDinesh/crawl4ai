# Tech intelligence pipeline (prototype)

Finds **new tech websites**, works out **what they do**, **tracks how they change**, and produces an
**analytics report**. Built on crawl4ai. There's no AI API and no database yet: classification is
rule-based and data is stored in JSON Lines files. Both are designed to be swapped later.

```
 1. DISCOVER ─▶ 2. QUALIFY ─▶ 2b. ENRICH ─▶ 3. PROFILE ─▶ 4. MONITOR ─▶ 5. ANALYZE ─▶ 6. REPORT
 new domains     cheap check:   registration   deep crawl:    re-crawl and    summary      Markdown, HTML
 from sources    tech or not?   date (RDAP)    what they do   record changes  numbers      and CSV
```

## Quick start

```bash
uv sync --frozen
uv run crawl4ai-setup                     # one-time: browser for the profile stage

uv run python -m techintel run            # all stages, using the enabled sources
uv run python -m techintel status         # counts and last run of each stage
open output/techintel/reports/            # the reports (macOS)
```

Or run the stages one at a time:

```bash
uv run python -m techintel discover                       # all enabled sources
uv run python -m techintel discover --source hackernews   # one source
uv run python -m techintel discover --source certstream --minutes 10
uv run python -m techintel qualify  --limit 50
uv run python -m techintel enrich                         # registration dates (RDAP)
uv run python -m techintel profile  --limit 20
uv run python -m techintel monitor                        # sites not checked for monitor.interval_days
uv run python -m techintel monitor --force --domain foo.ai
uv run python -m techintel report --days 7
```

Re-running is always safe. Each stage only processes what's new or due: discover skips domains it
already knows, qualify skips checked ones until they're due for a re-check, and so on.

## Running it often: `quick` and `watch`

```bash
uv run python -m techintel quick     # every 10-15 min: new domains (listener queue, Hacker News, seeds) -> qualify -> enrich
uv run python -m techintel watch     # live listener: runs until stopped, queues new domains from the certificate stream
```

- **`watch`** keeps a connection to the Certificate Transparency stream (`sources.certstream.url`) and writes every
  interesting new root domain to `data/techintel/stream/pending.jsonl`, skipping domains already known. It never
  opens the database, so it never blocks other runs. It reconnects on its own and logs one line per batch
  (`watch.batch_minutes`, default 5) plus `stream/status.json`.
- **`quick`** takes the queued domains (the `stream` source), adds Hacker News and your seeds, then qualifies and
  enriches the new ones, within `quick.qualify_max_minutes`. Profiling and monitoring stay in the hourly `run`.
- To run them automatically: `./schedule.sh watch on` and `./schedule.sh every 15m --profile frequent` (see the
  main README, "Automation").
- The public CertStream server isn't always available. For reliable use, run your own
  ([certstream-server-go](https://github.com/d-Rickyy-b/certstream-server-go)) and set `sources.certstream.url`.

## How long does a run take?

Each stage prints progress as it goes (`qualify 12/30 example.ai qualified … (eta 1m20s)`), plus a
"still working" line every 20 seconds, and `run` ends with the time per stage. Typical costs:

| Stage | What takes time |
|---|---|
| discover | **crt.sh** can take up to a minute per query (it's a slow public service). Skip it with `--source hackernews --source seeds` |
| qualify | One quick HTTP request per domain, 10 at a time, 15-second timeout. A real browser is used only for pages that load but look empty (JavaScript sites) or answer "forbidden"; never for domains that don't resolve or don't answer |
| enrich | One RDAP lookup per new qualified domain, 3 at a time |
| profile | The slowest: a real browser opens up to 6 pages per site (home, about, pricing, docs, blog, careers), 4 sites at a time, with a pause between pages of the same site |
| monitor | Same as profile, but only for sites last checked more than `monitor.interval_days` ago |

To keep runs short:

```bash
uv run python -m techintel run --limit 20            # at most 20 sites per stage
uv run python -m techintel run --max-minutes 5       # each stage stops starting new sites after 5 minutes
uv run python -m techintel status                    # what's been done, and how long each stage took
```

Every stage saves each site's result as soon as it's finished, so **Ctrl+C is safe**: nothing finished is
lost, and running the same command again continues with what's left. The same goes for `--limit` and
`--max-minutes`: whatever wasn't processed is picked up by the next run.

## The stages

| Stage | What it does | Reads | Writes |
|---|---|---|---|
| **discover** | Collects domains from the sources below. Keeps only the **first sighting** of each root domain (`app.foo.com.lk` becomes `foo.com.lk`) and drops ignored platforms (GitHub, `*.vercel.app`...) | sources | `candidates.jsonl` |
| **qualify** | Fetches the homepage in cheap **HTTP mode** (browser fallback for JavaScript sites) and decides: **qualified** (real tech site), **watch** ("coming soon", unreachable; re-checked after 7 days) or **rejected** (parked, for sale, not tech, robots.txt says no; re-checked after 30 days) | candidates | `qualifications.jsonl` |
| **enrich** | Looks up the **domain registration date** (and registrar) via RDAP for qualified and watched domains. Generic TLDs (.com, .io, .dev, .ai...) usually work; `.lk` has no public RDAP, so its date stays empty | qualified/watch | `registrations.jsonl` |
| **profile** | Crawls the homepage plus key pages (about, product, pricing, docs, blog, careers, contact) with a **real browser** and records name, description, category, tech stack, social links, signals (pricing, docs, hiring, open source), keywords and outbound links. Page text is saved under `raw/pages/` | qualified sites | `profiles.jsonl`, `raw/` |
| **monitor** | Re-profiles sites every `interval_days`, compares with the last snapshot, and records changes: started using X, added pricing page, started hiring, new description, went offline... | profiles | `profiles.jsonl`, `changes.jsonl` |
| **analyze** | Turns everything into numbers: funnel, new sites by category, source and TLD, tech stack popularity, signals, keywords, changes, and pipeline health warnings | all | `output/techintel/analysis-<date>.json` |
| **report** | Renders the analysis as Markdown, a standalone HTML page and a CSV of all discovered domains | all | `output/techintel/reports/report-<date>.{md,html}`, `domains-<date>.csv` |

### Reading the report

- **New tech sites:** qualified sites, **latest first**, numbered, with the website URL as the link
  (opens in a new tab). Three dates per site:
  - **Seen:** when the source saw the site online (Hacker News post time, certificate logged time).
    This is what the list is sorted by. Sources without a date (seeds, outlinks) use the Found date.
  - **Registered:** domain registration date from RDAP, where the registry provides one.
  - **Found:** when this pipeline discovered it. On a first run everything is found within minutes,
    so this date says little about the site itself.
- **All discovered domains:** every domain found in the period with its status (qualified, watch,
  rejected, not checked) and the reason, latest first. Use the search box and status buttons to filter.
  The same list is saved as `domains-<date>.csv` for Excel or Numbers.
- The numbers at the top are links: click "New domains" to jump to the full list.

Every run of every stage is logged in `runs.jsonl`. The report warns you when a source fails or
suddenly finds nothing.

## Discovery sources

| Source | What it is | Needs |
|---|---|---|
| `seeds` | Your own list in `config/seeds.txt` | nothing |
| `hackernews` | "Show HN" launch posts from the last `lookback_hours` (free Algolia HN API) | internet |
| `crtsh` | Certificate Transparency logs via crt.sh: certificates for names matching your `queries` (default `%.lk`) logged in the last `lookback_days` | internet; crt.sh is slow, keep queries narrow |
| `certstream` | **Real-time** stream of every new certificate worldwide, filtered by `domain_filter.match_tlds` / `match_keywords`. Off by default; run it with `--source certstream --minutes N` | a CertStream server (the public one is unreliable; self-host `certstream-server-go` for real use) |
| `outlinks` | Unknown domains linked from sites you've already profiled ("snowball" discovery) | profiles |

To add a source, create `techintel/sources/<name>.py` with `async def collect(ctx)` that returns
`Candidate`s, and register it in `techintel/sources/__init__.py`.

## Configuration (`techintel/config/`)

| File | What you'd change |
|---|---|
| `settings.json` → `sources` | Turn sources on or off; crt.sh queries; HN lookback |
| `settings.json` → `domain_filter` | TLDs and name keywords that look interesting; platforms to ignore |
| `settings.json` → `categories` | **The classifier:** keywords per category. Add categories or terms freely |
| `settings.json` → `qualify` | `concurrency` and `timeout_ms` for the homepage check, `min_score`, `min_words`, parked and placeholder phrases, re-check intervals |
| `settings.json` → `enrich` | Turn RDAP lookups on or off; timeout and politeness |
| `settings.json` → `report` | Period, and how many sites and domains to list (`max_sites_listed`, `max_domains_listed`) |
| `settings.json` → `profile` | Which key pages to look for, max pages per site, job-board hosts |
| `settings.json` → `crawl` | Politeness: concurrency, delay between requests to the same site, robots.txt, user agent, `max_stage_minutes` (time limit per stage) |
| `fingerprints.json` | Tech stack detection rules (HTML regex / headers / meta generator) |
| `seeds.txt` | Sites you want evaluated |

## Data layout

```
data/techintel/techintel.duckdb   all pipeline tables (DuckDB; git-ignored)
data/techintel/raw/pages/<domain>/<hash>.md   page text as crawled, for re-processing
output/techintel/                 analysis JSON and reports (git-ignored)
```

Tables: `candidates`, `qualifications`, `registrations`, `profiles`, `changes`, `runs`. Each row keeps
the full record as JSON; a domain's current state is its **latest** row, and older rows are its history.
Typed views make SQL easy: `v_candidates`, `v_qualifications`, `v_registrations`, `v_profiles` and
`v_changes`. For example:

```bash
uv run python -m warehouse sql "select category, count(*) from tech.v_qualifications where status='qualified' group by 1"
```

Older `.jsonl` files in `data/techintel/` are imported automatically and renamed to `*.jsonl.imported`.
Override the folders with `TECHINTEL_DATA_DIR` and `TECHINTEL_OUTPUT_DIR`.

## Designed for the next steps

- **Database:** all reads and writes go through `techintel/store.py`. `DuckStore` (DuckDB) is used by
  default; `FileStore` (JSON Lines) has the same methods.
- **AI:** `techintel/classify.py` has a `RuleClassifier` with an `assess()` method. An LLM classifier
  with the same method can replace it, and LLM-written profile summaries can be added to `profile.py`.
- **Scheduling:** e.g. `cron` on the Mac: `0 */6 * * * cd /path/to/repo && uv run python -m techintel run`.

## Tests

```bash
uv run pytest
```

Unit tests cover every building block offline. The end-to-end test runs the whole pipeline against
fake websites served on localhost (a tech startup, a parked domain, a bakery, a "coming soon" page,
and a site that blocks crawlers via robots.txt), including change detection and outlink discovery.
No internet is needed.

## Known limits of the prototype

- Classification is keyword-based. It's good at "is this tech, and roughly which area", but it
  can't explain *what's novel*; that's what the AI step is for. Keywords in reports are simple
  frequency counts.
- The internet sources (Hacker News, crt.sh, CertStream) and the RDAP lookup are written against
  their public formats but couldn't be run from the development environment. Run them once and check the
  output, then adjust `settings.json` (queries, filters) to your needs.
- Be polite: keep `crawl.delay_seconds` and `concurrency` modest, and leave `check_robots_txt` on.
