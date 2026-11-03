"""News analysis: our own numbers about the saved headlines (no AI, nothing sent anywhere).

`analyze(items, previous, config)` looks at the headlines (title + summary) first seen in a period
and works out:
  stories     the same story told by several sources: headlines grouped by the words they share
  topics      politics, economy, conflict, sports, ... (keyword lists in settings.json "analysis"),
              overall and per country group
  people      names of people, organisations and places that come up most (capitalised words in normal sentences)
  countries   countries mentioned, wherever the source is from
  rising      words used much more than in the previous period of the same length
  tone        a rough word-based tone (e.g. "killed", "crisis" vs "wins", "record"), per group
  sri_lanka   the same for Sri Lankan sources and anything mentioning Sri Lanka
  sources     headlines per group and source, and the hour they were published

Everything is counting and matching words, so it is quick and explainable; every number points
back to headlines. It is a summary of what the sources wrote about, never a copy of their articles.
"""

import re
from collections import Counter, defaultdict
from datetime import datetime

STOPWORDS = set("""
a about above after again against all almost also am among amid an and any are around as at back be because been
before being below between both but by can could did do does doing down during each even ever every few for from
further get gets got had has have having he her here hers him his how i if in into is it its itself just last like
make makes many may me more most much must my near new news next no nor not now of off on once one only or other our
out over own per said same say says see she should since so some still such than that the their them then there these
they this those three through to too two under until up upon us very via was way we were what when where which while
who whom why will with within without would year years you your week weeks day days today yesterday tomorrow time
first second set amid man woman people latest top big report reports live update updates video watch photos
know need ahead things thing pct percent cent read look looks here's what's how's breaking exclusive opinion analysis explained
monday tuesday wednesday thursday friday saturday sunday morning evening night tonight
""".split())
# Words written with a capital in headlines that aren't names
NOT_NAMES = set("""
the a an and or but in on at to for of with from by as is are was be this that these those it its new how why what
when who where which after over amid says said will can could may might should would just more most first last top
big live watch video photos update updates breaking exclusive opinion analysis review explained report here there
mr mrs ms dr prof ceo cfo cto coo jan feb mar apr jun jul aug sep sept oct nov dec understand read more monday tuesday wednesday thursday friday saturday sunday january february march april may june
july august september october november december today tonight week year
""".split())
COUNTRIES = {
    "United States": ["united states", "u.s.", "usa", "america", "american", "americans", "washington"],
    "Sri Lanka": ["sri lanka", "sri lankan", "lanka", "colombo"], "India": ["india", "indian", "indians", "new delhi", "delhi"],
    "China": ["china", "chinese", "beijing"], "Russia": ["russia", "russian", "moscow", "kremlin"],
    "Ukraine": ["ukraine", "ukrainian", "kyiv"], "Israel": ["israel", "israeli", "israelis"],
    "Palestine": ["gaza", "palestinian", "palestinians", "west bank", "hamas"], "Iran": ["iran", "iranian", "tehran"],
    "United Kingdom": ["uk", "britain", "british", "united kingdom", "england", "london", "scotland", "wales"],
    "Canada": ["canada", "canadian", "canadians", "ottawa"], "Australia": ["australia", "australian", "australians", "canberra"],
    "Japan": ["japan", "japanese", "tokyo"], "South Korea": ["south korea", "south korean", "seoul"],
    "North Korea": ["north korea", "north korean", "pyongyang"], "Pakistan": ["pakistan", "pakistani", "islamabad"],
    "Bangladesh": ["bangladesh", "bangladeshi", "dhaka"], "Nepal": ["nepal", "nepali", "kathmandu"],
    "Maldives": ["maldives", "maldivian"], "Afghanistan": ["afghanistan", "afghan", "kabul", "taliban"],
    "Germany": ["germany", "german", "berlin"], "France": ["france", "french", "paris"], "Italy": ["italy", "italian", "rome"],
    "Spain": ["spain", "spanish", "madrid"], "Netherlands": ["netherlands", "dutch"], "Poland": ["poland", "polish"],
    "Turkey": ["turkey", "turkish", "ankara", "turkiye", "türkiye"], "Saudi Arabia": ["saudi", "saudi arabia", "riyadh"],
    "United Arab Emirates": ["uae", "emirates", "dubai", "abu dhabi"], "Qatar": ["qatar", "qatari", "doha"],
    "Egypt": ["egypt", "egyptian", "cairo"], "Syria": ["syria", "syrian", "damascus"], "Lebanon": ["lebanon", "lebanese", "beirut"],
    "Iraq": ["iraq", "iraqi", "baghdad"], "Yemen": ["yemen", "yemeni", "houthi", "houthis"],
    "Taiwan": ["taiwan", "taiwanese", "taipei"], "Hong Kong": ["hong kong"], "Singapore": ["singapore", "singaporean"],
    "Malaysia": ["malaysia", "malaysian", "kuala lumpur"], "Indonesia": ["indonesia", "indonesian", "jakarta"],
    "Thailand": ["thailand", "thai", "bangkok"], "Vietnam": ["vietnam", "vietnamese", "hanoi"],
    "Philippines": ["philippines", "filipino", "manila"], "Myanmar": ["myanmar", "burma"],
    "Brazil": ["brazil", "brazilian"], "Mexico": ["mexico", "mexican"], "Argentina": ["argentina", "argentine"],
    "Venezuela": ["venezuela", "venezuelan"], "Colombia": ["colombia", "colombian"], "Chile": ["chile", "chilean"],
    "South Africa": ["south africa", "south african"], "Nigeria": ["nigeria", "nigerian"], "Kenya": ["kenya", "kenyan"],
    "Ethiopia": ["ethiopia", "ethiopian"], "Sudan": ["sudan", "sudanese"], "New Zealand": ["new zealand"],
    "Switzerland": ["switzerland", "swiss"], "Sweden": ["sweden", "swedish"], "Norway": ["norway", "norwegian"],
    "Greece": ["greece", "greek"], "Ireland": ["ireland", "irish"], "Cuba": ["cuba", "cuban"],
    "European Union": ["european union", "eu", "brussels"],
}
# Titles and institutions without a name: "the President", "the Opposition"
TITLES = {"president", "prime minister", "minister", "opposition", "government", "parliament", "police", "court",
          "cabinet", "senate", "house", "state", "city", "council", "ministry", "department", "chief", "king", "queen"}
