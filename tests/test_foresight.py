"""foresight: the scoring, the rules that keep it honest, and the markets analyst, on made-up prices in a throwaway PostgreSQL."""

import json
import random
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest

from foresight.analogues import PastCases
from foresight.analysts.markets import Case, Data, MarketsAnalyst, build_cases, classify, walk_forward, zone_table
from foresight.scoring import brier, judge, skill, week_of

CFG = json.loads((Path(__file__).parent.parent / "foresight" / "config" / "settings.json").read_text())["markets"]
LAST = date(2026, 11, 6)


def walk(seed: int, n: int = 300, step: float = 0.04, start: float = 100.0) -> list[tuple[date, float]]:
    rng = random.Random(seed)
    rows, price = [], start
    for i in range(n):
        price *= 1 + rng.uniform(-step, step)
        rows.append((LAST - timedelta(days=n - 1 - i), price))
    return rows


def data_for(symbols: dict[str, list], group: str = "crypto") -> Data:
    return Data({group: symbols}, {(group, s): s.title() for s in symbols}, {group: max(r[-1][0] for r in symbols.values())})


# ── scoring ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_brier_by_hand():
    assert brier({"up": 1.0, "flat": 0.0, "down": 0.0}, "up") == 0
    assert brier({"up": 0.0, "flat": 1.0, "down": 0.0}, "up") == 2
    assert brier({"up": 0.5, "flat": 0.25, "down": 0.25}, "up") == pytest.approx(0.25 + 0.0625 + 0.0625)
    assert skill(0.5, 0.5) == 0 and skill(0.25, 0.5) == 0.5 and skill(1.0, 0.5) == -1 and skill(0.1, 0) is None


def _scored(week_count, score, normal):
    start = date(2026, 1, 5)
    return [(start + timedelta(days=7 * w + d), score(w), normal) for w in range(week_count) for d in range(5)]


def test_verdict_only_when_the_range_is_clear():
    rng = random.Random(1)
    assert judge(_scored(30, lambda w: 0.40 + rng.uniform(-0.02, 0.02), 0.50))["verdict"] == "better"
    assert judge(_scored(30, lambda w: 0.60 + rng.uniform(-0.02, 0.02), 0.50))["verdict"] == "worse"
    noisy = judge(_scored(30, lambda w: 0.50 + rng.uniform(-0.3, 0.3), 0.50))
    assert noisy["verdict"] == "no_difference" and noisy["skill_low"] < 0 < noisy["skill_high"]
    few = judge(_scored(5, lambda w: 0.10, 0.50))
    assert few["verdict"] == "too_little" and few["skill"] == pytest.approx(0.8)     # the number shows, the verdict waits
    assert judge([])["verdict"] == "too_little"


def test_a_lucky_week_counted_many_times_is_still_one_week():
    """Thousands of days that share a few weeks must not look like thousands of separate cases."""
    start = date(2026, 1, 5)
    lucky = [(start + timedelta(days=7 * w), 0.1 if w < 2 else 0.9, 0.5) for w in range(10) for _ in range(1000 if w < 2 else 1)]
    result = judge(lucky)
    assert result["skill"] > 0 and result["verdict"] != "better"


def test_same_data_same_verdict():
    rows = _scored(20, lambda w: 0.5 + (w % 3) * 0.05, 0.5)
    assert judge(rows) == judge(rows)


def test_past_cases_pull_small_groups_to_the_simple_guess():
    known = PastCases(("up", "down"))
    for i in range(80):
        known.add("a", "down", date(2026, 1, 1) + timedelta(days=i))
    for i in range(20):
        known.add("b", "up", date(2026, 1, 1) + timedelta(days=i))
    assert known.normal() == {"up": 0.2, "down": 0.8}
    odds = known.odds("b", shrink=20)
    assert odds["up"] == pytest.approx((20 + 20 * 0.2) / 40) and sum(odds.values()) == pytest.approx(1)
    assert known.odds("never seen", shrink=20) == known.normal()
    assert known.separate_weeks("b") == len({week_of(date(2026, 1, 1) + timedelta(days=i)) for i in range(20)})
    assert not known.enough("b", 100, 2) and known.enough("a", 80, 2) and not known.enough("a", 80, 50)


