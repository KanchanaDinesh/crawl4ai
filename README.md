# Web scrapers

Three parts, all built on crawl4ai:

- **`scrapers/`**: news, vehicle ads, products, IMDb and Colombo Stock Exchange scrapers (this page).
- **`techintel/`**: a pipeline that discovers new tech websites, profiles and monitors them, and
  writes analytics reports. See [techintel/README.md](techintel/README.md).
  Quick start: `uv run python -m techintel run`.
- **`worldnews/`**: the latest headlines from major news sites in the US, India, Sri Lanka, Australia,
  Canada, China, plus world and technology news, in one daily report.
  See [worldnews/README.md](worldnews/README.md). Quick start: `uv run python -m worldnews run`.

All three use
[crawl4ai](https://github.com/unclecode/crawl4ai) ([docs](https://docs.crawl4ai.com/)) as an
installed library, pinned in `pyproject.toml`.

## Setup (everything stays inside this folder)

Everything lives inside the project folder, so it can sit on an external drive such as `MAC-DISK2`:

| Folder | What's in it |
|---|---|
| `.venv/` | Python packages (crawl4ai, DuckDB, ...) |
| `.local/` | uv's download cache, the Chromium browser, crawl4ai's cache, and Python if uv downloads one |
| `data/` | **Your databases** (DuckDB) and collected data |
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

## Database (DuckDB)

Each tool stores its data in its own DuckDB file inside `data/`, so they can run at the same time:

| Database | Contents |
|---|---|
| `data/worldnews/worldnews.duckdb` | `news_items` (every headline, first/last seen, times seen), `news_runs`, `news_source_runs` (source health), `news_run_items`, `feed_cache` |
| `data/techintel/techintel.duckdb` | `candidates`, `qualifications`, `registrations`, `profiles`, `changes`, `runs`, plus typed views `v_candidates`, `v_qualifications`, `v_registrations`, `v_profiles`, `v_changes` |
| `data/scrapers/scrapers.duckdb` | `scraper_rows` (every scraper output, every run), plus views `v_vehicle_ads`, `v_vehicle_ads_latest`, `v_vehicle_price_changes`, `v_news_headlines`, `v_products` |

Query all three together (read-only) with the `warehouse` command. Prefix tables with `news.`, `tech.` or `scrapers.`:

```bash
uv run python -m warehouse info                    # sizes, tables, row counts
uv run python -m warehouse examples                # ready-made queries
uv run python -m warehouse sql "select published_at, source, title from news.news_items order by published_at desc limit 20"
uv run python -m warehouse sql "select * from scrapers.v_vehicle_price_changes" --csv price_changes.csv
uv run python -m warehouse sql                     # interactive prompt
```

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
| `python -m scrapers.vehicles` | Latest 10 vehicle ads from ikman.lk and riyasewana.com (separately) | `vehicles_ikman.json`, `vehicles_riyasewana.json` |
| `python -m scrapers.vehicles --site ikman --limit 5` | One site, a different number of ads | `vehicles_ikman.json` |
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
project_paths.py         where data/, .local/ and the databases live
warehouse/               DuckDB helpers and the `python -m warehouse` query tool
data/                    databases and collected data (git-ignored)
output/                  everything the scrapers write (git-ignored)
scrapers/
├── common.py            shared paths, logging, JSON/CSV saving
├── config/
│   ├── news_sites.json  news sites + selectors (toggle with "enabled")
│   ├── product_sites.json  product sites + crawl4ai extraction schemas
│   └── vehicle_sites.json  vehicle ad sites + selectors
├── news.py              news headline scraper
├── products.py          product listing scraper
├── vehicles.py          latest vehicle ads (ikman, riyasewana)
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

**Vehicle ad site**: add an entry to `scrapers/config/vehicle_sites.json` with an
`item_selector` (one ad), an optional `exclude_selector` (promoted ads) and `fields`. Each field's
`selector` can be a list of fallbacks, and the first match wins. If a site returns 0 ads, its page is
saved to `output/html_dumps/<name>.html`; run `python -m scrapers.inspect_site` on that file to find
the right selectors. The file docstring in `scrapers/vehicles.py` documents every option.

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

## Vehicle ads output format

Each site's file (and the printed output) is one JSON object:

```json
{
  "site": "ikman",
  "source_url": "https://ikman.lk/en/ads/sri-lanka/vehicles",
  "fetched_at": "2026-10-31 22:00:00",
  "success": true,
  "count": 10,
  "ads": [
    {
      "rank": 1,
      "title": "Toyota Axio 2015",
      "url": "https://ikman.lk/en/ad/toyota-axio-2015-for-sale-colombo",
      "price": "Rs 6,500,000",
      "location": "Colombo, Cars",
      "posted": "2 mins",
      "image": "https://i.ikman-st.com/....jpg",
      "price_lkr": 6500000
    }
  ]
}
```

riyasewana ads also have `year`, `mileage` and `details` (the raw card text), and are sorted
by ad number, newest first, so older pinned ads are skipped. `price_lkr` is `null` when the price isn't a plain number
(for example "Negotiable").

## Updating crawl4ai

Change the version in `pyproject.toml` (for example `"crawl4ai==0.9.6"`), then:

```bash
source env.sh
uv sync
uv run crawl4ai-setup        # in case the new version needs a newer browser
```

Check the [release notes](https://github.com/unclecode/crawl4ai/releases) for breaking changes first.
