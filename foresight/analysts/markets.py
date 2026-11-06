"""Markets: where a price stands in 7 days, given its Stochastic RSI zone today.

Scenarios: up by more than its usual week, within it, or down by more than it. "Its usual week" (the band) is the median size of
that instrument's own 7-day moves so far, so a sleepy share and a wild coin can be counted together, and roughly half of all
weeks land inside the band by construction.

Odds: of the past days in the same class and zone whose 7 days are over, how did it go? The simple guess is the same count over
all zones. The test on the past and the live forecasts share build_cases and PastCases, so the test can't flatter by being
cleverer than the real thing.

A case is left out when its numbers can't be trusted: a stablecoin or a share that hardly moves, a share with too few trades
lately, or a jump too big to be real trading (a CSE share's closes may not be adjusted for splits and rights).
"""

from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from markets.indicators import MIN_CLOSES, stoch_rsi_series, zone

from ..analogues import PastCases
from ..scoring import VERDICTS, brier, judge

OUTCOMES = ("up", "flat", "down")
METHOD = "stochrsi_zone"
METHOD_VERSION = 1


@dataclass
class Case:
    symbol: str
    day: date
    check_on: date
    zone: str
    pct_k: float
    band: float                  # percent
    close: float
    actual: str | None = None    # None while its 7 days aren't over
    move_pct: float | None = None


@dataclass
class Data:
    history: dict[str, dict[str, list[tuple[date, float]]]]     # {group: {symbol: [(day, close), ...]}}
    names: dict[tuple[str, str], str]
    last_day: dict[str, date]


def classify(move_pct: float, band: float) -> str:
    return "up" if move_pct > band else "down" if move_pct < -band else "flat"


def close_on(days: list[date], closes: list[float], day: date) -> tuple[date, float] | None:
    """The last close on or before a day: a share without a trade still stands at its last price."""
    i = bisect_right(days, day) - 1
    return (days[i], closes[i]) if i >= 0 else None


def weekly_moves(days: list[date], closes: list[float], horizon: int) -> list[float | None]:
    """Each day's move over the previous `horizon` days, in percent. Uses nothing after that day."""
    out: list[float | None] = []
    for i, day in enumerate(days):
        j = bisect_right(days, day - timedelta(days=horizon)) - 1
        out.append((closes[i] / closes[j] - 1) * 100 if j >= 0 else None)
    return out


def jump_days(closes: list[float], jump_pct: float | None) -> list[int]:
    if not jump_pct:
        return []
    return [i for i in range(1, len(closes)) if abs(closes[i] / closes[i - 1] - 1) * 100 > jump_pct]


def build_cases(symbol: str, history: list[tuple[date, float]], cfg: dict, group_cfg: dict, last_data_day: date) -> tuple[list[Case], dict[date, str]]:
    """Every day of one instrument that can be a case, and why the others can't. Each case's zone and band only use closes up to
    that day; its outcome is only filled in if its check date has passed in the data."""
    days = [d for d, _ in history]
    closes = [c for _, c in history]
    horizon = cfg["horizon_days"]
    series = stoch_rsi_series(closes)["pct_k"]
    moves = weekly_moves(days, closes, horizon)
    jumps = jump_days(closes, group_cfg.get("jump_pct"))
    cases, why = [], {}
    for i, day in enumerate(days):
        if i + 1 < MIN_CLOSES or series[i] is None:
            why[day] = "not enough closes or no value"
            continue
        past = [abs(m) for m in moves[max(0, i - cfg["band_closes"] + 1):i + 1] if m is not None]
        if len(past) < cfg["min_band_moves"]:
            why[day] = "not enough closes or no value"
            continue
        band = median(past)
        if band < cfg["min_band_pct"]:
            why[day] = "hardly moves"
            continue
        recent = bisect_right(days, day) - bisect_right(days, day - timedelta(days=cfg["recent_days"]))
        if recent < cfg["min_recent_closes"]:
            why[day] = "too few trades lately"
            continue
        if any(i - MIN_CLOSES < j <= i for j in jumps):    # its RSI still carries the jump
            why[day] = "a jump too big to be real trading"
            continue
        case = Case(symbol, day, day + timedelta(days=horizon), zone(series[i]), series[i], band, closes[i])
        if case.check_on <= last_data_day:
            end = bisect_right(days, case.check_on) - 1
            if any(i < j <= end for j in jumps):           # the outcome would be the split, not the market
                why[day] = "a jump too big to be real trading"
                continue
            case.move_pct = (closes[end] / closes[i] - 1) * 100
            case.actual = classify(case.move_pct, band)
        cases.append(case)
    return cases, why


