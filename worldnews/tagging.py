"""Topic tagging: marks headlines about technology, from any source in any country."""

import re


class TechTagger:
    def __init__(self, keywords: list[str]):
        words = sorted({k.lower() for k in keywords}, key=len, reverse=True)
        self.pattern = re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in words) + r")(?![\w-])", re.I) \
            if words else None

    def matches(self, item: dict) -> list[str]:
        if not self.pattern:
            return []
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        return sorted({m.lower() for m in self.pattern.findall(text)})

    def tag(self, item: dict, group: str) -> None:
        hits = self.matches(item)
        topics = set(item.get("topics") or [])
        if group == "TECH" or hits:
            topics.add("tech")
        item["topics"] = sorted(topics)
        if hits:
            item["tech_terms"] = hits[:6]
