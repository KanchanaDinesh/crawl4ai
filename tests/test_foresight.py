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


# ── world news ───────────────────────────────────────────────────────────────────────────────────────────────────
from datetime import datetime  # noqa: E402

from foresight.analysts.news import Case as NewsCase, NewsAnalyst, QUESTIONS, coverage, make_data, size_zone, spread_outcome, story_outcome  # noqa: E402

NEWS_CFG = json.loads((Path(__file__).parent.parent / "foresight" / "config" / "settings.json").read_text())["news"]
D0 = date(2026, 9, 1)


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour)


def headline(day, title, source, group="US", published=None, summary=""):
    return {"url": f"u/{source}/{title}/{day}", "title": title, "summary": summary, "source": source, "group_code": group, "first_seen_at": at(day), "published_at": published}


def every_day(n=30):
    return {D0 + timedelta(days=i) for i in range(n)}


STORY = "volcano erupts iceland flights cancelled reykjavik"


def story_rows(day, sources=5, groups=("US", "IN")):
    return [headline(day, f"Volcano erupts in Iceland as flights cancelled Reykjavik {s}", f"src{s}", groups[s % len(groups)]) for s in range(sources)]


def remembered(rows, runs=None, cfg=NEWS_CFG):
    class Store:
        def save_signals(self, section, r):
            return len(r)
    analyst = NewsAnalyst(cfg)
    data = make_data(rows, runs if runs is not None else every_day(), [], cfg["old_after_days"])
    analyst.remember(data, Store())
    return analyst, data


def test_story_outcomes_by_hand():
    assert (story_outcome(1, 5), story_outcome(0, 2), story_outcome(2, 5), story_outcome(4, 5), story_outcome(5, 5), story_outcome(9, 5)) == ("faded", "faded", "going", "going", "bigger", "bigger")
    assert (spread_outcome(5, 3, 2), spread_outcome(5, 2, 2), spread_outcome(1, 5, 0)) == ("spread", "no_spread", "no_spread")
    assert (size_zone(2), size_zone(3), size_zone(4), size_zone(7), size_zone(8)) == ("2-3 sources", "2-3 sources", "4-7 sources", "4-7 sources", "8+ sources")


def test_first_day_is_a_backlog_and_todays_news_isnt_finished():
    rows = [r for i in range(6) for r in story_rows(D0 + timedelta(days=i))]
    analyst, data = remembered(rows, {D0 + timedelta(days=i) for i in range(6)})
    assert data.last_complete == D0 + timedelta(days=4)             # the newest run's day may not be over
    assert min(data.signals["story"]) == D0 + timedelta(days=2)     # needs two days after the first one
    assert min(data.signals["topic"]) == D0 + timedelta(days=1)
    assert max(data.signals["story"]) == data.last_complete


def test_old_articles_arent_new_coverage():
    day = D0 + timedelta(days=5)
    old = headline(day, "Volcano erupts in Iceland as flights cancelled", "a", published=at(day) - timedelta(days=9))
    new = headline(day, "Volcano erupts in Iceland as flights cancelled", "b", published=at(day) - timedelta(hours=2))
    assert [i["source"] for i in make_data([old, new], every_day(), []).items[day]] == ["b"]


def test_coverage_counts_new_headlines_around_the_check_date():
    check = D0 + timedelta(days=10)
    rows = story_rows(check, 3, ("US", "IN", "LK")) + story_rows(check - timedelta(days=1), 2) + story_rows(check - timedelta(days=2), 9)
    rows += [headline(check, "Iceland football team wins cup", "src8")]     # one shared word isn't this story
    data = make_data(rows, every_day(), [])
    key = ["volcano", "erupts", "iceland", "flights", "cancelled"]
    assert coverage(data, key, check, NEWS_CFG) == (3, 3)                   # src0-2; the day before adds no new source; older ones don't count


