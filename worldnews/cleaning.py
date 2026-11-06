"""Tidying what news sites give us, before it's saved. A homepage's link text can hold more than the headline, and feeds and page descriptions carry the site's own
slogans. Left in, these show up in reports and PDFs, and spoil the analysis (a slogan's words count as if every story were about them).

Each pattern is written for the exact wording seen, so an ordinary headline is never touched. `python -m worldnews clean` applies the same rules to what's already saved."""

import re
from datetime import datetime, timedelta, timezone

from .extract import iso

# "05-11-2026 | 12:37 PM" on a homepage's card. Only Newsfirst, a Sri Lankan site, writes dates this way, so it's Sri Lankan time. Its pages give no other date.
CARD_DATE = re.compile(r"\s*\b(\d{2}-\d{2}-\d{4}) \| (\d{1,2}:\d{2} [AP]M)\b\s*")
CARD_TIME_ZONE = timezone(timedelta(hours=5, minutes=30))
READ_MORE = re.compile(r"\s+Read more\s*$", re.I)
NEWS_1ST_TEASER = re.compile(r"\s+[A-Z]{3,}\s*[-–]?\s*\(News\s*1st\).*$", re.S)         # the article's first words, after the headline
NEWS_1ST_DATELINE = re.compile(r"^[A-Z]{3,}\s*[-–]?\s*\(News\s*1st\)\s*[:;,.–-]*\s*")
NEWS_1ST_SLOGAN = re.compile(r"\s+-\s+Get the latest breaking news and top stories from Sri Lanka.*$", re.S)
SITE_TRAILERS = (
    re.compile(r"\s*Read the full story on \S+.*$", re.S),                 # CryptoSlate
    re.compile(r"\s*\bThe post .{0,300}? first appeared on .*$", re.S),    # WordPress's own line, at the end of many feeds
)
EMPTY_SUMMARIES = {"comments"}                                             # Hacker News's feed has only a link to the comments
MIN_TITLE_WORDS = 2


def card_date(title: str) -> str | None:
    found = CARD_DATE.search(title or "")
    if not found:
        return None
    try:
        moment = datetime.strptime(f"{found[1]} {found[2]}", "%d-%m-%Y %I:%M %p")
    except ValueError:
        return None
    return iso(moment.replace(tzinfo=CARD_TIME_ZONE))


def clean_title(title: str) -> str:
    original = title or ""
    text = original
    if CARD_DATE.search(text):       # only on a card: a headline can end in "read more" by itself
        text = READ_MORE.sub("", CARD_DATE.sub(" ", text))
    text = " ".join(NEWS_1ST_TEASER.sub("", text).split())
    return text if len(text.split()) >= MIN_TITLE_WORDS else " ".join(original.split())


def _whole_words(text: str) -> str:
    """Newsfirst's description stops mid-word at a fixed length, before its slogan."""
    if text.endswith((".", "!", "?", "…", '"', "”")):
        return text
    head = text.rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{head}…" if head else text


def clean_summary(summary: str) -> str:
    text = (summary or "").strip()
    if text.lower() in EMPTY_SUMMARIES:
        return ""
    without = NEWS_1ST_SLOGAN.sub("", text)
    if without != text:
        without = re.sub(r"(?<=[a-z])\.(?=[A-Z])", ". ", _whole_words(without))    # the same site leaves out the space after a full stop
    text = NEWS_1ST_DATELINE.sub("", without)
    for trailer in SITE_TRAILERS:
        text = trailer.sub("", text)
    return text.strip()


def clean_item(item: dict) -> dict:
    """In place, and safe to repeat. A date only fills a gap: one the site states for itself stays."""
    title = item.get("title") or ""
    item["published_at"] = item.get("published_at") or card_date(title)
    item["title"] = clean_title(title)
    item["summary"] = clean_summary(item.get("summary") or "")
    return item