def walk_forward(cases: list[Case], cfg: dict) -> tuple[list[tuple[date, float, float]], PastCases]:
    """Go through the days in order. Each day's odds come only from cases whose check date has already arrived, as they would
    have on that day. Returns the scores, and what's known at the end."""
    known = PastCases(OUTCOMES)
    waiting = sorted((c for c in cases if c.actual), key=lambda c: c.check_on)
    next_up = 0
    scored = []
    for case in sorted((c for c in cases if c.actual), key=lambda c: c.day):
        while next_up < len(waiting) and waiting[next_up].check_on <= case.day:
            done = waiting[next_up]
            known.add(done.zone, done.actual, done.day)
            next_up += 1
        if known.enough(case.zone, cfg["min_cases"], cfg["min_weeks"]):
            scored.append((case.day, brier(known.odds(case.zone, cfg["shrink"]), case.actual), brier(known.normal(), case.actual)))
    while next_up < len(waiting):
        done = waiting[next_up]
        known.add(done.zone, done.actual, done.day)
        next_up += 1
    return scored, known


def zone_table(cases: list[Case]) -> dict:
    """What actually happened after each zone, with no odds involved: the plainest answer there is."""
    table = {}
    for name in ("oversold", "middle", "overbought", "all"):
        counts = Counter(c.actual for c in cases if c.actual and (name == "all" or c.zone == name))
        n = sum(counts.values())
        table[name] = {"cases": n, **{k: (counts[k] / n if n else None) for k in OUTCOMES}}
    return table