def test_nothing_collected_is_never_counted_as_faded():
    """Runs are by hand. If nobody ran it on the check date, the story didn't fade: we just didn't look."""
    start = D0 + timedelta(days=3)
    rows = story_rows(start) + story_rows(start - timedelta(days=1))
    runs = {D0 + timedelta(days=i) for i in range(5)} | {D0 + timedelta(days=20)}   # nothing around day 6 or 10
    analyst, data = remembered(rows, runs)
    assert start in data.signals["story"]
    for question in ("story_3d", "story_7d", "story_spread_7d"):
        assert [c for c in analyst.story_cases(data, question) if c.actual and c.day == start] == []
    subject = next(iter(data.signals["story"][start]))
    p = data.signals["story"][start][subject]
    row = {"id": 1, "subject": f"story:{subject}", "question": "story_7d", "check_on": start + timedelta(days=7), "start_value": p["sources"],
           "signals": {"key_words": p["key_words"], "sources": p["sources"], "countries": p["countries"]},
           "scenarios": [{"key": k, "probability": 1 / 3, "normal": 1 / 3} for k in ("bigger", "going", "faded")]}
    out = analyst.check(row, data)
    assert (out["actual"], out["score"], out["normal_score"]) == ("no_data", None, None)
    # with a run on those days and no matching headline, it really did fade
    analyst2, data2 = remembered(rows, every_day())
    assert analyst2.check(row, data2)["actual"] == "faded"


def test_a_story_that_keeps_going_is_scored_as_going():
    start = D0 + timedelta(days=3)
    rows = story_rows(start, 6) + story_rows(start + timedelta(days=7), 3)
    analyst, data = remembered(rows)
    actual = {c.day: c.actual for c in analyst.story_cases(data, "story_7d")}
    assert actual[start] == "going"


def test_saved_signals_dont_change_when_headlines_are_rewritten():
    start = D0 + timedelta(days=3)
    rows = story_rows(start, 6)
    analyst, data = remembered(rows)
    saved = [("story", d, s, p) for d, found in data.signals["story"].items() for s, p in found.items()]
    cleaned = [{**r, "title": "something else entirely"} for r in rows]
    again = make_data(cleaned, every_day(), saved)
    analyst.remember(again, type("S", (), {"save_signals": lambda self, s, r: len(r)})())
    assert again.signals["story"][start] == data.signals["story"][start]


def _topic_rows(days, share_of_day, per_day=40):
    rows = []
    for i in range(days):
        day = D0 + timedelta(days=i)
        for n in range(per_day):
            title = "Parliament election vote today" if n < share_of_day(i) * per_day else f"Weather stays mild number {n}"
            rows.append(headline(day, title, f"s{n % 5}"))
    return rows


