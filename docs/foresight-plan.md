# Foresight, the prediction module: plan

_Written 2026-11-06 with Kanchana. Status: **phase 1 built (2026-11-06).** Decided: a separate module, and the first version runs **without AI**._

## What it is for

The system already collects and cleans data. This is the last step: for each section, say what could happen next, how likely
it is, and why. Then check later whether it was right.

No system can really predict the future. So every prediction here is **scenarios with odds**, and each one is **scored afterwards**.
The scoring is the point: after some weeks we can see which sections deserve trust and which don't, and the page says so.

Numbers only, never buy or sell advice.

## One prediction looks like this

> **Bitcoin, in 7 days** (made 2026-11-06, checked on 2026-11-13)
> Up more than 6%: 31% · Within 6%: 44% · Down more than 6%: 25%
> **Why:** %K is 12 (oversold). On 214 past days like this across the coins, this is how the next 7 days went.
> **A normal week** for the coins is 27% / 46% / 27%, so this is only a little different from normal.
> **Track record:** Markets 7-day forecasts are slightly better than the normal-week guess so far (132 checked).

The example's numbers are made up, to show the layout. The real ones come from the data.

## Rules that keep it honest

1. **Saved before the outcome is known, and never edited.** The table is append-only (a database trigger, like the audit log). No forecast is
   ever dropped, including the embarrassing ones.
2. **Every forecast can be checked by the system itself**, from our own data on a fixed date. "Tensions may rise" can't be checked, so it isn't allowed.
3. **Always compared with the simple guess.** A forecast only counts as good if it beats "same as a normal week". Being right when anyone would be
   right isn't skill. Measured with the Brier score against that simple guess, shown in plain words.
4. **Test on the past is kept apart from the live record.** A test on old data is useful to find out if a method has any value, but it can flatter. The
   track record shown to people is live forecasts only.
5. **Too little data means no odds.** Below a minimum number of past cases it says "not enough history yet (N of M)", never a made-up number.
6. **Only data available on that day is used** for a forecast or a test, so tomorrow's numbers can't leak in.
7. **It says so when there's no edge.** A method that doesn't beat the simple guess is shown as "no better than normal", or switched off.

**Expect this:** predicting prices is hard, and the test may well show that StochRSI adds little or nothing. That is a useful result, and it'll be shown as it is.

## What each section can do (real data on 2026-11-06)

| Section | Data today | Can be predicted and checked | Starts |
|---|---|---|---|
| **Markets** | About a year of daily closes for 295 CSE lines and the coins (backfill still finishing); about 6 months in saved Yahoo data for US stocks, indices, metals, FX | Up, flat or down in 7 days, against what's normal for that class | **First**: can be tested on the past at once |
| **World news** | 5,819 headlines from 76 sources, **only 4 days** (since 2026-11-03) | Will this story still be covered in 3 and 7 days? Will it spread to more countries? Will this topic's share grow next week? | **Second**: starts saving at once; first odds after about 3-4 weeks |
| **Tech intel** | 3,027 domains, 6 days | Which category of new sites grows fastest next week | Later |
| **Vehicles** | 414 ads, 4 days | Is a model's asking price drifting? More or fewer ads next week? | Later, after weeks of ads; few ads per model |
| **Companies** | 195,415 rows, first seen in the last 2 days: a directory, not a history | Nothing useful yet | Shows "no predictions yet: this data doesn't change over time" |

### The limit of no AI

Without AI, news forecasts are about **how the coverage behaves** (will this story grow or fade), not about **what happens in the world** ("if this continues,
fuel prices could rise"). Scenarios of that kind need something that can read and reason, which is the AI step in phase 5. Linking sections with numbers (does a
story topic move a price?) is possible without AI, but only with months of data.

## How it's built

A separate module, laid out like the others, and the example for turning the existing tools into modules later:

```
foresight/            python -m foresight run | test | show | score   (no Django in here)
  analysts/           one small file per section: markets.py, news.py, ... each says how to find signals, make scenarios, and check them
  scoring.py          Brier score, the simple guess, the track record
  store.py            schema foresight
  config/             periods, minimum cases, on/off per section
webapp/foresight/     a thin page on top: menu Foresight, its own menu right
```

- **Module rules:** it reads other tools' data only with the read-only login, and writes only to its own schema. It changes nothing in them, and removing it
  breaks nothing.
