"""World news: how the coverage behaves. Without AI this says nothing about what will happen in the world, only whether a story
keeps being told, and which topics grow. All of that can be checked from our own headlines.

    story_3d, story_7d    two days of coverage around the check date, compared with now: bigger, still going, or faded
    story_spread_7d       is it being reported from more countries than now?
    topic_7d              is the topic's share of all headlines up, flat or down over the next week, by its own usual move?

Stories come from worldnews.analysis.stories (same headline words from different sources), over two days. Odds come from past
stories of the same size, or topics moving the same way.

Three traps in this data, and what's done about them:
  * Runs are by hand. A day with nothing collected doesn't mean a story faded, so a check window without a run is never scored as
    faded: a past case is left out, and a saved forecast is recorded as no_data, without a score. It can't be dropped either.
  * The first collection scooped up whatever was on each homepage, so the first day isn't a normal day. Stories start after it. A
    headline published long before we first saw it isn't new coverage either.
  * Only finished days (UTC) are used: a day counts as finished once a later run exists, so morning and evening runs agree.

Each day's stories and topic counts are saved once (foresight.signals), because re-clustering every past day on every run gets slow,
and because `worldnews clean` can rewrite old headlines later.
"""

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median

from worldnews.analysis import GROUP_NAMES, content_words, stories, text_of

from .. import analogues
from ..scoring import brier, judge

METHOD_STORY, METHOD_TOPIC = "story_size", "topic_momentum"
METHOD_VERSION = 1
GROW = ("bigger", "going", "faded")
SPREAD = ("spread", "no_spread")
MOVE = ("up", "flat", "down")
MIN_SOURCES = 2
MIN_TOPIC_BAND = 1.0            # percent: a usual move smaller than this is just rounding of small counts
QUESTIONS = {"story_3d": dict(days=3, outcomes=GROW, zones=("2-3 sources", "4-7 sources", "8+ sources"), label="Stories in 3 days"),
             "story_7d": dict(days=7, outcomes=GROW, zones=("2-3 sources", "4-7 sources", "8+ sources"), label="Stories in 7 days"),
             "story_spread_7d": dict(days=7, outcomes=SPREAD, zones=("0-1 countries", "2 countries", "3+ countries"), label="Stories spreading to more countries in 7 days"),
             "topic_7d": dict(days=7, outcomes=MOVE, zones=("rising", "steady", "falling"), label="Topics' share in 7 days")}
OUTCOME_LABELS = {"bigger": "bigger", "going": "still going", "faded": "faded", "spread": "spread", "no_spread": "didn't", "up": "up", "flat": "within", "down": "down"}


def utc_day(moment) -> date:
    return moment.date() if hasattr(moment, "date") else moment


def size_zone(sources: int) -> str:
    return "2-3 sources" if sources <= 3 else "4-7 sources" if sources <= 7 else "8+ sources"


def country_zone(countries: int) -> str:
    return "0-1 countries" if countries <= 1 else "2 countries" if countries == 2 else "3+ countries"


def countries_of(groups) -> int:
    return sum(1 for g in set(groups) if g in GROUP_NAMES)


def story_outcome(sources_later: int, sources_now: int) -> str:
    return "faded" if sources_later < MIN_SOURCES else "bigger" if sources_later >= sources_now else "going"


def spread_outcome(sources_later: int, countries_later: int, countries_now: int) -> str:
    return "spread" if sources_later >= MIN_SOURCES and countries_later > countries_now else "no_spread"


def move_outcome(move_pct: float, band: float) -> str:
    return "up" if move_pct > band else "down" if move_pct < -band else "flat"


def signature(key_words: list[str]) -> str:
    return hashlib.sha1(" ".join(sorted(key_words)).encode()).hexdigest()[:12]


@dataclass
class Case:
    subject: str
    day: date
    check_on: date
    zone: str
    actual: str | None = None
    facts: dict = field(default_factory=dict)


