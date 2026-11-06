"""Odds from similar past cases: of the times things looked like this, how did it go?

Shared by the analysts. Odds are pulled towards the simple guess ("how it goes in general") by `shrink` cases, so a group with
few cases can't claim much. This counts only what it's told about, so the caller decides what was known at the time.
"""

from collections import defaultdict
from datetime import date

from .scoring import week_of


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
