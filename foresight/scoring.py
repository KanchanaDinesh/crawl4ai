"""How forecasts are scored. Pure functions, no database.

Brier score: for each scenario, (the odds we gave - 1 if it happened, else 0) squared, added up. 0 is perfect and lower is
better. A score means little alone, so it's always compared with the simple guess: skill = 1 - score / simple guess's score.
Above 0 beats the simple guess, 0 is no better, below 0 is worse.

Cases aren't independent: days next to each other share most of the same week, and coins mostly move together. So
"could this be luck?" is answered by resampling whole weeks (a block bootstrap), which is much more cautious than counting
days. Only a range entirely above or below 0 gets a verdict.
"""

import random
from collections import defaultdict
from datetime import date

RESAMPLES = 2000
RANGE = 0.90
MIN_WEEKS_FOR_VERDICT = 8

VERDICTS = {"better": "better than a normal-week guess", "worse": "worse than a normal-week guess",
            "no_difference": "no clear difference from a normal-week guess: any gap could be luck",
            "too_little": "too little history to tell"}


def brier(odds: dict[str, float], actual: str) -> float:
    return sum((p - (1.0 if key == actual else 0.0)) ** 2 for key, p in odds.items())


def skill(score: float, normal_score: float) -> float | None:
    return 1 - score / normal_score if normal_score > 0 else None


def week_of(day: date) -> tuple[int, int]:
    return tuple(day.isocalendar())[:2]


def judge(scored: list[tuple[date, float, float]], seed: int = 42) -> dict:
    """scored: (day, score, simple guess's score) per case. Returns the means, skill, its range and the verdict."""
    weeks: dict[tuple, list[float]] = defaultdict(lambda: [0.0, 0.0, 0])
    for day, score, normal in scored:
        w = weeks[week_of(day)]
        w[0] += score
        w[1] += normal
        w[2] += 1
    out = {"cases": len(scored), "weeks": len(weeks), "score": None, "normal_score": None, "skill": None, "skill_low": None,
           "skill_high": None, "verdict": "too_little", "first_day": None, "last_day": None}
    if not scored:
        return out
    total = [sum(w[i] for w in weeks.values()) for i in range(3)]
    out.update(score=total[0] / total[2], normal_score=total[1] / total[2], skill=skill(total[0], total[1]),
               first_day=min(s[0] for s in scored), last_day=max(s[0] for s in scored))
    if len(weeks) < MIN_WEEKS_FOR_VERDICT:
        return out
    rng = random.Random(seed)                # the same data always gets the same answer
    blocks = list(weeks.values())
    samples = []
    for _ in range(RESAMPLES):
        picked = rng.choices(blocks, k=len(blocks))
        s = skill(sum(w[0] for w in picked), sum(w[1] for w in picked))
        if s is not None:
            samples.append(s)
    samples.sort()
    tail = (1 - RANGE) / 2
    low, high = samples[int(tail * len(samples))], samples[min(len(samples) - 1, int((1 - tail) * len(samples)))]
    out.update(skill_low=low, skill_high=high, verdict="better" if low > 0 else "worse" if high < 0 else "no_difference")
    return out
