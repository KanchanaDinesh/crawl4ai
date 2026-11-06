# Markets

Prices for **Sri Lankan and US stocks and indices, gold, silver, oil, exchange rates and the top 100
cryptocurrencies**, saved on every run so you build up a price history. View them in the dashboard's
**Markets** tab; market and crypto **news** comes from `worldnews` (groups `MARKETS` and `CRYPTO`).

## Commands

```bash
uv run python -m markets run                     # collect everything and save it (about 30 seconds)
uv run python -m markets run --only lk,crypto    # just some groups: us, lk, metals, fx, crypto
uv run python -m markets check                   # same, but only print the results (nothing saved)
uv run python -m markets show --class crypto     # latest saved prices (index, stock_us, stock_lk, commodity, commodity_lkr, fx, crypto)
uv run python -m markets history ASPI --days 30  # one instrument over time
uv run python -m markets closes                  # daily closes for the CSE and crypto, then their Stochastic RSI
uv run python -m markets stochrsi --class crypto --zone oversold   # the latest values (also: overbought, short)
uv run python -m dashboard --open                # Markets tab
```

Run `markets run` as often as you like (for example every 15–60 minutes during market hours). Each run
adds a point to the history and the dashboard charts.

## Stochastic RSI

For every CSE share and every coin we show, on the Markets tab (CSE and Crypto), in the side panel of each row, and in the grid
as the datasets **Stochastic RSI** and **Daily closes**. Numbers only, not advice.

- **The calculation** (`markets/indicators.py`): Wilder's RSI 14, then the stochastic of RSI over 14 days, %K = mean of 3, %D = mean of 3 of %K.
  Everything is on a **0-100 scale** (some charts show 0-1). %K above 80 is marked overbought, below 20 oversold.
- **Needs 34 daily closes** (14 + 14 + 3 + 3; the first %D is possible at 32). With fewer it says
  "not enough history yet (N of 34 days)". Prices or an RSI that didn't move give "no value", never a made-up number.
- **Daily closes** (`markets/closes.py`, table `markets.daily_closes`), never prices from our own runs, which can be from the middle of a day:
  - CSE: cse.lk's chart API, about a year per share, one close per day the share traded. A day's close is only saved after 15:00 Sri Lankan time.
  - Crypto: CoinPaprika's free API, 364 days, the price at 00:00 UTC. The free plan is for **personal use** and requires attribution, so the page says
    "Powered by CoinPaprika". Paid plans start at $99 a month if this becomes more than personal use. It allows about **60 requests an hour**.
- **How long:** CSE, first time about 8 minutes (one call every 1.5 s), then 2 calls a day. A share needs its own call again only if a whole
  trading day was missed. Crypto takes at most 50 coins a run (under 2 minutes), stalest first, so the top 100 needs two runs at least an
  hour apart. A coin that waits loses nothing: one call fills every day it's behind. Each row shows the date of its last close. Stop a run any time; the next continues.
- **When a source says no** (401, 402, 403, 429): it stops at once and leaves that source alone for 60 minutes (`markets.history_refusals`). No retries, no tricks.
- `markets.stoch_rsi` is a table written after each sync, not a view: Wilder's RSI carries each day's average into the next, which is awkward and slow in SQL.
- Shares with a corporate action: cse.lk's closes may not be adjusted for splits or rights. See STATUS.md.

## What's collected

| Group | Instruments | Sources, in order (the first that works is used) |
|---|---|---|
| `lk` | **ASPI** and **S&P SL20**, live during trading (cse.lk's `aspiData`/`snpData`; the daily summary only has finished days), with P/E, P/BV, dividend yield, turnover; **S&P SL20 TRI** (total return, end of day); and **every CSE-listed company**: price, change, volume, turnover, market cap | cse.lk's own API |
| `us` | S&P 500, Dow Jones, Nasdaq Composite, Nasdaq 100, Russell 2000, VIX, plus the `us_stocks` watchlist | Yahoo Finance → Stooq |
| `metals` | Gold, silver, platinum (USD/oz), WTI and Brent crude, plus **gold and silver in LKR** | Yahoo Finance → Stooq |
| `fx` | USD/LKR, EUR/USD, GBP/USD, USD/INR, USD/JPY, US Dollar Index | Yahoo Finance → Stooq → open.er-api.com (daily rates) |
| `crypto` | **Top 100 by market cap**: price, 1h/24h/7d change, market cap, 24h volume | CoinGecko → CoinPaprika |

All sources are free and need no API key. **Yahoo Finance often blocks automated requests.** The
collector doesn't try to get around that: on Yahoo's first "blocked" or "too many requests" answer it
stops asking Yahoo for the rest of the run and uses the next source instead. `run` prints how many
prices came from each source. Stooq gives daily closing prices (today's price while the market is
open), so its prices may lag slightly behind Yahoo's. Requests identify themselves honestly with the
`fetch.user_agent` setting, and the CSE API is called without pretending to be cse.lk's own website.

**Stooq now shows a "verify your browser" puzzle** to automated requests (November 2026). It isn't
worked around: after the first such answer Stooq isn't asked again that run. So for US stocks, indices
and metals there's currently **no working backup** if Yahoo blocks us; exchange rates still have
open.er-api.com.

**The day's change** compares with the last close from an *earlier day*, using the dates of Yahoo's daily
closes. (Yahoo's list sometimes doesn't include today yet; taking "the second-to-last close" then compared
with the day before yesterday.)

**Gold and silver in LKR** (per gram, and gold 24K/22K per pawn of 8 g) are calculated from the
international USD price and USD/LKR. They're a **reference price, not a local jeweller's rate**, which
includes duties and margins.

## Settings (`markets/config/settings.json`)

| Setting | What it does |
|---|---|
| `us_indices`, `us_stocks` | Which US indices and stocks to track. Add any Yahoo symbol, e.g. `"AMD"`, `"BRK-B"` |
| `commodities`, `fx` | Yahoo symbols for commodities and exchange rates (`"metal": "gold"` marks metals converted to LKR) |
| `crypto.top_n` | How many coins (default 100, max 250) |
| `cse.enabled` | Turn the Colombo Stock Exchange off or on |
| `"stooq": "..."` on an instrument | Its Stooq symbol (e.g. `^spx`, `xauusd`, `usdlkr`); US stocks default to `<ticker>.us` |
| `fetch.try_yahoo`, `fetch.stooq_fallback`, `fetch.fx_api_fallback` | Turn individual sources off or on (set `try_yahoo` to `false` if Yahoo always blocks you) |

## Data

PostgreSQL, database `crawl4ai`, schema `markets` (since 2026-11-06; tables in `warehouse/schema/markets.sql`).
The dashboard reads while a run is saving. The old DuckDB file is kept in `data/archive/duckdb-2026-11/` as a backup.

| Table / view | Contents |
|---|---|
| `quotes` | Every price from every run (history) |
| `v_latest` | The latest price of each instrument |
| `v_daily` | The last price of each day (UTC), for longer-term charts |
| `runs`, `source_runs` | Each run and each source's result (to spot sources that stop working) |

Query with `uv run python -m warehouse sql "select * from markets.v_latest where asset_class = 'stock_lk' order by change_pct desc nulls last limit 10"`.

## Notes

- These are free public endpoints without keys. They can rate-limit or change without notice. A price
  that no source could provide shows under "Data sources" in the dashboard; everything else keeps working.
- Prices may be delayed (typically 15 minutes for exchanges). Use them for tracking and research, not trading.
