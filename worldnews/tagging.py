"""Topic tagging: marks headlines about technology, crypto and markets, from any source.

Keywords come from settings.json: `tech_keywords` and `topic_keywords` ({"crypto": [...], "markets": [...]}).
Every headline from a group named after a topic (TECH, CRYPTO, MARKETS) also gets that topic.
"""

import re

GROUP_TOPIC = {"TECH": "tech", "CRYPTO": "crypto", "MARKETS": "markets"}


def _pattern(words) -> re.Pattern | None:
    words = sorted({w.lower() for w in words}, key=len, reverse=True)
    return re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in words) + r")(?![\w-])", re.I) if words else None


class TopicTagger:
    def __init__(self, topics: dict[str, list[str]]):
        self.patterns = {topic: p for topic, words in topics.items() if (p := _pattern(words))}

    @classmethod
    def from_settings(cls, raw: dict) -> "TopicTagger":
        return cls({"tech": raw.get("tech_keywords", []), **(raw.get("topic_keywords") or {})})

    def matches(self, item: dict, topic: str = "tech") -> list[str]:
        pattern = self.patterns.get(topic)
        if not pattern:
            return []
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        return sorted({m.lower() for m in pattern.findall(text)})

    def tag(self, item: dict, group: str) -> None:
        topics = set(item.get("topics") or [])
        if group in GROUP_TOPIC:
            topics.add(GROUP_TOPIC[group])
        for topic in self.patterns:
            hits = self.matches(item, topic)
            if hits:
                topics.add(topic)
                item[f"{topic}_terms"] = hits[:6]
        item["topics"] = sorted(topics)


class TechTagger(TopicTagger):
    """Tech-only tagger (kept for compatibility)."""

    def __init__(self, keywords: list[str]):
        super().__init__({"tech": keywords})