- **An analyst** has three jobs: `signals()` (what's changing), `forecast()` (scenarios with odds and reasons), `check()` (what really happened). A new
  dataset means one new file, and the framework, scoring and page need no change.
- **Tables** (schema `foresight`): `forecasts` (section, subject, question, scenarios and odds, reasons, method and its version, the simple guess, number of past
  cases, data date, check date), `outcomes` (what happened, score), `backtests` (results of tests on the past), `runs`.
- **A run** first checks forecasts that are due, then makes new ones, at most one per question per day. Runs are by hand, so a missed day just has no forecast, and
  a late check still uses the value from the check date. New run_all step `foresight`, after the collectors.
- **New schema:** add `foresight` to `warehouse.postgres.SCHEMAS`; on the Mac that means `uv run python -m warehouse setup` once.

## Phases

| Phase | What | Done when |
|---|---|---|
| **1. Framework + Markets** | Module, tables, append-only rule, scoring. Markets analyst: similar past days by StochRSI zone, per class. Test on the past year | `foresight test` says plainly whether it beats the simple guess, and daily forecasts are saved |
| **2. World news** | Stories and topics from `worldnews.analysis`; save signals and forecasts from day one, odds appear once enough stories have finished | Coverage forecasts get checked automatically |
| **3. The page** | Menu Foresight: one tab per section with scenarios, reasons, track record; also in the catalog, grid and reports | Kanchana can read it all in the browser |
| **4. Tech, vehicles** | The same analysts, when there's enough history. Companies when it has changes over time | Each section has a forecast or says why not |
| **5. AI (last, by Kanchana's decision)** | Claude writes scenarios in words from the numbers and headlines, including links between sections. Its odds get the same scoring, so we can see if AI adds anything | AI forecasts have a track record next to the plain ones |

## Phase 1 results (2026-11-06)

**Stochastic RSI zones gave no edge on the past year, for either class.** The test used only what was known each day, and resampled whole weeks.

| Class | Scored days | Weeks | Skill against a normal-week guess | 90% range |
|---|---|---|---|---|
| CSE shares | 46,393 | 36 | -0.6% | -1.0% to -0.2% |
| Coins (76 of them) | 16,165 | 39 | -0.6% | -1.1% to -0.0% |

What happened in the 7 days after each zone:

| | Oversold: up / within / down | Middle | Overbought | All days |
|---|---|---|---|---|
| CSE shares | 20 / 55 / 25% | 18 / 53 / 28% | 21 / 55 / 24% | 19 / 54 / 27% |
| Coins | 20 / 51 / 29% | 23 / 55 / 22% | 21 / 54 / 25% | 22 / 53 / 25% |

- So "oversold" was **not** followed by more rises. For coins it was followed by fewer. The method is slightly worse than the simple guess, by an amount too small to matter.
- **Variations were deliberately not tried.** Trying many until one looks good finds luck, not skill. A new idea should be a new method version, with its own test and its own live record.
- It's one year, mostly falling, so this doesn't prove the indicator never works. It does mean nothing here supports trusting it.
- **Live forecasts started:** 343 on 2026-11-06, checked from 2026-11-13. 21 more coins join once CoinPaprika's history is complete. Odds stay within about 4 points of normal, and `show` says to read them that way.
- **Tests that guard this:** changing the future doesn't change past zones, bands or odds (and that test fails when a leak is put in on purpose); pure noise never gets "better"; a real planted pattern is found.

## Things to watch

- **Corporate actions:** cse.lk's closes may not be adjusted for splits or rights, so a split looks like a crash. Such days must be left out of the tests.
- **Thin CSE shares:** many trade rarely, and a share with few trades gets odds only if it has enough cases.
- **Stablecoins** don't really move, so they'd make any crypto forecast look good. They're left out of the scoring.
- **Overlapping weeks:** days next to each other share most of the same 7 days, so 214 similar days is fewer really separate cases. The minimum number of cases allows for this.
- **Every coin or share that exists today survived**: the failed ones are missing from the past data, so tests can look too rosy.
- **CoinPaprika's free plan is for personal use.** Showing this to colleagues may need a paid plan (from $99 a month).
- **AI step:** today's assistant never sends rows to Claude. Phase 5 would send headlines, so it needs Kanchana's separate OK.

## Open questions for Kanchana

1. **How far ahead?** The plan says 7 days. 1 day is mostly noise; 30 days gives far fewer separate cases.
2. **Who sees it?** The plan says administrators first, then a role on the Roles page.
3. **More markets?** The plan starts with CSE and crypto, and could add US stocks and metals using the Yahoo closes we already save.
