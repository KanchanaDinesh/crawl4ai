# Vehicles

The latest vehicle ads from **ikman.lk** and **riyasewana.com**. Every run is saved, so you get a history:
which ads are new since the last run, how long each ad stays up, and price changes.

```bash
source env.sh
uv run python -m vehicles run                      # latest ads from every enabled site
uv run python -m vehicles run --site ikman --limit 5
uv run python -m vehicles show                     # latest saved ads, newest first
uv run python -m vehicles show --new               # only ads first seen in the last run
uv run python -m vehicles show --search vezel      # search title and location
uv run python -m vehicles changes                  # price changes between runs
uv run python -m vehicles sites                    # the configured sites
```

`run` options:

| Option | What it does |
|---|---|
| `--site NAME` | Only this site (repeatable). Default: every enabled site |
| `--limit N` | Ads per site (default: the site's `limit` in the config, 10) |
| `--max-seconds N` | Give up on a site after this long (default 120). The browser gets 60s to start |
| `--save-html` | Also save each page to `output/html_dumps/` |
| `--print` | Also print each site's JSON |

Each site logs how long it took (`ikman: 20 ads in 14.2s`). A site that doesn't load in time is
skipped (`gave up after 120s`) and the next site still runs. A crashed browser is restarted and the
site retried once. If the browser keeps failing, run `uv run python doctor.py`.

## Where things go

| | |
|---|---|
| `vehicles/config/sites.json` | the sites and their CSS selectors |
| `output/vehicles_<site>.json` | the latest run, one file per site |
| PostgreSQL, schema `vehicles` | **every run**: tables `ads`, `runs`; views `v_vehicle_ads`, `v_vehicle_ads_latest` (one row per ad: first seen, times seen), `v_vehicle_price_changes` |

Since 2026-11-06 the history is in PostgreSQL (database `crawl4ai`; tables and views in
`warehouse/schema/vehicles.sql`), so the dashboard reads while a run is saving. It includes everything from the
old `vehicles.duckdb` (kept in `data/archive/duckdb-2026-11/` as a backup), which in turn had the ads saved earlier in
`scrapers.duckdb`.

The dashboard's **Vehicles** tab reads the same data. To run it on a schedule, use the `vehicles` step:
`./run_all.sh vehicles`, or the `frequent` profile (`./schedule.sh every 15m --profile frequent`).
Options for scheduled runs go in `VEHICLES_ARGS` in `automation.conf`.

SQL, for example the cheapest ads:

```bash
uv run python -m warehouse sql "select site, title, price_lkr, url from vehicles.v_vehicle_ads_latest where price_lkr is not null order by price_lkr limit 20"
```

## Adding a site

Add an entry to `vehicles/config/sites.json` with an `item_selector` (one ad), an optional
`exclude_selector` (promoted ads) and `fields`. Each field's `selector` can be a list of fallbacks,
and the first match wins. The docstring at the top of `vehicles/scrape.py` documents every option.

If a site returns 0 ads, its page is saved to `output/html_dumps/<name>.html`. Find the right
selectors with:

```bash
uv run python -m scrapers.inspect_site output/html_dumps/<name>.html --base "<selector>"
```

## Output format

Each site's file (`output/vehicles_<site>.json`) is one JSON object:

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
