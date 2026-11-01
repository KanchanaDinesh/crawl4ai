# Tech intelligence pipeline (prototype)

Finds **new tech websites**, works out **what they do**, **tracks how they change**, and produces an
**analytics report**. Built on crawl4ai. There's no AI API and no database yet: classification is
rule-based and data is stored in JSON Lines files. Both are designed to be swapped later.

```
 1. DISCOVER ─▶ 2. QUALIFY ─▶ 3. PROFILE ─▶ 4. MONITOR ─▶ 5. ANALYZE ─▶ 6. REPORT
 new domains     cheap check:   deep crawl:    re-crawl and    summary      Markdown +
 from sources    tech or not?   what they do   record changes  numbers      HTML report
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
uv run python -m techintel profile  --limit 20
uv run python -m techintel monitor                        # sites not checked for monitor.interval_days
uv run python -m techintel monitor --force --domain foo.ai
uv run python -m techintel report --days 7
```

Re-running is always safe. Each stage only processes what's new or due: discover skips domains it
already knows, qualify skips checked ones until they're due for a re-check, and so on.

## The stages

| Stage | What it does | Reads | Writes |
|---|---|---|---|
| **discover** | Collects domains from the sources below. Keeps only the **first sighting** of each root domain (`app.foo.com.lk` becomes `foo.com.lk`) and drops ignored platforms (GitHub, `*.vercel.app`...) | sources | `candidates.jsonl` |
| **qualify** | Fetches the homepage in cheap **HTTP mode** (browser fallback for JavaScript sites) and decides: **qualified** (real tech site), **watch** ("coming soon", unreachable; re-checked after 7 days) or **rejected** (parked, for sale, not tech, robots.txt says no; re-checked after 30 days) | candidates | `qualifications.jsonl` |
| **profile** | Crawls the homepage plus key pages (about, product, pricing, docs, blog, careers, contact) with a **real browser** and records name, description, category, tech stack, social links, signals (pricing, docs, hiring, open source), keywords and outbound links. Page text is saved under `raw/pages/` | qualified sites | `profiles.jsonl`, `raw/` |
| **monitor** | Re-profiles sites every `interval_days`, compares with the last snapshot, and records changes: started using X, added pricing page, started hiring, new description, went offline... | profiles | `profiles.jsonl`, `changes.jsonl` |
| **analyze** | Turns everything into numbers: funnel, new sites by category, source and TLD, tech stack popularity, signals, keywords, changes, and pipeline health warnings | all | `output/techintel/analysis-<date>.json` |
| **report** | Renders the analysis as Markdown and a standalone HTML page | all | `output/techintel/reports/report-<date>.{md,html}` |

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
| `settings.json` → `qualify` | `min_score`, `min_words`, parked and placeholder phrases, re-check intervals |
| `settings.json` → `profile` | Which key pages to look for, max pages per site, job-board hosts |
| `settings.json` → `crawl` | Politeness: concurrency, delay between requests, robots.txt, user agent |
| `fingerprints.json` | Tech stack detection rules (HTML regex / headers / meta generator) |
| `seeds.txt` | Sites you want evaluated |

## Data layout

```
data/techintel/                 pipeline state (git-ignored)
  candidates.jsonl  qualifications.jsonl  profiles.jsonl  changes.jsonl  runs.jsonl
  raw/pages/<domain>/<hash>.md  page text as crawled, for re-processing
output/techintel/               analysis JSON and reports (git-ignored)
```

Each `.jsonl` file is append-only: a domain's current state is its **latest** row, and older rows
are its history. Override the folders with `TECHINTEL_DATA_DIR` and `TECHINTEL_OUTPUT_DIR`.

## Designed for the next steps

- **Database:** all reads and writes go through `techintel/store.py` (`append`, `read`, `latest`,
  `write_raw_page`). A DuckDB version with the same methods replaces it without touching the stages.
  The JSONL files can be loaded straight into DuckDB with `read_json_auto('data/techintel/*.jsonl')`.
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
- The internet sources (Hacker News, crt.sh, CertStream) are written against their public
  formats but couldn't be run from the development environment. Run them once and check the
  output, then adjust `settings.json` (queries, filters) to your needs.
- Be polite: keep `crawl.delay_seconds` and `concurrency` modest, and leave `check_robots_txt` on.