class MarketsAnalyst:
    section = "markets"
    question = "price_7d"

    def __init__(self, config: dict):
        self.cfg = config

    def load(self, con) -> Data:
        history: dict = {}
        for grp, symbol, day, close in con.execute("""select asset_class, symbol, day, close from markets.daily_closes
                where asset_class = any(%s) order by asset_class, symbol, day""", [list(self.cfg["groups"])]):
            history.setdefault(grp, {}).setdefault(symbol, []).append((day, close))
        names = {(g, s): n for g, s, n in con.execute("select asset_class, symbol, name from markets.v_latest where asset_class = any(%s)",
                                                      [list(self.cfg["groups"])]).fetchall()}
        last_day = {g: max(rows[-1][0] for rows in symbols.values()) for g, symbols in history.items()}
        return Data(history, names, last_day)

    def _cases(self, data: Data, grp: str) -> tuple[dict[str, list[Case]], dict[str, dict[date, str]]]:
        by_symbol, why = {}, {}
        for symbol, rows in data.history.get(grp, {}).items():
            by_symbol[symbol], why[symbol] = build_cases(symbol, rows, self.cfg, self.cfg["groups"][grp], data.last_day[grp])
        return by_symbol, why

    def signals(self, data: Data, today: date) -> list[dict]:
        """Each instrument's latest zone and usual week."""
        out = []
        for grp in data.history:
            by_symbol, _ = self._cases(data, grp)
            for symbol, cases in by_symbol.items():
                if cases:
                    c = cases[-1]
                    out.append({"group": grp, "symbol": symbol, "day": c.day, "zone": c.zone, "pct_k": c.pct_k, "band": c.band, "close": c.close})
        return out

    def forecast(self, data: Data, today: date) -> tuple[list[dict], Counter]:
        made, not_made = [], Counter()
        for grp in data.history:
            label = self.cfg["groups"][grp]["label"]
            by_symbol, why = self._cases(data, grp)
            everything = [c for cases in by_symbol.values() for c in cases]
            _, known = walk_forward(everything, self.cfg)
            for symbol, cases in by_symbol.items():
                last_close_day = data.history[grp][symbol][-1][0]
                if last_close_day < data.last_day[grp] - timedelta(days=self.cfg["fresh_days"]):
                    not_made["no recent close"] += 1
                    continue
                if not cases or cases[-1].day != last_close_day:
                    not_made[why[symbol].get(last_close_day, "not enough closes or no value")] += 1
                    continue
                case = cases[-1]
                if case.check_on <= today:           # the answer is already out: that isn't a forecast
                    not_made["its date has already passed"] += 1
                    continue
                if not known.enough(case.zone, self.cfg["min_cases"], self.cfg["min_weeks"]):
                    not_made["too few past cases"] += 1
                    continue
                made.append(self._forecast_row(grp, label, symbol, data, case, known))
        return made, not_made

    def _forecast_row(self, grp: str, label: str, symbol: str, data: Data, case: Case, known: PastCases) -> dict:
        odds, normal = known.odds(case.zone, self.cfg["shrink"]), known.normal()
        labels = {"up": f"Up more than {case.band:.1f}%", "flat": f"Within {case.band:.1f}%", "down": f"Down more than {case.band:.1f}%"}
        n, weeks = known.cases(case.zone), known.separate_weeks(case.zone)
        days = self.cfg["horizon_days"]
        reasons = [f"%K is {case.pct_k:.1f}: {case.zone}.",
                   f"{case.band:.1f}% is this one's usual {days}-day move.",
                   f"From {n:,} past days of {label} in the same zone, across {weeks} separate weeks, compared with all {sum(known.overall.values()):,}."]
        return {"section": self.section, "grp": grp, "subject": f"{grp}:{symbol}", "subject_name": data.names.get((grp, symbol)) or symbol,
                "question": self.question, "as_of": case.day, "check_on": case.check_on, "start_value": case.close,
                "scenarios": [{"key": k, "label": labels[k], "probability": round(odds[k], 4), "normal": round(normal[k], 4)} for k in OUTCOMES],
                "reasons": reasons, "signals": {"pct_k": round(case.pct_k, 2), "zone": case.zone, "band_pct": case.band},
                "method": METHOD, "method_version": METHOD_VERSION, "cases": n, "separate_weeks": weeks}

    def check(self, row: dict, data: Data) -> dict | None:
        """Uses the close on the check date, however late the check runs."""
        grp, symbol = row["subject"].split(":", 1)
        rows = data.history.get(grp, {}).get(symbol)
        if not rows or data.last_day[grp] < row["check_on"]:
            return None
        days, closes = [d for d, _ in rows], [c for _, c in rows]
        end = close_on(days, closes, row["check_on"])
        if end is None or not row["start_value"]:
            return None
        move = (end[1] / row["start_value"] - 1) * 100
        actual = classify(move, row["signals"]["band_pct"])
        return {"forecast_id": row["id"], "end_value": end[1], "end_day": end[0], "move_pct": move, "actual": actual,
                "score": brier({s["key"]: s["probability"] for s in row["scenarios"]}, actual),
                "normal_score": brier({s["key"]: s["normal"] for s in row["scenarios"]}, actual)}

    def test(self, data: Data) -> list[dict]:
        results = []
        for grp in data.history:
            by_symbol, _ = self._cases(data, grp)
            everything = [c for cases in by_symbol.values() for c in cases]
            scored, _ = walk_forward(everything, self.cfg)
            results.append({"section": self.section, "grp": grp, "label": self.cfg["groups"][grp]["label"], "question": self.question,
                            "method": METHOD, "method_version": METHOD_VERSION, **judge(scored), "details": {"zones": zone_table(everything), "symbols": len(by_symbol)}})
        return results


__all__ = ["MarketsAnalyst", "VERDICTS"]