@dataclass
class Data:
    items: dict[date, list[dict]]
    run_days: set[date]
    signals: dict[str, dict[date, dict[str, dict]]]     # {kind: {day: {subject: payload}}}
    first_day: date | None
    last_complete: date | None
    last_day: dict[str, date]
    _coverage: dict = field(default_factory=dict)


def make_data(rows: list[dict], run_days: set[date], signal_rows: list[tuple], old_after_days: int = 3) -> Data:
    """rows: headlines with first_seen_at. Headlines long older than when we first saw them aren't new coverage."""
    items: dict[date, list[dict]] = defaultdict(list)
    for r in rows:
        seen, published = r["first_seen_at"], r.get("published_at")
        if published and published < seen - timedelta(days=old_after_days):
            continue
        items[utc_day(seen)].append({**r, "words": set(content_words(r.get("title") or ""))})
    signals: dict = {"story": defaultdict(dict), "topic": defaultdict(dict)}
    for kind, day, subject, payload in signal_rows:
        signals.setdefault(kind, defaultdict(dict))[day][subject] = payload
    first = min(run_days) if run_days else None
    last = max(run_days) - timedelta(days=1) if run_days else None     # the newest run's day may not be over
    return Data(dict(items), set(run_days), signals, first, last, {"news": last} if last else {})


def coverage(data: Data, key_words: list[str], check_on: date, cfg: dict) -> tuple[int, int] | None:
    """New headlines on the check date and the day before that share enough of the story's words: (sources, countries).
    None if nothing was collected on those days, which says nothing about the story."""
    window = [check_on - timedelta(days=d) for d in range(cfg["window_days"])]
    if not any(d in data.run_days for d in window):
        return None
    cache_key = (tuple(key_words), check_on)
    if cache_key not in data._coverage:
        key, need = set(key_words), min(cfg["min_match_words"], len(key_words))
        matched = [i for d in window for i in data.items.get(d, []) if len(key & i["words"]) >= need]
        data._coverage[cache_key] = (len({i.get("source") for i in matched}), countries_of(i.get("group_code") for i in matched))
    return data._coverage[cache_key]


def stories_of_day(data: Data, day: date, cfg: dict) -> dict[str, dict]:
    """The biggest stories of the two days ending on `day`, and the biggest Sri Lankan ones, which world news would crowd out."""
    window = [i for d in range(cfg["window_days"]) for i in data.items.get(day - timedelta(days=d), [])]
    found = [s for s in stories(window, min_sources=MIN_SOURCES) if len(s["key_words"]) >= cfg["min_story_words"]]
    chosen = found[:cfg["top_stories"]] + [s for s in found[cfg["top_stories"]:] if "LK" in s["groups"]][:cfg["top_lk_stories"]]
    return {signature(s["key_words"]): {"key_words": s["key_words"], "headline": s["headline"], "sources": len(s["sources"]),
                                        "countries": countries_of(s["groups"]), "groups": s["groups"]} for s in chosen}


def topic_counts(items: list[dict], patterns: dict) -> dict[str, dict]:
    counts = {t: 0 for t in patterns}
    for i in items:
        text = text_of(i)
        for t, p in patterns.items():
            if p.search(text):
                counts[t] += 1
    return {t: {"count": n, "total": len(items)} for t, n in counts.items()}


def topic_share(data: Data, topic: str, day: date, cfg: dict) -> float | None:
    """The topic's share of headlines over the week ending on this day, if enough was collected for that to mean anything."""
    days = [day - timedelta(days=d) for d in range(cfg["topic_window_days"])]
    saved = [data.signals["topic"].get(d, {}).get(topic) for d in days]
    if sum(1 for s in saved if s) < cfg["topic_min_run_days"]:
        return None
    total = sum(s["total"] for s in saved if s)
    if total < cfg["topic_min_headlines"]:
        return None
    return sum(s["count"] for s in saved if s) / total * 100


