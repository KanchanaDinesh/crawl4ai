# Company directory (`python -m companies`)

A directory of companies of every kind (production, distribution, services, tech, ...), mainly the US, UK,
Australia, India and Sri Lanka, then every other country the sources cover. Two separate steps:

1. **collect**: adds companies: **name, website, country**, plus the source's id and cheap fields it gives
   (industry, registration number, sector, ...). Each run continues where the last one stopped and stops after
   `--max-minutes`, so the directory keeps growing run after run.
2. **update**: reads each company's own website (homepage + about, team and contact pages) and keeps what the
   company says about itself: social account links, people with their roles, employees, emails, phones, founded,
   legal name, address.

```bash
cd /Volumes/MAC-DISK2/crawl4ai && source env.sh
uv run python -m companies collect --max-minutes 5      # add companies (resumable)
uv run python -m companies update --max-minutes 10      # read company websites
uv run python -m companies stats                        # counts per country and source
uv run python -m companies show --country LK --search bank
uv run python -m companies sources                      # which sources are on, where each stopped
```

Both are steps of `./run_all.sh` (`companies_collect`, `companies_update`, in the default STEPS). Options in
`automation.conf`: `COMPANIES_COLLECT_ARGS`, `COMPANIES_UPDATE_ARGS`. The dashboard has a **Companies** tab.

## Sources

Only open data and public APIs, each used the way its owner allows. Settings: `config/settings.json`.

| Source | On? | What it gives | Terms and limits |
|---|---|---|---|
| `wikidata` | on | name, **website**, country, industry: companies with an official website (P856) | CC0. Query service: 60 s of query time per minute per client, honest User-Agent with a contact (ours has the GitHub address). One query per country and company type; after each query we wait as long as it took. Its robots.txt keeps search crawlers off `/sparql`; the service itself is offered to programs under the Wikimedia User-Agent policy |
| `gleif` | on | legal name, country, city, legal form, registration number: legal entities worldwide (no websites) | CC0. 60 requests a minute: we make one every 1.1 s. Only active, ordinary entities (no funds or branches) |
| `cse` | on | Sri Lankan listed companies with **website**, sector, established, board | cse.lk's own API (robots.txt allows it). One request a second |
| `companies_house` | off | every UK company (~5.5 million), SIC industry, no websites | Open Government Licence. 7 zip files of ~70 MB, downloaded one at a time into `data/companies/downloads/` and deleted after import |
| `asic` | off | every Australian company (~3 million), no websites | CC BY 3.0 AU. data.gov.au's robots.txt says no to all robots, so **you** download "Company Dataset - Current CSV" from https://data.gov.au/data/dataset/asic-companies and set `"file"` to its path |
| `sec` | off | US listed companies (name, ticker) | The SEC requires a real contact email in the User-Agent. We never invent one: set `"contact_email"` to turn it on |

**Never used:** LinkedIn, Facebook (only the links a company publishes itself), Crunchbase, OpenCorporates bulk
scraping, search-engine results.

**Countries:** `priority_countries` (US, GB, AU, IN, LK) take turns, and after each round one other country gets a
turn, so the rest of the world moves too. Use ISO codes: `--country GB` (`UK` also works), `--country LK,IN`.

**How resuming works:** each source saves a cursor per country in the `cursors` table after every batch (Wikidata:
the next company type; GLEIF: the next page's link; CSE: the next listed company; Companies House: file and row;
ASIC: byte offset). A finished country starts again after `refresh_days` (30), which picks up new companies.

**Refusals:** a source that answers 401/403/429 is stopped for that run (nothing saved, nothing retried in a
loop); it continues from its cursor next time. A company website that refuses is not saved as a result, only noted
in `refusals` and tried again after `refused_wait_days` (14).

## Turning on the big registers

In `companies/config/settings.json`:

- UK: `"companies_house": {"enabled": true, ...}`: each run imports as many rows as fit in the time limit
  (20,000 per batch). The whole register takes several runs.
- Australia: download the ASIC CSV (about 400 MB), then set `"asic": {"enabled": true, "file": "/path/to/company_YYYYMM.csv"}`.
- SEC: `"sec": {"enabled": true, "contact_email": "you@example.com"}`.

Or run one once without enabling it: `uv run python -m companies collect --source companies_house --max-minutes 20`.

## Update (company websites)

- Plain HTTP, no browser; robots.txt respected; the honest user agent from `settings.json`; one request a second
  per site; 8 sites at a time. Several companies with the same website are read once (one row per domain in `sites`).
- Order: never-read websites first, priority countries first. A site is read again after `recheck_days` (30), one
  that couldn't be read after `retry_failed_days` (7).
- The facts come from tech intel's extractors (`techintel/company.py`), the same as `techintel company`.
- `skip_domains`: "websites" that are really a page on a platform (facebook.com/..., blogspot, ...) aren't read.

## Database

PostgreSQL, database `crawl4ai`, schema `companies` (since 2026-11-06; tables in `warehouse/schema/companies.sql`).
The dashboard and anyone with the read-only login can read while collect and update are writing. Query it with
`uv run python -m warehouse pg "..."` or `psql -U crawl4ai_reader crawl4ai`. The old DuckDB file
`data/companies/companies.duckdb` is no longer used (kept as a backup until the move is finished).

| Table | What |
|---|---|
| `companies` | one row per company per source: `id` (`wikidata:Q95`, `gleif:<LEI>`, `cse:JKH.N0000`, ...), name, country, website, domain, industry, `extra` (the source's own fields), first_seen, last_seen |
| `sites` | the latest website check per domain: status, social, people, emails, phones, employees, founded, ... |
| `refusals` | websites that refused us, and when |
| `cursors` | where each source stopped, per country |
| `runs` | every collect/update run with its summary |
| `v_companies` | companies joined with their website check |

```bash
uv run python -m warehouse pg "select country, count(*), count(website) from companies.companies group by 1 order by 2 desc"
```
