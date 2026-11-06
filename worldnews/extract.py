"""Turning feeds and web pages into headlines.

  parse_feed(xml, base_url)          RSS/Atom -> headlines with date and summary
  discover_feeds(html, base_url)     feed URLs advertised in a homepage's <head>
  homepage_headlines(html, ...)      article links picked out of a homepage
  selector_headlines(html, ...)      article links using site-specific CSS selectors
  article_meta(html)                 published date, summary and image from an article page
  normalize_url(url)                 strip tracking parameters and #fragments, for de-duplication
"""

import calendar
import html as html_lib
import json
import re
import warnings
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import feedparser
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|ocid|cmpid|ref$|ref_|src$|taid|ito|cid$|mc_)", re.I)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def parse_date(value, naive_zone=timezone.utc) -> str | None:
    """Many date formats -> ISO UTC string ('2026-11-02T08:15:00Z'), or None. naive_zone: what a date with no time zone means (see collect.source_zone)."""
    if not value:
        return None
    if hasattr(value, "tm_year"):  # time.struct_time from feedparser (UTC)
        return iso(datetime.fromtimestamp(calendar.timegm(value), timezone.utc))
    text = str(value).strip()
    for parser in (lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")), parsedate_to_datetime):
        try:
            dt = parser(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=naive_zone)
            return iso(dt)
        except (ValueError, TypeError, IndexError):
            continue
    return None


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", query, ""))


def clean_text(text: str | None, limit: int = 400) -> str:
    if not text:
        return ""
    text = BeautifulSoup(html_lib.unescape(text), "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


# ── feeds ─────────────────────────────────────────────────────────────────
def parse_feed(xml: str, base_url: str = "") -> list[dict]:
    parsed = feedparser.parse(xml)
    items = []
    for entry in parsed.entries:
        link = entry.get("link") or ""
        title = clean_text(entry.get("title"), 300)
        if not link or not title:
            continue
        image = None
        for media in (entry.get("media_content") or []) + (entry.get("media_thumbnail") or []):
            if media.get("url"):
                image = media["url"]
                break
        items.append({
            "title": title,
            "url": urljoin(base_url, link),
            "summary": clean_text(entry.get("summary") or entry.get("description")),
            "published_at": parse_date(entry.get("published_parsed") or entry.get("updated_parsed")
                                       or entry.get("published") or entry.get("updated")),
            "image": image,
        })
    return items


def looks_like_feed(text: str) -> bool:
    head = text[:1000].lower()
    return "<rss" in head or "<feed" in head or "<rdf:rdf" in head


def discover_feeds(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel") or []).lower()
        kind = (link.get("type") or "").lower()
        if "alternate" in rel and ("rss" in kind or "atom" in kind):
            url = urljoin(base_url, link["href"])
            if url not in found:
                found.append(url)
    return found


# ── homepages ─────────────────────────────────────────────────────────────
DATE_IN_PATH = re.compile(r"/20\d{2}/[01]?\d(/[0-3]?\d)?/|/20\d{2}-[01]\d-[0-3]\d|20\d{6}")
LONG_ID = re.compile(r"\d{5,}")


def _same_site(host: str, home_host: str) -> bool:
    strip = lambda h: h.lower().removeprefix("www.")  # noqa: E731
    host, home_host = strip(host), strip(home_host)
    base = ".".join(home_host.split(".")[-2:]) if not re.search(r"\.(co|com|org|net|gov|ac)\.\w\w$", home_host) \
        else ".".join(home_host.split(".")[-3:])
    return host == home_host or host.endswith("." + base) or host == base


def article_score(url: str, home_url: str, skip_words: set[str]) -> int:
    """How much a URL looks like an article (0 = not an article)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not _same_site(parts.netloc, urlsplit(home_url).netloc):
        return 0
    path = parts.path.lower()
    segments = [s for s in path.split("/") if s]
    if not segments or path.endswith((".pdf", ".jpg", ".png", ".mp3", ".mp4", ".xml")):
        return 0
    if any(s in skip_words for s in segments[:2]):
        return 0
    score = 0
    if DATE_IN_PATH.search(path):
        score += 3
    if LONG_ID.search(path):
        score += 2
    slug = max(segments, key=len)
    if slug.count("-") >= 3:
        score += 2
    if any(s in ("article", "articles", "story", "stories", "news", "a") for s in segments):
        score += 1
    if len(segments) >= 2:
        score += 1
    return score


def _link_text(a) -> str:
    text = a.get_text(" ", strip=True) or a.get("aria-label") or a.get("title") or ""
    return re.sub(r"\s+", " ", text).strip()


def homepage_headlines(html: str, home_url: str, config: dict) -> list[dict]:
    """Article links in page order (the homepage's own order of prominence)."""
    soup = BeautifulSoup(html, "html.parser")
    skip = set(config.get("skip_url_words", []))
    min_words, min_chars = config.get("min_title_words", 4), config.get("min_title_chars", 25)
    max_chars = config.get("max_title_chars", 220)
    best: dict[str, dict] = {}
    order: list[str] = []
    for a in soup.find_all("a", href=True):
        url = normalize_url(urljoin(home_url, a["href"]))
        if article_score(url, home_url, skip) < 2:
            continue
        title = _link_text(a)
        if not (min_chars <= len(title) <= max_chars and len(title.split()) >= min_words):
            continue
        if url not in best:
            order.append(url)
            best[url] = {"title": title, "url": url}
        elif len(title) > len(best[url]["title"]):   # same link twice (image + text): keep the fuller text
            best[url]["title"] = title
    return [best[u] for u in order]


def selector_headlines(html: str, home_url: str, selectors: dict) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for block in soup.select(selectors["base"]):
        title_el = block.select_one(selectors.get("title", "a"))
        link_el = block.select_one(selectors.get("link", "a"))
        if not (title_el and link_el and link_el.get("href")):
            continue
        url = normalize_url(urljoin(home_url, link_el["href"]))
        title = re.sub(r"\s+", " ", title_el.get_text(" ", strip=True))
        if title and url not in seen:
            seen.add(url)
            out.append({"title": title, "url": url})
    return out


# ── article pages ─────────────────────────────────────────────────────────
def article_meta(html: str, naive_zone=timezone.utc) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    def meta(*names):
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name}) \
                or soup.find("meta", attrs={"itemprop": name})
            if tag and tag.get("content"):
                return tag["content"]
        return None

    published = meta("article:published_time", "og:article:published_time", "datePublished", "pubdate",
                     "publish-date", "date", "dc.date", "sailthru.date", "parsely-pub-date")
    if not published:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (ValueError, TypeError):
                continue
            for obj in (data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []):
                if isinstance(obj, dict) and obj.get("datePublished"):
                    published = obj["datePublished"]
                    break
            if published:
                break
    if not published:
        time_tag = soup.find("time", datetime=True)
        published = time_tag["datetime"] if time_tag else None
    return {
        "published_at": parse_date(published, naive_zone),
        "summary": clean_text(meta("og:description", "description", "twitter:description")),
        "image": meta("og:image", "twitter:image"),
    }