def topic_moves(data: Data, topic: str, cfg: dict) -> dict[date, float]:
    """Each day's change in share against a week earlier, in percent. Uses nothing after that day."""
    moves = {}
    for day in sorted(data.signals["topic"]):
        now, before = topic_share(data, topic, day, cfg), topic_share(data, topic, day - timedelta(days=cfg["topic_window_days"]), cfg)
        if now is not None and before:
            moves[day] = (now / before - 1) * 100
    return moves


class NewsAnalyst:
    section = "news"

    def __init__(self, config: dict):
        self.cfg = config
        self._patterns = None

    @property
    def patterns(self) -> dict:
        if self._patterns is None:
            from worldnews.settings import load_settings
            topics = load_settings().section("analysis").get("topics") or {}
            self._patterns = {t: re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")(?![\w-])", re.I)
                              for t, words in topics.items() if words}
        return self._patterns

    def load(self, con) -> Data:
        saved = con.execute("select kind, day, subject, payload from foresight.signals where section = 'news'").fetchall()
        latest_topic = max((d for k, d, _, _ in saved if k == "topic"), default=date.min)
        # summaries are only needed for days whose topics haven't been counted yet
        cur = con.execute("""select url, title, case when (first_seen_at at time zone 'UTC')::date > %s then summary end as summary, source, group_code,
                published_at at time zone 'UTC' as published_at, first_seen_at at time zone 'UTC' as first_seen_at
            from news.news_items order by first_seen_at""", [latest_topic])
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        run_days = {r[0] for r in con.execute("select distinct (run_at at time zone 'UTC')::date from news.news_runs").fetchall()}
        return make_data(rows, run_days, saved, self.cfg["old_after_days"])

    def remember(self, data: Data, store) -> int:
        """Save the finished days that aren't saved yet. Stories start after the first day, whose count is a backlog."""
        if not data.last_complete:
            return 0
        rows = []
        day = data.first_day + timedelta(days=1)
        while day <= data.last_complete:
            if day in data.run_days:
                if day not in data.signals["topic"]:
                    counts = topic_counts(data.items.get(day, []), self.patterns)
                    data.signals["topic"][day] = counts
                    rows += [("topic", day, t, c) for t, c in counts.items()]
                if day >= data.first_day + timedelta(days=self.cfg["window_days"]) and day not in data.signals["story"]:
                    found = stories_of_day(data, day, self.cfg)
                    data.signals["story"][day] = found
                    rows += [("story", day, s, p) for s, p in found.items()]
            day += timedelta(days=1)
        return store.save_signals(self.section, rows)

    # ── cases ────────────────────────────────────────────────────────────────────────────────────────────────
    def story_cases(self, data: Data, question: str) -> list[Case]:
        days = QUESTIONS[question]["days"]
        cases = []
        for day, found in data.signals["story"].items():
            check_on = day + timedelta(days=days)
            for subject, s in found.items():
                zone = country_zone(s["countries"]) if question == "story_spread_7d" else size_zone(s["sources"])
                case = Case(subject, day, check_on, zone, facts=s)
                if data.last_complete and check_on <= data.last_complete:
                    later = coverage(data, s["key_words"], check_on, self.cfg)
                    if later is None:
                        continue                  # nothing collected then: says nothing about the story
                    case.actual = (spread_outcome(later[0], later[1], s["countries"]) if question == "story_spread_7d"
                                   else story_outcome(later[0], s["sources"]))
                cases.append(case)
        return cases

    def topic_cases(self, data: Data) -> list[Case]:
        step = self.cfg["topic_window_days"]
        cases = []
        for topic in sorted({t for day in data.signals["topic"].values() for t in day}):
            moves = topic_moves(data, topic, self.cfg)
            for day in sorted(moves):
                past = [abs(m) for d, m in moves.items() if day - timedelta(days=self.cfg["topic_band_days"]) < d <= day]
                if len(past) < self.cfg["topic_band_moves"]:
                    continue
                band = max(median(past), MIN_TOPIC_BAND)
                zone = {"up": "rising", "flat": "steady", "down": "falling"}[move_outcome(moves[day], band)]
                check_on = day + timedelta(days=step)
                case = Case(topic, day, check_on, zone, facts={"band_pct": band, "share": topic_share(data, topic, day, self.cfg), "move_pct": moves[day]})
                if data.last_complete and check_on <= data.last_complete:
                    later = topic_share(data, topic, check_on, self.cfg)
                    if later is None:
                        continue
                    case.actual = move_outcome((later / case.facts["share"] - 1) * 100, band) if case.facts["share"] else None
                cases.append(case)
        return cases

    def cases(self, data: Data, question: str) -> list[Case]:
        return self.topic_cases(data) if question == "topic_7d" else self.story_cases(data, question)

    # ── the analyst's jobs ───────────────────────────────────────────────────────────────────────────────────
    def signals(self, data: Data, today: date) -> list[dict]:
        """The stories being watched, biggest first."""
        latest = max(data.signals["story"], default=None)
        if not latest:
            return []
        return [{"day": latest, "subject": s, **p} for s, p in sorted(data.signals["story"][latest].items(), key=lambda x: -x[1]["sources"])]

    def forecast(self, data: Data, today: date):
        from collections import Counter
        made, not_made = [], Counter()
        if not data.last_complete:
            return made, not_made
        as_of = data.last_complete
        for question, q in QUESTIONS.items():
            cases = self.cases(data, question)
            _, known = analogues.walk_forward(cases, self.cfg, q["outcomes"])
            for case in (c for c in cases if c.day == as_of):
                if as_of < today - timedelta(days=self.cfg["fresh_days"]):
                    not_made["the news is too old: run the collector"] += 1
                elif case.check_on <= today:
                    not_made["its date has already passed"] += 1
                elif not known.enough(case.zone, self.cfg["min_cases"], self.cfg["min_weeks"]):
                    not_made["too few past cases"] += 1
                else:
                    made.append(self._row(question, q, case, known))
        return made, not_made

    def _row(self, question: str, q: dict, case: Case, known) -> dict:
        odds, normal = known.odds(case.zone, self.cfg["shrink"]), known.normal()
        n, weeks = known.cases(case.zone), known.separate_weeks(case.zone)
        f = case.facts
        if question == "topic_7d":
            band = f["band_pct"]
            labels = {"up": f"Share up more than {band:.0f}%", "flat": f"Within {band:.0f}%", "down": f"Share down more than {band:.0f}%"}
            reasons = [f"{f['share']:.1f}% of this week's headlines, {f['move_pct']:+.0f}% against last week: {case.zone}.",
                       f"From {n:,} past days of topics moving the same way, across {weeks} separate weeks."]
            name, method, start = case.subject.replace("_", " ").title(), METHOD_TOPIC, f["share"]
            subject, grp, signals = f"topic:{case.subject}", "topics", {"band_pct": band, "zone": case.zone, "move_pct": round(f["move_pct"], 2)}
        else:
            if question == "story_spread_7d":
                labels = {"spread": f"Reported from more than {f['countries']} of our countries", "no_spread": "Not reported from more countries"}
            else:
                labels = {"bigger": f"Bigger: {f['sources']} or more sources", "going": f"Still going: 2 to {max(f['sources'] - 1, 2)} sources", "faded": "Faded: under 2 sources"}
            reasons = [f"Told by {f['sources']} sources from {f['countries']} of our countries in the last {self.cfg['window_days']} days.",
                       "Words: " + ", ".join(f["key_words"]) + ".",
                       f"From {n:,} past stories like this, across {weeks} separate weeks."]
            name, method, start = f["headline"], METHOD_STORY, f["sources"]
            subject, grp = f"story:{case.subject}", "stories"
            signals = {"key_words": f["key_words"], "sources": f["sources"], "countries": f["countries"], "zone": case.zone}
        return {"section": self.section, "grp": grp, "subject": subject, "subject_name": name, "question": question, "as_of": case.day, "check_on": case.check_on,
                "start_value": start, "scenarios": [{"key": k, "label": labels[k], "probability": round(odds[k], 4), "normal": round(normal[k], 4)} for k in q["outcomes"]],
                "reasons": reasons, "signals": signals, "method": method, "method_version": METHOD_VERSION, "cases": n, "separate_weeks": weeks}

    def check(self, row: dict, data: Data) -> dict | None:
        """Scored from what was collected around the check date, however late the check runs."""
        if not data.last_complete or row["check_on"] > data.last_complete:
            return None
        question, signals = row["question"], row["signals"]
        if question == "topic_7d":
            later = topic_share(data, row["subject"].split(":", 1)[1], row["check_on"], self.cfg)
            actual = None if later is None or not row["start_value"] else move_outcome((later / row["start_value"] - 1) * 100, signals["band_pct"])
        else:
            found = coverage(data, signals["key_words"], row["check_on"], self.cfg)
            later = None if found is None else found[0]
            if found is None:
                actual = None
            elif question == "story_spread_7d":
                actual = spread_outcome(found[0], found[1], signals["countries"])
            else:
                actual = story_outcome(found[0], signals["sources"])
        if actual is None:                        # the date passed with too little collected: on the record, not scored
            return {"forecast_id": row["id"], "end_value": None, "end_day": row["check_on"], "move_pct": None, "actual": "no_data", "score": None, "normal_score": None}
        return {"forecast_id": row["id"], "end_value": later, "end_day": row["check_on"], "move_pct": None, "actual": actual,
                "score": brier({s["key"]: s["probability"] for s in row["scenarios"]}, actual),
                "normal_score": brier({s["key"]: s["normal"] for s in row["scenarios"]}, actual)}

    def test(self, data: Data) -> list[dict]:
        results = []
        for question, q in QUESTIONS.items():
            cases = self.cases(data, question)
            scored, _ = analogues.walk_forward(cases, self.cfg, q["outcomes"])
            grp = "topics" if question == "topic_7d" else "stories"
            results.append({"section": self.section, "grp": grp, "label": q["label"], "question": question, "method": METHOD_TOPIC if grp == "topics" else METHOD_STORY,
                            "method_version": METHOD_VERSION, **judge(scored),
                            "details": {"zones": analogues.zone_table(cases, q["zones"], q["outcomes"]), "labels": {k: OUTCOME_LABELS[k] for k in q["outcomes"]},
                                        "note": f"{sum(1 for c in cases if c.actual):,} settled so far"}})
        return results

    def progress(self, data: Data) -> list[str]:
        """How far each question is from giving odds. Needs enough settled cases from separate weeks, so it can't be rushed."""
        days = sorted(data.signals["story"])
        lines = []
        if days:
            lines.append(f"Watching {len(data.signals['story'][days[-1]])} stories on {days[-1]}, saved since {days[0]}.")
        need = f"{self.cfg['min_cases']} cases from {self.cfg['min_weeks']} separate weeks"
        for question, q in QUESTIONS.items():
            _, known = analogues.walk_forward(self.cases(data, question), self.cfg, q["outcomes"])
            ready = sum(1 for z in q["zones"] if known.enough(z, self.cfg["min_cases"], self.cfg["min_weeks"]))
            settled = sum(known.overall.values())
            lines.append(f"{q['label']}: no odds yet, {settled:,} cases settled, {ready} of {len(q['zones'])} groups have the {need} they need."
                         if ready < len(q["zones"]) else f"{q['label']}: ready, {settled:,} cases settled.")
        return lines
