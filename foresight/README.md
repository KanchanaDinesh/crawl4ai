# Foresight

For each section: what could happen next, how likely, and why. Every forecast is **saved before the outcome is known, can't be changed
afterwards, and is scored on its date**, so we can see which sections deserve trust. Scenarios with odds, not promises, and numbers only: never
advice to buy or sell. No AI in this version. Plan and rules: [docs/foresight-plan.md](../docs/foresight-plan.md).

## Commands

```bash
uv run python -m foresight test                     # how the method would have done on the past year
uv run python -m foresight run                      # check what's due, then make today's forecasts (once per day; ./run_all.sh foresight)
uv run python -m foresight show --subject BTC       # the latest forecast, with its reasons, and how far to trust it
uv run python -m foresight show --zone oversold     # also: overbought, middle
uv run python -m foresight score                    # the live record: only forecasts saved before their outcome
```

Markets needs the daily closes first: `uv run python -m markets closes`.

## What the result means so far

**On the past year, Stochastic RSI zones gave no edge** (see the plan's "Phase 1 results"). So the odds are about the same as a normal week, and `show` says so. The useful
part today is each instrument's **usual 7-day move**. Forecasts keep being saved because the live record is the real test, and it costs almost nothing.

## How it works

- `analysts/` has one file per section with four jobs: `load`, `signals`, `forecast`, `check`, plus `test`. A new dataset means one new file. Add it to
  `analysts/__init__.py` and `config/settings.json`.
- `scoring.py`: the Brier score (0 is perfect, lower is better), compared with the simple guess. A verdict comes only if the 90% range, resampling whole weeks,
  is entirely above or below 0.
- `analogues.py`: odds from similar past cases, pulled towards the simple guess when there are few.
- `store.py` + `warehouse/schema/foresight.sql`: schema `foresight`. `forecasts` and `outcomes` are append-only (a database trigger refuses changes).
- **Module rules:** it reads other tools' data with the read-only login and writes only to its own schema. It uses one pure function from `markets/indicators.py`. Removing it breaks nothing.

## Markets

- **Question:** where is the price in 7 days? Up by more than its usual move, within it, or down by more. The usual move is the median size of that instrument's own 7-day moves
  over its last 180 closes, using only data up to that day.
- **Odds:** of the past days in the same class and Stochastic RSI zone whose 7 days are over, how did it go? The simple guess is the same count over all zones.
- **Left out:** stablecoins and shares that hardly move (usual move under 0.5%), shares with under 5 closes in 30 days, and CSE shares near a one-day jump over 40% (cse.lk's closes aren't
  adjusted for splits and rights; smaller bonus issues can still slip through).
- **The check** uses the last close on or before the check date, however late the check runs. A share with no trade stands at its last price.
- **Limits:** one year of data, in which prices mostly fell; instruments that exist today only (the failed ones are missing); coins mostly move together, which is why whole weeks are resampled.