# ── what counts as a case ────────────────────────────────────────────────────────────────────────────────────────
def test_scenarios_by_the_instruments_own_usual_move():
    assert (classify(6.1, 6), classify(6, 6), classify(-6, 6), classify(-6.1, 6), classify(0, 6)) == ("up", "flat", "flat", "down", "flat")


def test_outcome_only_when_its_date_has_passed():
    cases, _ = build_cases("X", walk(1), CFG, {}, LAST)
    assert cases[-1].day == LAST and cases[-1].actual is None and cases[-1].check_on == LAST + timedelta(days=7)
    done = [c for c in cases if c.actual]
    assert done[-1].check_on == LAST and all(c.check_on <= LAST for c in done)
    assert all(c.actual is None for c in cases if c.check_on > LAST)


def test_a_case_only_uses_closes_up_to_its_day():
    """Rule 6: change the future, and the past days' zone and band must stay exactly the same."""
    rows = walk(2)
    changed = rows[:250] + [(d, c * 5) for d, c in rows[250:]]
    a, _ = build_cases("X", rows, CFG, {}, LAST)
    b, _ = build_cases("X", changed, CFG, {}, LAST)
    first = {c.day: (c.zone, c.pct_k, c.band) for c in a if c.day < rows[250][0]}
    second = {c.day: (c.zone, c.pct_k, c.band) for c in b if c.day < rows[250][0]}
    assert first == second and first


def test_walk_forward_only_knows_outcomes_whose_date_has_arrived():
    """Rule 6 again, for the odds: a day's score can't change when a later day's outcome changes."""
    symbols = {f"S{i}": walk(i, n=200) for i in range(6)}
    cases = [c for s, rows in symbols.items() for c in build_cases(s, rows, CFG, {}, LAST)[0]]
    scored, _ = walk_forward(cases, CFG)
    cut = max(c.day for c in cases) - timedelta(days=30)
    flipped = []
    for c in cases:
        later = c.check_on > cut
        flipped.append(Case(c.symbol, c.day, c.check_on, c.zone, c.pct_k, c.band, c.close, ("up" if c.actual == "down" else "down") if later and c.actual else c.actual, c.move_pct))
    scored2, _ = walk_forward(flipped, CFG)
    # days whose own 7 days ended before the cut: their score depends only on what was known then, plus their own unchanged outcome
    settled = cut - timedelta(days=7)
    early = [s for s in scored if s[0] <= settled]
    assert early and early == [s for s in scored2 if s[0] <= settled]


def test_stablecoin_and_jumps_and_thin_trading_are_left_out():
    still = [(LAST - timedelta(days=299 - i), 1.0 + (i % 2) * 0.001) for i in range(300)]
    cases, why = build_cases("USDT", still, CFG, {}, LAST)
    assert cases == [] and why[LAST] == "hardly moves"

    rows = walk(3)
    split = rows[:200] + [(d, c / 10) for d, c in rows[200:]]        # a 10-for-1 split, not a crash
    cases, why = build_cases("JKH", split, CFG, {"jump_pct": 40}, LAST)
    bad_day = rows[200][0]
    assert why[bad_day] == "a jump too big to be real trading"
    assert all(not (c.day < bad_day <= c.check_on) for c in cases if c.actual)
    assert rows[230][0] in why                                       # its RSI still carries the jump
    assert rows[260][0] not in why                                   # and later it's fine again
    assert build_cases("BONK", split, CFG, {}, LAST)[0][-1].day == LAST     # no jump rule for a class without one

    thin = [r for i, r in enumerate(walk(3, n=700)) if i % 10 == 0]
    cases, why = build_cases("THIN", thin, CFG, {}, LAST)
    assert cases == [] and "too few trades lately" in why.values()


def test_a_share_without_a_trade_counts_as_unchanged():
    rows = walk(4, n=120)
    rows = rows[:-20] + [rows[-20]]                                  # no trades in the last 19 days
    cases, _ = build_cases("X", rows, {**CFG, "recent_days": 365}, {}, LAST)
    last_full = [c for c in cases if c.day == rows[-1][0]]
    assert last_full and last_full[0].actual == "flat" and last_full[0].move_pct == 0


