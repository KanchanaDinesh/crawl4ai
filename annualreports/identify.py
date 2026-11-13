"""Whose report it is and for which year, from what the PDF prints on (nearly) every page: "BPPL HOLDINGS PLC | ANNUAL REPORT 2024/25",
"HSBC Holdings plc Annual Report and Accounts 2024". Settings can say it by hand for a file that doesn't (settings.json "documents")."""

import re
from collections import Counter

YEAR = r"(?P<yr>(?:19|20)\d{2}(?:\s*/\s*(?:19|20)?\d{2})?)"
SEP = r"\s*[|\-–—:•]\s*"
HEADER_PATTERNS = [
    re.compile(rf"^(?P<co>.{{3,80}}?){SEP}(?:integrated\s+)?annual\s+report(?:\s+and\s+accounts)?{SEP}?{YEAR}\b", re.I),
    re.compile(rf"^(?:integrated\s+)?annual\s+report(?:\s+and\s+accounts)?\s+{YEAR}{SEP}(?P<co>.{{3,80}})$", re.I),
    re.compile(rf"^(?P<co>.{{3,80}}?)\s+(?:integrated\s+)?annual\s+report(?:\s+and\s+accounts)?\s+{YEAR}\b", re.I),
    re.compile(r"^(?P<co>.{3,80}?)\s+annual\s+report\s+on\s+form\s+20-f\b", re.I),
    re.compile(rf"^(?:\d{{1,4}}\s+)?(?:integrated\s+)?annual\s+report\s+{YEAR}(?:\s+\d{{1,4}})?$", re.I),     # the year alone
]
COMPANY_LINE = re.compile(r"^(?:the\s+)?[A-Z][\w&.,'’()\- ]{2,70}\b(?:PLC|plc|Limited|LIMITED|Ltd\.?|LTD\.?|Inc\.?|Corporation|Holdings)\s*$")
PERIOD = re.compile(r"(?:year|period)\s+ended\s+(?:on\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|"
                    r"october|november|december),?\s+((?:19|20)\d{2})|as\s+at\s+(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|"
                    r"july|august|september|october|november|december),?\s+((?:19|20)\d{2})", re.I)
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                                      "november", "december"], 1)}
SUFFIX = re.compile(r"\b(?:PLC|plc|Limited|LIMITED|Ltd\.?|LTD\.?|Inc\.?|Corporation|Holdings|HOLDINGS|Bank|BANK)\s*$")
STOP_START = {"of", "in", "our", "this", "see", "copies", "refer", "and", "for", "to", "from", "with", "by", "on", "as", "at", "these", "its",
              "a", "an", "all", "any", "each", "please", "further", "read", "both", "throughout", "including"}
NOT_A_COMPANY = re.compile(r"\b(report|page|contents|statement|notes?|section|chapter|board|directors?|glossary|introduction|overview)\b|^\d+$", re.I)


def norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def fiscal_year(label: str) -> tuple[str, int]:
    """'2024/2025' -> ('2024/25', 2025); '2024' -> ('2024', 2024). The year is the one the report's period ends in."""
    label = re.sub(r"\s+", "", label)
    if "/" in label:
        start, end = label.split("/", 1)
        end_full = int(end) if len(end) == 4 else int(start[:2] + end[-2:])
        return f"{start}/{str(end_full)[-2:]}", end_full
    return label, int(label)


def _clean_company(co: str) -> str | None:
    co = norm_space(re.sub(r"^\d{1,4}\s+|\s+\d{1,4}$", "", co.strip(" |-–—:•")))
    co = re.sub(r"^(?:[IVXLC]{1,6}|\d{1,4})\s+(?=[A-Z])", "", co)                # a page number before the name ("II ACL CABLES PLC")
    if len(co) < 3 or len(co.split()) > 9 or NOT_A_COMPANY.search(co) or sum(c.isalpha() for c in co) < 3:
        return None
    if not co[0].isupper() or co.split()[0].lower().strip(",.") in STOP_START:      # "of the Annual Report 2024": a sentence
        return None
    return co


def best_company(companies: Counter) -> str | None:
    """A name ending in PLC / Limited / Holdings on 2 pages or more; else the one on the cover (the first 2 pages); else any name with
    such an ending; else the most repeated. One line naming another company ("... Tea Estates PLC Annual Report 2023/24" in a
    chamber of commerce's report) doesn't make it the owner."""
    if not companies:
        return None
    first = getattr(companies, "first", {})
    suffixed = [(n, c) for c, n in companies.items() if SUFFIX.search(c)]
    if any(n >= 2 for n, _ in suffixed):
        return max(suffixed)[1]
    cover = sorted((p, c) for c, p in first.items() if p <= 2)
    if cover:
        return cover[0][1]
    if suffixed:
        return max(suffixed)[1]
    return companies.most_common(1)[0][0]


