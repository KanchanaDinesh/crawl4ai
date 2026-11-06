# Articles

Articles and blogs from the **most valuable companies** of the US, UK, India, Australia and Canada, from their own sites, **tagged by the kind of service** they're about, with the source's own date. Its own section: schema `articles`, linked to each company. Sri Lanka will be separate.

## Commands

```bash
uv run python -m articles run                      # everything that's due (stops after 20 minutes; the next run continues). Also: ./run_all.sh articles
uv run python -m articles show --tag ai --country US
uv run python -m articles show --company Microsoft
uv run python -m articles stats
uv run python -m articles retag                    # after changing the word lists: applies them to what's saved, downloads nothing
uv run python -m articles companies                # read the company lists again (otherwise every 30 days)
uv run python -m articles feeds                    # look for feeds of companies not checked lately
uv run python -m articles collect --country GB     # read the feeds that are due
```

In the web app:
- **Articles** in the side menu (`/articles/`): read and filter by tag, country, company, date, or words anywhere in the text. A second tab, Companies, shows who we can and can't reach. It needs the menu right, plus the right to see the dataset Tech articles.
- **Datasets → Tech articles** and **Tech article companies**: the same data in the grid, for exports, charts and reports.

## What's stored

| Table | One row per | Linked by |
|---|---|---|
| `companies` | company: name, countries, index, sector, website | |
| `feeds` | feed we read | `company_id` |
| `articles` | article: title, summary, text, author, date, `tags` | `company_id`, `feed_url` |

Views: `v_articles` (each article with its company), `v_companies` (each company with its counts).

## Where it comes from

- **The companies:** the members of each country's main index, which are chosen by size: S&P 100, FTSE 100, Nifty 50, S&P/ASX 50, S&P/TSX 60. From Wikipedia's tables through its API, with the website from Wikidata. Exact rankings were checked and not used: Wikidata's values are only good for the US (India has 2 companies), and companiesmarketcap.com's terms need permission for this kind of reuse. `market_cap` is shown for information only: patchy, and sometimes years old.
- **Added by hand:** `extra_companies` in settings, for service companies that aren't in those indexes or aren't listed (OpenAI, Cloudflare, Mailchimp, ...). Marked "added by hand". Not "most valuable" by any list: they're there because the services are what we're after.
- **The articles:** each company's own feeds, which are published for programs. Found from `known_feeds` in settings (each one tested), the site's own `<link>` tags, then the usual places (`/blog/`, `/feed/`, ...), up to 14 requests per company. A feed that was added to the settings is picked up on the next run.
- **The text:** from the feed if it carries the whole article, otherwise from the page, if robots.txt allows it. Plain HTTP, no browser, so a page that only draws itself in a browser stays `short`.

## Tags

Word lists in `config/settings.json`: `cloud`, `ai`, `email`, `web_services`, `security`, `data`, `developer`, `business_apps`, `networking`, `payments`, `ecommerce`, `chips`. An article can have several. A word in the title or summary counts at once, and in the body it has to come up 3 times. **An article with no tag isn't kept: that's the filter**, and the page isn't even opened. Words written in capitals in the settings (`AI`, `API`) only match in capitals.

## The rules

- An honest user agent, one request at a time per site with a pause, robots.txt respected for every page. No browser, no impersonation.
- **A site that answers 401, 403 or 429 is left alone** for that run, and a refusing feed for 14 days. It isn't worked around.
- **The date is the source's own.** If neither the feed nor the page gives one, it stays empty, never the day we collected it.
- **Stored for our own analysis, never republished.** The text is the companies' work.
- Job ads in a feed aren't articles. The same feed at `/feed/` and `/rss/` counts once. Comment feeds are skipped.
- A changed Wikipedia page that gives too few companies is an error, and the old list stays. A company that leaves its index keeps its articles.
- It won't save articles with under 0.5 GB free on the Mac's drive, where PostgreSQL keeps its data.

## Limits

- **Feeds only carry the latest items** (usually 10-50), so history builds up from the first run on.
- **Most big companies have no public feed we can read.** See STATUS.md for how many. Reading their sitemaps would be the next step.
- Tags are word matching: quick and explainable, and sometimes wrong. `email` is the loosest.