GROUP_NAMES = {"US": "United States", "IN": "India", "LK": "Sri Lanka", "AU": "Australia", "CA": "Canada", "CN": "China"}


def _pattern(words) -> re.Pattern | None:
    words = sorted({w.lower() for w in words}, key=len, reverse=True)
    return re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in words) + r")(?![\w-])", re.I) if words else None


def text_of(item: dict) -> str:
    return f"{item.get('title') or ''}. {item.get('summary') or ''}"


def content_words(text: str) -> list[str]:
    """Lower-case words that carry meaning (no stopwords, no numbers, at least 3 letters)."""
    words = re.findall(r"[a-z][a-z'’-]*[a-z]", text.lower().replace("’s", "").replace("'s", ""))
    return [w for w in words if len(w) > 2 and w not in STOPWORDS]


def _title_case(title: str) -> bool:
    """"A Right-Wing Think Tank Has a Plan": every word capitalised, so capitals say nothing about names."""
    words = [w for w in re.findall(r"[A-Za-z][\w'’-]*", title) if len(w) > 3]
    return len(words) >= 4 and sum(w[0].isupper() for w in words) / len(words) > 0.7


def _capital(token: str) -> bool:
    base = re.sub(r"['’]s$", "", token.strip(".'’"))
    if base.lower() in NOT_NAMES:
        return False
    return (base[:1].isupper() and not base.isupper()) or (base.isupper() and 2 <= len(base) <= 5 and base.isalpha())


