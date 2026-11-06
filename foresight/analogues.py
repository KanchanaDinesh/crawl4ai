"""Odds from similar past cases: of the times things looked like this, how did it go?

Shared by the analysts. Odds are pulled towards the simple guess ("how it goes in general") by `shrink` cases, so a group with
few cases can't claim much. This counts only what it's told about, so the caller decides what was known at the time.
"""

from collections import Counter, defaultdict
from datetime import date

from .scoring import brier, week_of


class PastCases:
    def __init__(self, outcomes: tuple[str, ...]):
        self.outcomes = outcomes
        self.counts: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(outcomes, 0))
        self.weeks: dict[str, set] = defaultdict(set)
        self.overall = dict.fromkeys(outcomes, 0)

    def add(self, group: str, outcome: str, day: date) -> None:
        self.counts[group][outcome] += 1
        self.weeks[group].add(week_of(day))
        self.overall[outcome] += 1

    def cases(self, group: str) -> int:
        return sum(self.counts[group].values()) if group in self.counts else 0

    def separate_weeks(self, group: str) -> int:
        return len(self.weeks[group]) if group in self.weeks else 0

    def enough(self, group: str, min_cases: int, min_weeks: int) -> bool:
        return self.cases(group) >= min_cases and self.separate_weeks(group) >= min_weeks

    def normal(self) -> dict[str, float]:
        total = sum(self.overall.values())
        return {k: self.overall[k] / total for k in self.outcomes} if total else {k: 1 / len(self.outcomes) for k in self.outcomes}

    def odds(self, group: str, shrink: float = 20) -> dict[str, float]:
        normal = self.normal()
        counts = self.counts[group] if group in self.counts else dict.fromkeys(self.outcomes, 0)
        n = sum(counts.values())
        if n + shrink <= 0:
            return normal
        return {k: (counts[k] + shrink * normal[k]) / (n + shrink) for k in self.outcomes}


def walk_forward(cases: list, cfg: dict, outcomes: tuple[str, ...]) -> tuple[list[tuple[date, float, float]], PastCases]:
    """Go through the days in order. Each day's odds come only from cases whose check date has already arrived, as they would
    have on that day. A case needs .day, .check_on, .zone (the group it's compared with) and .actual (None if not known yet).
    Returns the scores, and what's known at the end."""
    known = PastCases(outcomes)
    settled = [c for c in cases if c.actual]
    waiting = sorted(settled, key=lambda c: c.check_on)
    next_up = 0
    scored = []
    for case in sorted(settled, key=lambda c: c.day):
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


def zone_table(cases: list, zones: tuple[str, ...], outcomes: tuple[str, ...]) -> dict:
    """What actually happened after each group, with no odds involved: the plainest answer there is."""
    table = {}
    for name in (*zones, "all"):
        counts = Counter(c.actual for c in cases if c.actual and (name == "all" or c.zone == name))
        n = sum(counts.values())
        table[name] = {"cases": n, **{k: (counts[k] / n if n else None) for k in outcomes}}
    return table