# ── the test on the past ─────────────────────────────────────────────────────────────────────────────────────────
def test_random_prices_get_no_verdict_of_skill():
    """No method can have skill on pure noise. If this ever says "better", the test is leaking the future."""
    symbols = {f"S{i}": walk(100 + i) for i in range(25)}
    result = MarketsAnalyst(CFG).test(data_for(symbols))[0]
    assert result["cases"] > 1000 and result["verdict"] != "better"


def test_a_real_pattern_is_found():
    start = date(2026, 1, 5)
    cases = []
    rng = random.Random(5)
    for w in range(40):
        for d in range(5):
            day = start + timedelta(days=7 * w + d)
            cases.append(Case("A", day, day + timedelta(days=7), "oversold", 5.0, 3.0, 100.0, "up" if rng.random() < 0.9 else "down", 1.0))
            cases.append(Case("B", day, day + timedelta(days=7), "overbought", 95.0, 3.0, 100.0, "down" if rng.random() < 0.9 else "up", 1.0))
    scored, _ = walk_forward(cases, {**CFG, "min_cases": 20, "min_weeks": 2})
    assert judge(scored)["verdict"] == "better"
    table = zone_table(cases)
    assert table["oversold"]["up"] > 0.8 and table["overbought"]["down"] > 0.8 and table["all"]["cases"] == 400


# ── forecasts, in the database ───────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def pg(pg_env):
    from warehouse import postgres
    postgres.setup()
    return pg_env


def _closes(store, group, symbols):
    for symbol, rows in symbols.items():
        store.save_closes(group, symbol, rows, "test", source_id="1", synced_day=rows[-1][0], error=None)


def _setup(tmp_path, symbols, group="crypto"):
    from foresight.store import ForesightStore
    from markets.store import MarketStore
    from warehouse.postgres import connect
    market = MarketStore(tmp_path)
    market.save_run("2026-11-06T10:00:00Z", "2026-11-06T10:00:05Z",
                    [{"asset_class": group, "symbol": s, "name": s.title(), "price": r[-1][1], "extra": {}, "source": "test"} for s, r in symbols.items()], [])
    _closes(market, group, symbols)
    return market, ForesightStore(), connect("reader", autocommit=True)


def _coins(n=12, length=300):
    return {f"C{i}": walk(i, n=length) for i in range(n)}


def test_run_saves_forecasts_with_odds_reasons_and_a_check_date(tmp_path, pg):
    from foresight.cli import run_section
    market, store, reader = _setup(tmp_path, _coins())
    result = run_section(MarketsAnalyst(CFG), store, reader, LAST)
    assert result["made"] == 12 and result["checked"] == 0
    row = store.con.execute("select as_of, check_on, scenarios, reasons, signals, cases, separate_weeks, subject_name from foresight.forecasts where subject = 'crypto:C1'").fetchone()
    assert row[0] == LAST and row[1] == LAST + timedelta(days=7)
    assert [s["key"] for s in row[2]] == ["up", "flat", "down"]
    assert sum(s["probability"] for s in row[2]) == pytest.approx(1, abs=0.001) and sum(s["normal"] for s in row[2]) == pytest.approx(1, abs=0.001)
    assert row[4]["zone"] in row[3][0] and row[5] >= CFG["min_cases"] and row[6] >= CFG["min_weeks"] and row[7] == "C1"
    assert all(word not in " ".join(row[3]).lower() for word in ("buy", "sell"))
    # a second run on the same data adds nothing and changes nothing
    assert run_section(MarketsAnalyst(CFG), store, reader, LAST)["made"] == 0
    assert store.con.execute("select count(*) from foresight.forecasts").fetchone()[0] == 12
    for c in (market, store, reader):
        c.close()


