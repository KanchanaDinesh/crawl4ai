"""Which services an article is about, from word lists in settings.json ("tags"), so every tag can be explained.

A word in the title or summary counts at once. In the body it has to come up several times: a bank's annual results that mention
"AI" once aren't an article about AI. An article with no tag isn't kept: that's the filter.

Words are matched whole. One written in capitals in the settings ("AI", "API") only matches in capitals, or "ai" and "api" inside
names and other languages would count too.
"""

import re


def compile_tags(tags: dict[str, list[str]]) -> dict[str, tuple[re.Pattern | None, re.Pattern | None]]:
    out = {}
    for tag, words in tags.items():
        exact = [w for w in words if w.isupper()]
        loose = [w for w in words if not w.isupper()]
        out[tag] = (_pattern(exact, 0), _pattern(loose, re.I))
    return out


def _pattern(words: list[str], flags: int) -> re.Pattern | None:
    if not words:
        return None
    ordered = sorted(words, key=len, reverse=True)
    return re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(w) for w in ordered) + r")(?![\w-])", flags)


def _hits(patterns: tuple, text: str) -> int:
    return sum(len(p.findall(text)) for p in patterns if p)


def tag_article(title: str, summary: str, body: str, compiled: dict, body_hits_needed: int = 3) -> list[str]:
    head = f"{title or ''}. {summary or ''}"
    return sorted(t for t, patterns in compiled.items() if _hits(patterns, head) or _hits(patterns, body or "") >= body_hits_needed)
