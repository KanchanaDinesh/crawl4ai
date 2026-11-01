"""Pull the useful parts out of a page's HTML."""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

STOPWORDS = set("""
a about above after again all also am an and any are as at be because been before being below between both
but by can could did do does doing down during each few for from further had has have having he her here
hers him his how i if in into is it its itself just let me more most my no nor not now of off on once only
or other our ours out over own same she should so some such than that the their theirs them then there these
they this those through to too under until up very was we were what when where which while who whom why will
with would you your yours get got use used using new one two make made way well like see may us also via
learn read view click home menu skip content main page site website contact privacy policy terms cookies
cookie rights reserved copyright all inc ltd llc co login sign log signup free start started today now
""".split())


@dataclass
class PageInfo:
    title: str = ""
    description: str = ""
    site_name: str = ""
    generator: str = ""
    headings: list[str] = field(default_factory=list)
    text: str = ""
    word_count: int = 0

    @property
    def content_hash(self) -> str:
        return hashlib.sha1(re.sub(r"\s+", " ", self.text).strip().encode()).hexdigest()[:16]


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def parse_page(html: str, markdown: str | None = None) -> PageInfo:
    soup = BeautifulSoup(html or "", "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    headings = []
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
        if 2 < len(text) < 200 and text not in headings:
            headings.append(text)

    if markdown:
        text = markdown
    else:
        for tag in soup(["script", "style", "noscript", "svg", "template"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
    text = re.sub(r"[ \t]+", " ", text).strip()

    return PageInfo(
        title=title,
        description=_meta(soup, "description", "og:description", "twitter:description"),
        site_name=_meta(soup, "og:site_name", "application-name"),
        generator=_meta(soup, "generator"),
        headings=headings[:30],
        text=text,
        word_count=len(re.findall(r"\w+", text)),
    )


def strip_shared_lines(texts: list[str]) -> list[str]:
    """Remove lines that appear on more than one page (navigation, footers, cookie banners)."""
    seen: Counter[str] = Counter()
    for text in texts:
        seen.update({line.strip() for line in text.splitlines() if line.strip()})
    return ["\n".join(line for line in text.splitlines() if seen[line.strip()] <= 1) for text in texts]


def top_keywords(texts: list[str], limit: int = 20) -> list[str]:
    """Most frequent meaningful words and two-word phrases."""
    counts: Counter[str] = Counter()
    for text in texts:
        words = [w for w in re.findall(r"[a-z][a-z0-9+#.-]*[a-z0-9+#]|[a-z]", text.lower())
                 if len(w) > 2 and w not in STOPWORDS and not w.isdigit()]
        counts.update(words)
        counts.update(f"{a} {b}" for a, b in zip(words, words[1:]) if a != b)
    # Prefer phrases that repeat; keep single words that are frequent.
    ranked = [(w, c * (1.5 if " " in w else 1)) for w, c in counts.items() if c >= 2]
    ranked.sort(key=lambda x: -x[1])
    return [w for w, _ in ranked[:limit]]
