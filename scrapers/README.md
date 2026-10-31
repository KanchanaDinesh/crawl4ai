# My scrapers

Personal scrapers built on [crawl4ai](../README.md). Everything specific to this fork lives in this
folder. The rest of the repository is upstream `unclecode/crawl4ai` and stays untouched, so it can be
updated with a plain merge (see [Updating crawl4ai](#updating-crawl4ai)).

## Setup

```bash
uv sync                 # or: pip install -e .
crawl4ai-setup          # one-time: installs the Playwright browser
```

`aiohttp` and `beautifulsoup4` are already crawl4ai dependencies, so nothing else is needed.

## Running

Run everything from the repository root as modules:

| Command | What it does | Output (in `output/`) |
|---|---|---|
| `python -m scrapers.news` | Headlines from the enabled sites in `config/news_sites.json` | `news.json` |
| `python -m scrapers.news --list` | Show the configured sites and whether each is on | – |
| `python -m scrapers.news --site "Daily Mirror" --site CBSL` | Only these sites, even if disabled | `news.json` |
| `python -m scrapers.news --all` | Include disabled sites (broadcom, veeam, fastcompany) | `news.json` |
| `python -m scrapers.products` | Product listings from `config/product_sites.json` | `products_*.json` |
| `python -m scrapers.imdb` | IMDb Top 250 + release calendar, merged into the existing file | `imdb.json` |
| `python -m scrapers.cse summary` | CSE daily market summary (ASPI, S&P SL20, turnover) | `cse_market_summary.json` |
| `python -m scrapers.cse trades` | CSE per-company trade summary | `cse_trade_summary.csv` |
| `python -m scrapers.inspect_site <url\|site name\|file.html>` | Find or test CSS selectors for a new site | `html_dumps/` with `--save-html` |

With `uv`, prefix commands with `uv run`, e.g. `uv run python -m scrapers.news`.

`output/` is git-ignored. Set `SCRAPERS_OUTPUT_DIR` to write somewhere else.

## Layout

```
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
3. Add an entry to `config/news_sites.json` and run `python -m scrapers.news --site <name>`.
   The `--base`/`--title`/`--link` options map to `base_selector`/`title_selector`/`url_selector` in the config.

**Product site**: add an entry with a crawl4ai `schema` to `config/product_sites.json`
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

This fork tracks upstream. To pull a new crawl4ai release:

```bash
git remote add upstream https://github.com/unclecode/crawl4ai.git   # once
git fetch upstream
git merge upstream/main
```

Because nothing outside `scrapers/`, `output/` and the `.gitignore` entry is changed, these merges
should apply cleanly. If `uv.lock` conflicts, take upstream's copy:
`git checkout upstream/main -- uv.lock`.
