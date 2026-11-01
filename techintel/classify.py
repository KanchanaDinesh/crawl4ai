"""Deciding whether a homepage is a real tech site, and which category it belongs to.

RuleClassifier scores keyword matches; no AI needed. It exposes the same
`assess()` method an LLM-based classifier would, so it can be swapped later.
"""

import re
from dataclasses import dataclass, field

from .pages import PageInfo

# Where a keyword is found matters: the title says more than the footer.
WEIGHTS = {"title": 3.0, "description": 3.0, "headings": 2.0, "text": 1.0}
MAX_TEXT_HITS = 5   # cap repeated body matches so one word can't dominate


@dataclass
class Assessment:
    status: str                 # qualified | watch | rejected
    reason: str
    category: str | None = None
    score: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)
    matched: dict[str, list[str]] = field(default_factory=dict)


def _pattern(phrase: str) -> re.Pattern:
    return re.compile(r"(?<![\w-])" + re.escape(phrase.lower()) + r"(?![\w-])")


class RuleClassifier:
    def __init__(self, categories: dict[str, list[str]], qualify_config: dict):
        self.categories = {name: [(kw, _pattern(kw)) for kw in kws] for name, kws in categories.items()}
        self.parked = [p.lower() for p in qualify_config.get("parked_phrases", [])]
        self.placeholder = [p.lower() for p in qualify_config.get("placeholder_phrases", [])]
        self.parking_hosts = tuple(h.lower() for h in qualify_config.get("parking_hosts", []))
        self.min_words = qualify_config.get("min_words", 80)
        self.min_score = qualify_config.get("min_score", 6)

    def score(self, page: PageInfo) -> tuple[dict[str, float], dict[str, list[str]]]:
        parts = {
            "title": page.title.lower(),
            "description": page.description.lower(),
            "headings": " | ".join(page.headings).lower(),
            "text": page.text.lower(),
        }
        scores: dict[str, float] = {}
        matched: dict[str, list[str]] = {}
        for category, keywords in self.categories.items():
            total = 0.0
            hits = []
            for keyword, pattern in keywords:
                kw_score = 0.0
                for part, weight in WEIGHTS.items():
                    n = len(pattern.findall(parts[part]))
                    if part == "text":
                        n = min(n, MAX_TEXT_HITS)
                    kw_score += n * weight
                if kw_score:
                    total += kw_score
                    hits.append(keyword)
            if total:
                scores[category] = round(total, 1)
                matched[category] = hits
        return scores, matched

    def assess(self, page: PageInfo, final_host: str = "") -> Assessment:
        head = f"{page.title} {page.description} {page.text[:3000]}".lower()

        if final_host and final_host.endswith(self.parking_hosts):
            return Assessment("rejected", f"redirects to parking/marketplace host {final_host}")
        for phrase in self.parked:
            if phrase in head:
                return Assessment("rejected", f"parked: '{phrase}'")
        for phrase in self.placeholder:
            if phrase in head and page.word_count < self.min_words * 3:
                return Assessment("watch", f"placeholder: '{phrase}'")
        if page.word_count < self.min_words:
            return Assessment("watch", f"too little content ({page.word_count} words)")

        scores, matched = self.score(page)
        if not scores:
            return Assessment("rejected", "no tech keywords", scores=scores, matched=matched)
        category = max(scores, key=scores.get)
        total = round(sum(scores.values()), 1)
        if total < self.min_score:
            return Assessment("rejected", f"low tech score ({total} < {self.min_score})",
                              category, total, scores, matched)
        return Assessment("qualified", f"tech score {total}, top category {category}",
                          category, total, scores, matched)