def header_candidates(page_texts: list[str]) -> tuple[Counter, Counter]:
    """Counts of (company) and (year label) in lines that look like a running header. `companies.first` has the first page each
    company name was on."""
    companies, years = Counter(), Counter()
    companies.first = {}
    for page_no, text in enumerate(page_texts, 1):
        seen = set()
        for line in text.splitlines():
            line = norm_space(line)
            if not 8 <= len(line) <= 140:
                continue
            for rx in HEADER_PATTERNS:
                m = rx.search(line)
                if not m:
                    continue
                co = _clean_company(m.group("co")) if m.groupdict().get("co") else None
                yr = m.groupdict().get("yr")
                if co and ("co", co) not in seen:
                    companies[co] += 1
                    companies.first.setdefault(co, page_no)
                    seen.add(("co", co))
                if yr and ("yr", yr) not in seen:
                    years[fiscal_year(yr)[0]] += 1
                    seen.add(("yr", yr))
                break
    return companies, years


def running_headers(page_texts: list[str], min_share: float = 0.25) -> set[str]:
    """Lines repeated at the top or bottom of many pages (the company and report name, "Annual Report 2024/25"), compared without
    their digits so the page number doesn't matter. Not taken as headings or table titles."""
    counts = Counter()
    for text in page_texts:
        lines = [norm_space(ln) for ln in text.splitlines() if norm_space(ln)]
        for ln in set(lines[:3] + lines[-3:]):
            counts[re.sub(r"\d+", "#", ln)] += 1
    need = max(3, min_share * len(page_texts))
    return {k for k, n in counts.items() if n >= need}


def is_running_header(text: str, headers: set[str]) -> bool:
    return re.sub(r"\d+", "#", norm_space(text)) in headers


def period_end(page_texts: list[str]) -> str | None:
    """The most common 'year ended 31 March 2025' / 'as at 31 December 2024' date, as YYYY-MM-DD."""
    found = Counter()
    for text in page_texts:
        for m in PERIOD.finditer(text):
            d, mon, y = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(4), m.group(5), m.group(6))
            found[f"{int(y):04d}-{MONTHS[mon.lower()]:02d}-{int(d):02d}"] += 1
    if not found:
        return None
    best = max(found.values())
    return max(k for k, n in found.items() if n == best)          # a tie: the later date is this year's, the other the comparison


def language(page_texts: list[str]) -> str:
    text = "".join(page_texts)
    letters = sum(c.isalpha() for c in text) or 1
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    return "zh" if cjk / letters > 0.3 else "en"


def identify(file_name: str, page_texts: list[str], meta: dict, by_hand: dict | None = None) -> dict:
    """Company, fiscal year, period end, title and language, and where each came from."""
    by_hand = by_hand or {}
    companies, years = header_candidates(page_texts)
    out = {"company": None, "company_from": None, "fiscal_year": None, "year": None, "period_end": period_end(page_texts),
           "language": language(page_texts), "title": None}
    lines = Counter(norm_space(ln) for t in page_texts for ln in t.splitlines() if COMPANY_LINE.match(norm_space(ln)))
    best = best_company(companies)
    if best and (SUFFIX.search(best) or not lines):
        out["company"], out["company_from"] = best, "running header"
    else:
        if lines:
            out["company"], out["company_from"] = lines.most_common(1)[0][0], "most repeated company name"
    if years:
        out["fiscal_year"], out["year"] = fiscal_year(years.most_common(1)[0][0])
    else:
        m = re.search(YEAR, " ".join([meta.get("Title", ""), file_name]))
        if m:
            out["fiscal_year"], out["year"] = fiscal_year(m.group("yr"))
        elif out["period_end"]:
            out["fiscal_year"], out["year"] = out["period_end"][:4], int(out["period_end"][:4])
    title = norm_space(meta.get("Title", ""))
    if title and not re.search(r"^untitled|\.(indd|pdf|docx?)$|^microsoft word", title, re.I):
        out["title"] = title
    elif out["company"]:
        out["title"] = f"{out['company']} Annual Report {out['fiscal_year'] or ''}".strip()
    for key in ("company", "fiscal_year", "title", "period_end", "language"):
        if by_hand.get(key):
            out[key] = by_hand[key]
            if key == "company":
                out["company_from"] = "settings"
            if key == "fiscal_year":
                out["fiscal_year"], out["year"] = fiscal_year(by_hand[key])
    return out


def name_key(name: str | None) -> str:
    """'B P P L Holdings PLC' and 'BPPL HOLDINGS PLC' are the same name: capitals and letters only, & as AND, no leading THE."""
    s = (name or "").upper().replace("&", " AND ")
    s = re.sub(r"^THE\s+", "", s.strip())
    return re.sub(r"[^A-Z0-9]", "", s)