def test_topic_move_uses_its_own_usual_move_and_only_the_past():
    wobble = lambda i: 0.25 + 0.05 * ((i // 7) % 2)              # noqa: E731
    rows = _topic_rows(60, wobble)
    analyst, data = remembered(rows, every_day(60))
    cases = [c for c in analyst.topic_cases(data) if c.subject == "politics"]
    assert cases and all(c.facts["band_pct"] >= 1 for c in cases)
    first = {c.day: (c.zone, c.facts["band_pct"]) for c in cases if c.day < D0 + timedelta(days=40)}
    later = _topic_rows(40, wobble) + _topic_rows(60, lambda i: 0.9)[40 * 40:]       # change the future
    analyst2, data2 = remembered(later, every_day(60))
    second = {c.day: (c.zone, c.facts["band_pct"]) for c in analyst2.topic_cases(data2) if c.subject == "politics" and c.day < D0 + timedelta(days=40)}
    assert first == second and first


def test_topic_needs_enough_collected_to_mean_anything():
    rows = _topic_rows(30, lambda i: 0.3, per_day=2)              # 14 headlines a week
    analyst, data = remembered(rows)
    assert analyst.topic_cases(data) == []


def test_no_news_odds_without_enough_settled_cases():
    rows = [r for i in range(1, 12) for r in story_rows(D0 + timedelta(days=i))]
    analyst, data = remembered(rows, every_day(12))
    made, not_made = analyst.forecast(data, D0 + timedelta(days=11))
    assert made == [] and not_made["too few past cases"] >= 1
    lines = analyst.progress(data)
    assert any("no odds yet" in line for line in lines) and any("Watching" in line for line in lines)


def _many_stories(days=60, keep_going=True):
    """A different story each day, some of them told again a week later."""
    rows = []
    rng = random.Random(3)
    words = [f"word{chr(97 + i)}{chr(97 + j)}" for i in range(26) for j in range(26)]
    for i in range(days):
        day = D0 + timedelta(days=i)
        for s in range(6):
            key = rng.sample(words, 5)
            rows += [headline(day, " ".join(key) + f" {src}", f"src{src}", ("US", "IN", "LK")[src % 3]) for src in range(3 + s % 4)]
            if keep_going and rng.random() < 0.5 and i + 7 < days:
                rows += [headline(day + timedelta(days=7), " ".join(key) + f" later {src}", f"src{src}") for src in range(3)]
    return rows


def test_news_forecasts_appear_once_there_is_history_and_get_checked():
    rows = _many_stories()
    analyst, data = remembered(rows, every_day(60))
    today = D0 + timedelta(days=59)
    made, _ = analyst.forecast(data, today)
    assert {m["question"] for m in made} >= {"story_3d", "story_7d", "story_spread_7d"}
    m = next(x for x in made if x["question"] == "story_7d")
    assert m["subject"].startswith("story:") and m["as_of"] == data.last_complete and m["check_on"] == data.last_complete + timedelta(days=7)
    assert [s["key"] for s in m["scenarios"]] == ["bigger", "going", "faded"] and sum(s["probability"] for s in m["scenarios"]) == pytest.approx(1, abs=0.001)
    assert m["cases"] >= NEWS_CFG["min_cases"] and m["separate_weeks"] >= NEWS_CFG["min_weeks"]
    assert all(word not in " ".join(m["reasons"]).lower() for word in ("buy", "sell"))
    results = analyst.test(data)
    assert {r["question"] for r in results} == set(QUESTIONS) and all(r["verdict"] in ("better", "worse", "no_difference", "too_little") for r in results)
    assert next(r for r in results if r["question"] == "story_7d")["details"]["zones"]["all"]["cases"] > 100


def test_news_end_to_end_saves_signals_and_keeps_no_data_on_the_record(tmp_path, pg):
    from foresight.cli import run_section
    from foresight.store import ForesightStore
    from warehouse import postgres
    from warehouse.postgres import connect
    postgres.migrate()
    store, reader = ForesightStore(), connect("reader", autocommit=True)
    writer = store.con
    rows = _many_stories(40)
    writer.cursor().executemany("insert into news.news_items (url, title, summary, source, group_code, first_seen_at) values (%s, %s, %s, %s, %s, %s)",
                                [(r["url"], r["title"], "", r["source"], r["group_code"], at(r["first_seen_at"].date()).strftime("%Y-%m-%d %H:%M:%S+00")) for r in rows])
    runs = [(d,) for d in sorted({r["first_seen_at"].date() for r in rows})]
    writer.cursor().executemany("insert into news.news_runs (run_at) values (%s)", [(f"{d[0]} 12:00:00+00",) for d in runs])
    analyst = NewsAnalyst(NEWS_CFG)
    today = D0 + timedelta(days=40)
    first = run_section(analyst, store, reader, today)
    assert first["progress"]
    saved = writer.execute("select count(*), count(distinct day) from foresight.signals where section = 'news' and kind = 'story'").fetchone()
    assert saved[0] > 50 and saved[1] > 30
    assert run_section(analyst, store, reader, today)["made"] == 0                  # a second run adds nothing
    assert writer.execute("select count(*) from foresight.signals where section = 'news' and kind = 'story'").fetchone()[0] == saved[0]
    made = writer.execute("select count(*) from foresight.forecasts where section = 'news'").fetchone()[0]
    assert made == first["made"] and made > 0
    # the check date passes, later runs exist, but nothing was collected around it
    writer.cursor().executemany("insert into news.news_runs (run_at) values (%s)", [(f"{D0 + timedelta(days=60)} 12:00:00+00",)])
    run_section(analyst, store, reader, D0 + timedelta(days=61))
    kinds = dict(writer.execute("select o.actual, count(*) from foresight.outcomes o join foresight.forecasts f on f.id = o.forecast_id group by 1").fetchall())
    assert kinds.get("no_data", 0) > 0
    record = writer.execute("select sum(not_checkable) from foresight.v_track_record where section = 'news'").fetchone()[0]
    assert record == kinds["no_data"]
    store.close()
    reader.close()