def names_in(text: str) -> list[str]:
    """Runs of capitalised words that aren't the first word of a sentence: "Donald Trump", "Bank of Ceylon"."""
    found = []
    for sentence in re.split(r"(?<=[.!?:;])\s+|\s[-–—|]\s", text):
        tokens = re.findall(r"[\w'’&.-]+", sentence)
        caps = [_capital(t) for t in tokens] + [False]
        run: list[str] = []
        for i, tok in enumerate(tokens + [""]):
            word = tok.strip(".'’")
            base = re.sub(r"['’]s$", "", word)
            joiner = base in ("of", "de", "bin", "al") and run
            # The first word of a sentence always has a capital: it only counts when the next one does too ("Donald Trump")
            if (caps[i] and (i > 0 or caps[i + 1])) or joiner:
                run.append(word)
                continue
            while run and run[-1] in ("of", "de", "bin", "al"):
                run.pop()
            if run:
                run[-1] = re.sub(r"['’]s$", "", run[-1])          # "Trump's" -> "Trump", but "People's Bank" stays
                found.append(" ".join(run))
            run = []
    return [n for n in found if len(n) > 2]


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def stories(items: list[dict], min_sources: int = 2, min_shared: int = 3, min_similarity: float = 0.3) -> list[dict]:
    """Headlines about the same event, from different sources: linked when their titles share at least
    `min_shared` meaningful words and enough of them (Jaccard). Only words that aren't too common count."""
    sets = [set(content_words(i.get("title") or "")) for i in items]
    df = Counter(w for s in sets for w in s)
    common = {w for w, n in df.items() if n > max(25, len(items) * 0.03)}       # "police", "government": too general
    sets = [s - common for s in sets]
    index: dict[str, list[int]] = defaultdict(list)
    for n, s in enumerate(sets):
        for w in s:
            index[w].append(n)
    parent = list(range(len(items)))

    def root(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for n, s in enumerate(sets):
        candidates = Counter(m for w in s for m in index[w] if m > n)
        for m, shared in candidates.items():
            if shared >= min_shared and _jaccard(s, sets[m]) >= min_similarity:
                parent[root(m)] = root(n)
    groups: dict[int, list[int]] = defaultdict(list)
    for n in range(len(items)):
        groups[root(n)].append(n)
    out = []
    for members in groups.values():
        sources = {items[n].get("source") for n in members}
        if len(sources) < min_sources:
            continue
        words = Counter(w for n in members for w in sets[n])
        key = [w for w, c in words.most_common(6) if c > 1]
        best = max(members, key=lambda n: (len(sets[n] & set(key)), -len(items[n].get("title") or "")))
        out.append({"headline": items[best].get("title"), "url": items[best].get("url"), "key_words": key,
                    "sources": sorted(s for s in sources if s), "groups": sorted({items[n].get("group_code") for n in members}),
                    "count": len(members),
                    "headlines": [{"title": items[n].get("title"), "source": items[n].get("source"), "url": items[n].get("url")}
                                  for n in members][:12]})
    out.sort(key=lambda s: (-len(s["sources"]), -len(s["groups"])))
    return out


def _tone(text: str, positive: re.Pattern | None, negative: re.Pattern | None) -> int:
    pos = len(positive.findall(text)) if positive else 0
    neg = len(negative.findall(text)) if negative else 0
    return (pos > neg) - (neg > pos)


def tone_summary(scores: list[int]) -> dict:
    n = len(scores)
    pos, neg = scores.count(1), scores.count(-1)
    return {"headlines": n, "positive": pos, "negative": neg, "neutral": n - pos - neg,
            "balance": round((pos - neg) / n * 100) if n else 0}


def rising(now_items: list[dict], before_items: list[dict], top: int = 20, min_count: int = 4) -> list[dict]:
    """Words in many more headlines now than in the period before (both counted per 100 headlines)."""
    if not before_items:
        return []
    now = Counter(w for i in now_items for w in set(content_words(i.get("title") or "")))
    before = Counter(w for i in before_items for w in set(content_words(i.get("title") or "")))
    scale = len(now_items) / len(before_items)
    out = []
    for w, n in now.items():
        expected = before.get(w, 0) * scale
        if n >= min_count and n >= 2 * (expected + 1):
            out.append({"word": w, "now": n, "before": before.get(w, 0), "score": round(n / (expected + 1), 1)})
    out.sort(key=lambda r: (-r["score"], -r["now"]))
    return out[:top]


def top_names(items: list[dict], top: int = 25) -> list[dict]:
    """Names counted once per headline. Title-case headlines aren't used to find names (all words have capitals),
    but they still count for names found elsewhere. The longest name wins where names overlap, so "Trump" counts
    only headlines that don't say "Donald Trump". Left out: ordinary words that happen to start a phrase
    ("Government", "State": the corpus mostly writes them in lower case), countries, and the sources' own names."""
    found: Counter = Counter()
    lower: Counter = Counter()
    for i in items:
        title = i.get("title") or ""
        text = (title if not _title_case(title) else "") + ". " + (i.get("summary") or "")
        found.update(set(names_in(text)))
        lower.update(w for w in re.findall(r"\b[a-z][a-z'’-]+\b", text_of(i)))
    squash = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())                      # noqa: E731
    sources = {squash(i.get("source") or "") for i in items}
    country_words = {w for aliases in COUNTRIES.values() for a in aliases for w in (a, *a.split())}   # also "Korea"
    names = [n for n, c in found.items() if c >= 2 and not any(squash(n) in src for src in sources if src)
             and n.lower() not in country_words
             and n.lower() not in TITLES
             and not (" " not in n and lower[n.lower()] >= c)]
    pattern = _pattern(names)
    counts: Counter = Counter()
    if pattern:
        lookup = {n.lower(): n for n in names}
        for i in items:
            counts.update({lookup[m.lower()] for m in pattern.findall(text_of(i)) if m.lower() in lookup})
    return [{"name": n, "headlines": c} for n, c in counts.most_common(top) if c >= 2]


