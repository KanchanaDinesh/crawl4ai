# World news collector

Collects the **latest N headlines** (default 10) from major news sites, grouped by country in
priority order: **United States, India, Sri Lanka, Australia, Canada, China**, then **World**,
**Technology**, **Markets & Business** and **Crypto** (CoinDesk, Cointelegraph, The Block, Decrypt,
Bitcoin Magazine, the Ethereum Foundation and Bitcoin Core blogs, ...). Every headline from every country is also checked for technology topics, so tech
stories from CNN or The Hindu appear in the Technology section too.

Run it manually for now. It's built to be scheduled later.

## Quick start

```bash
uv sync --frozen
uv run crawl4ai-setup                 # one-time: browser for sites that need JavaScript

uv run python -m worldnews check      # quick test of every source (3 headlines each, no report)
uv run python -m worldnews run        # collect the latest 10 per source and write the report
open output/worldnews/latest.html     # macOS
```

Common options:

```bash
uv run python -m worldnews run --limit 25                 # 25 headlines per source
uv run python -m worldnews run --group LK --group TECH    # only some groups
uv run python -m worldnews run --source "CNBC"            # one source
uv run python -m worldnews run --no-article-meta          # faster: skip opening article pages for dates
uv run python -m worldnews report                         # rebuild the report from the last run
uv run python -m worldnews list                           # show all sources
```

## Adding a news site

You only need the homepage. The collector finds the RSS feed itself if the site advertises one:

```bash
uv run python -m worldnews add https://www.example.com/ --name "Example News" --group US
uv run python -m worldnews check --source "Example News"
```

Or edit `worldnews/config/sources.json` directly. Each entry looks like this:

```json
{"name": "CNBC", "group": "US", "homepage": "https://www.cnbc.com/",
 "feeds": ["https://www.cnbc.com/id/100003114/device/rss/rss.html"]}
```

| Field | Meaning |
|---|---|
| `name` | Shown in the report |
| `group` | `US`, `IN`, `LK`, `AU`, `CA`, `CN`, `GLOBAL`, `TECH`. Add more groups (and change their order) in `settings.json` → `groups` |
| `homepage` | The site's front page |
| `feeds` | Optional RSS/Atom feed URLs. The most reliable way to get headlines with dates |
| `selectors` | Optional CSS selectors `{"base", "title", "link"}` for sites without feeds where automatic detection picks the wrong links |
| `limit` | Optional: a different number of headlines for this source |
| `enabled` | Set `false` to switch a source off without deleting it (add `disabled_because` to say why) |

## How each site is read

Methods are tried in order until one returns headlines. The report shows which one worked ("via"):

1. **feed:** the RSS/Atom feeds in `sources.json`. Best: titles, dates and summaries come straight from the site.
2. **selectors:** the site's CSS selectors, if given (used for the Sri Lankan sites we already had working).
3. **discovered feed:** a feed advertised on the homepage. It's remembered in `data/worldnews/feed_cache.json` for next time.
4. **homepage:** article links picked out of the homepage automatically (navigation, tags, login and similar links are skipped).
   Each article page is then opened to read its **publish date and summary**, so the newest come first.

If plain HTTP is blocked or the page needs JavaScript, a real browser (crawl4ai) is used.
`robots.txt` is respected for homepages and article pages.

## The report

`output/worldnews/news-<date>_<time>.html` (plus `latest.html`, always the newest):

- Sections per group in priority order, then **Technology**, which includes "Tech headlines from other sections".
- Each source's headlines are **newest first**, with publish time and "how long ago", a short summary, and a **NEW** badge for headlines not seen in earlier runs.
- **Links open in a new tab.**
- A search box, a "New only" switch, and jump links to each country.
- **Sources with no headlines:** each failing source and the reason, so you know what to fix.
- **All headlines, newest first:** one combined timeline across every source.

The same data is saved as `.md`, `.csv` (for Excel or Numbers) and `.json` next to the HTML.

## Data

Stored in the DuckDB database `data/worldnews/worldnews.duckdb` (see the main README):

| Table | Contents |
|---|---|
| `news_items` | Every headline ever collected, once per URL: title, summary, published time, source, country group, topics, first/last seen, times seen |
| `news_runs` | One row per run: limit, sources OK, headlines, new |
| `news_source_runs` | One row per source per run: status, method used, error. Use it to spot sources that keep failing |
| `news_run_items` | Which headlines each run collected, and their position |
| `feed_cache` | Feeds found by auto-discovery |

Each run is also saved as a JSON file in `data/worldnews/runs/`. Query the database with
`uv run python -m warehouse sql "..."`, prefixing tables with `news.`.

Only headlines, summaries, dates and links are stored, not full article text.

## Settings (`worldnews/config/settings.json`)

| Setting | What it does |
|---|---|
| `limit_per_source` | Headlines per source (default 10; `--limit` overrides it) |
| `groups` | Group codes, names and **priority order** in the report |
| `fetch` | Concurrency, timeout, user agent, browser fallback, opening article pages for dates, time limits: `article_meta_max_seconds` (30) for reading article dates, `source_max_seconds` (90) per source, so one slow site can't hold up the run |

The user agent is honest (`worldnews-reader/1.0 …`), never a browser's. Sites whose robots.txt only lets
named crawlers in (Reuters, news.com.au) are switched off rather than worked around.
| `homepage` | Rules for picking article links out of homepages |
| `tech_keywords` | Words that mark a headline as technology |
| `topic_keywords` | Words that mark a headline as `crypto` or `markets` (shown as badges, filterable in the dashboard) |

## Notes

- The feed URLs in `sources.json` are known public feeds, but sites change them. Run
  `check` after updating and fix or remove any that fail. Sites without a working feed still
  work through the homepage method.
- Some sites (Reuters, for example) block automated access. They'll show under "Sources with no headlines".