def test_saved_forecasts_and_outcomes_cant_be_changed(tmp_path, pg):
    from foresight.cli import run_section
    market, store, reader = _setup(tmp_path, _coins())
    run_section(MarketsAnalyst(CFG), store, reader, LAST)
    for statement in ("update foresight.forecasts set start_value = 1", "delete from foresight.forecasts", "truncate foresight.forecasts cascade"):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            store.con.execute(statement)
    fid = store.con.execute("select min(id) from foresight.forecasts").fetchone()[0]
    store.con.execute("insert into foresight.outcomes (forecast_id, actual, score, normal_score) values (%s, 'up', 0.5, 0.6)", [fid])
    for statement in ("update foresight.outcomes set score = 0", "delete from foresight.outcomes", "truncate foresight.outcomes"):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            store.con.execute(statement)
    with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)):
        reader.execute("insert into foresight.runs (section, made, checked) values ('x', 0, 0)")
    for c in (market, store, reader):
        c.close()


def test_check_uses_the_close_on_its_date_however_late(tmp_path, pg):
    from foresight.cli import run_section
    coins = _coins()
    market, store, reader = _setup(tmp_path, coins)
    analyst = MarketsAnalyst(CFG)
    run_section(analyst, store, reader, LAST)
    start = store.con.execute("select start_value, signals from foresight.forecasts where subject = 'crypto:C1'").fetchone()
    band = start[1]["band_pct"]

    # too early: nothing is checked, even with the date passed, until the data reaches it
    assert run_section(analyst, store, reader, LAST + timedelta(days=8))["checked"] == 0

    # the data arrives, with a big move on the check date and a much bigger one later: the later one must not count
    check_on = LAST + timedelta(days=7)
    up = start[0] * (1 + (band + 1) / 100)
    for symbol in coins:
        market.save_closes("crypto", symbol, [(check_on, up if symbol == "C1" else coins[symbol][-1][1]), (check_on + timedelta(days=5), start[0] / 100)],
                           "test", source_id="1", synced_day=check_on, error=None)
    result = run_section(analyst, store, reader, check_on + timedelta(days=9))
    assert result["checked"] == 12
    actual, end_value, end_day, score, normal = store.con.execute("""select o.actual, o.end_value, o.end_day, o.score, o.normal_score from foresight.outcomes o
        join foresight.forecasts f on f.id = o.forecast_id where f.subject = 'crypto:C1'""").fetchone()
    assert (actual, end_day) == ("up", check_on) and end_value == pytest.approx(up) and 0 <= score <= 2 and 0 <= normal <= 2
    # the record counts it; checking again adds nothing
    record = store.con.execute("select checked, skill from foresight.v_track_record").fetchall()
    assert record[0][0] == 12 and record[0][1] is not None
    assert run_section(analyst, store, reader, check_on + timedelta(days=9))["checked"] == 0
    for c in (market, store, reader):
        c.close()


def test_no_forecast_without_enough_cases_or_after_the_date_has_passed(tmp_path, pg):
    from foresight.cli import run_section
    market, store, reader = _setup(tmp_path, {"NEW": walk(9, n=40), "OLD": walk(10, n=60)})
    result = run_section(MarketsAnalyst(CFG), store, reader, LAST)
    assert result["made"] == 0 and sum(result["not_made"].values()) == 2
    assert result["not_made"].get("too few past cases", 0) >= 1
    assert store.con.execute("select count(*) from foresight.forecasts").fetchone()[0] == 0

    # the same data seen 8 days late: its answer is already out, so it's not a forecast
    market2 = _coins()
    for symbol, rows in market2.items():
        market.save_closes("crypto", symbol, rows, "test", source_id="1", synced_day=LAST, error=None)
    late = run_section(MarketsAnalyst(CFG), store, reader, LAST + timedelta(days=8))
    assert late["made"] == 0 and late["not_made"]["its date has already passed"] >= 12 and late["stale"] == ["crypto"]
    for c in (market, store, reader):
        c.close()


def test_it_writes_only_to_its_own_schema(tmp_path, pg):
    from foresight.cli import run_section
    market, store, reader = _setup(tmp_path, _coins())
    before = market.con.execute("select count(*), max(fetched_at) from markets.daily_closes").fetchone()
    run_section(MarketsAnalyst(CFG), store, reader, LAST)
    MarketsAnalyst(CFG).test(MarketsAnalyst(CFG).load(reader))
    assert market.con.execute("select count(*), max(fetched_at) from markets.daily_closes").fetchone() == before
    for c in (market, store, reader):
        c.close()