def countries(items: list[dict]) -> list[dict]:
    """Headlines mentioning each country (name, people, capital), and how many of those came from other countries'
    sources. "US" is matched only in capitals, so the word "us" doesn't count."""
    patterns = {c: _pattern(aliases) for c, aliases in COUNTRIES.items()}
    us = re.compile(r"\bUS\b")
    out = []
    for country, pattern in patterns.items():
        hits = [i for i in items if pattern.search(text_of(i)) or (country == "United States" and us.search(text_of(i)))]
        if hits:
            from_abroad = sum(1 for i in hits if GROUP_NAMES.get(i.get("group_code")) != country)
            out.append({"country": country, "headlines": len(hits), "from_other_countries": from_abroad})
    out.sort(key=lambda r: -r["headlines"])
    return out


def analyze(items: list[dict], previous: list[dict], config: dict, group_names: dict | None = None) -> dict:
    """All the numbers for one period. `items` and `previous` are rows of news_items (dicts)."""
    group_names = group_names or {}
    topic_patterns = {t: _pattern(words) for t, words in (config.get("topics") or {}).items()}
    tone_cfg = config.get("tone") or {}
    positive, negative = _pattern(tone_cfg.get("positive", [])), _pattern(tone_cfg.get("negative", []))

    by_topic: Counter = Counter()
    topic_by_group: dict[str, Counter] = defaultdict(Counter)
    tone_by_group: dict[str, list[int]] = defaultdict(list)
    tone_by_topic: dict[str, list[int]] = defaultdict(list)
    examples: dict[str, list[dict]] = defaultdict(list)
    for i in items:
        text, group = text_of(i), i.get("group_code") or "OTHER"
        topics = [t for t, p in topic_patterns.items() if p and p.search(text)] or ["other"]
        tone = _tone(text, positive, negative)
        tone_by_group[group].append(tone)
        for t in topics:
            by_topic[t] += 1
            topic_by_group[group][t] += 1
            tone_by_topic[t].append(tone)
            if len(examples[t]) < 5:
                examples[t].append({"title": i.get("title"), "source": i.get("source"), "url": i.get("url")})

    lk = [i for i in items if i.get("group_code") == "LK" or re.search(r"sri lanka", text_of(i), re.I)]
    hours = Counter()
    for i in items:
        when = i.get("published_at") or i.get("first_seen_at")
        if isinstance(when, datetime):
            hours[when.astimezone().hour] += 1
    group_order = list(group_names) + sorted({i.get("group_code") or "OTHER" for i in items} - set(group_names))
    return {
        "headlines": len(items), "sources": len({i.get("source") for i in items}),
        "previous_headlines": len(previous),
        "stories": stories(items)[:25],
        "topics": [{"topic": t, "headlines": n, "share": round(n / len(items) * 100, 1),
                    "tone": tone_summary(tone_by_topic[t]), "examples": examples[t]}
                   for t, n in sorted(by_topic.items(), key=lambda x: (x[0] == "other", -x[1]))],
        "topics_by_group": [{"group": g, "name": group_names.get(g, g), "headlines": sum(1 for i in items if (i.get("group_code") or "OTHER") == g),
                             "top": [{"topic": t, "headlines": n} for t, n in topic_by_group[g].most_common(5) if t != "other"]}
                            for g in group_order if topic_by_group.get(g)],
        "people": top_names(items),
        "countries": countries(items)[:25],
        "rising": rising(items, previous),
        "tone": {"all": tone_summary([s for v in tone_by_group.values() for s in v]),
                 "by_group": [{"group": g, "name": group_names.get(g, g), **tone_summary(tone_by_group[g])}
                              for g in group_order if tone_by_group.get(g)]},
        "sri_lanka": {"headlines": len(lk), "from_lk_sources": sum(1 for i in lk if i.get("group_code") == "LK"),
                      "stories": stories(lk, min_sources=2)[:8], "people": top_names(lk, 12),
                      "words": [{"word": w, "headlines": n} for w, n in
                                Counter(w for i in lk for w in set(content_words(i.get("title") or ""))).most_common(20)
                                if w not in ("sri", "lanka", "lankan")][:15]},
        "activity": {"by_group": [{"group": g, "name": group_names.get(g, g), "headlines": n}
                                 for g, n in Counter(i.get("group_code") or "OTHER" for i in items).most_common()],
                    "by_source": [{"source": s, "headlines": n} for s, n in
                                  Counter(i.get("source") for i in items).most_common(15)],
                    "by_hour": [{"hour": h, "headlines": hours.get(h, 0)} for h in range(24)]},
    }


def balance_label(balance: int) -> str:
    """-100..100 to words. Rough: news is mostly negative, so a small minus is normal."""
    if balance <= -30:
        return "mostly negative"
    if balance <= -10:
        return "leaning negative"
    if balance < 10:
        return "mixed"
    return "leaning positive" if balance < 30 else "mostly positive"

