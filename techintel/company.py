"""Company facts from a company's own web pages: what the company itself publishes.

Pure functions (no network), used by the `company` stage:
  social_links    the company's accounts on LinkedIn, Facebook, X, GitHub, ... (links only)
  organization    schema.org Organization data in the page (JSON-LD): legal name, founders,
                  founding date, number of employees, address, contact, other profiles
  contacts        public email addresses and phone numbers (mailto:/tel: links and plain emails)
  people          names with a leadership role (CEO, founder, ...) as written on about/team pages
  employee_hint   "a team of 25", "50+ employees", ... in the page text

Social sites themselves (LinkedIn, Facebook, ...) are never fetched: their terms forbid automated
collection and most of it is behind a login. We keep the links, so the profile is one click away.
"""

import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .domains import root_domain

SOCIAL = {
    "linkedin.com": "linkedin", "facebook.com": "facebook", "fb.com": "facebook", "x.com": "x",
    "twitter.com": "x", "instagram.com": "instagram", "youtube.com": "youtube", "github.com": "github",
    "tiktok.com": "tiktok", "medium.com": "medium", "discord.gg": "discord", "discord.com": "discord",
    "t.me": "telegram", "threads.net": "threads", "crunchbase.com": "crunchbase", "wellfound.com": "wellfound",
    "producthunt.com": "producthunt", "pinterest.com": "pinterest", "whatsapp.com": "whatsapp", "wa.me": "whatsapp",
}
# Share buttons, login pages and the like: links that aren't the company's own account
NOT_AN_ACCOUNT = re.compile(r"/(sharer|share|sharearticle|intent|home\?|login|signup|hashtag|search|watch\?|"
                            r"embed|plugins|dialog|privacy|legal|about/|policies|help|tr\?)", re.I)
# LinkedIn: company pages first; /in/ is a person's page, kept separately (as the company linked it)
LINKEDIN_COMPANY = re.compile(r"linkedin\.com/(company|school|showcase)/", re.I)
LINKEDIN_PERSON = re.compile(r"linkedin\.com/in/[^/?#]+", re.I)

ORG_TYPES = ("organization", "corporation", "localbusiness", "ngo", "educationalorganization",
             "onlinebusiness", "onlinestore", "professionalservice", "softwarecompany")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Image names (logo@2x.png), error trackers, and the made-up addresses in form placeholders
NOT_EMAIL = re.compile(r"\.(png|jpe?g|gif|svg|webp|avif|css|js)$|@\d+x\.|sentry|wixpress|"
                       r"@(example|acme|domain|company|email|yourcompany|yourdomain|test)\.(com|org|net)$", re.I)
# The role in any case; names must start with capitals, so the patterns below are case-sensitive
ROLE = (r"(?i:co-?founder(?:\s*(?:&|and|,)\s*(?:ceo|cto|coo|chief\s+\w+\s+officer))?|founder(?:\s*(?:&|and|,)\s*ceo)?|"
        r"ceo|chief\s+executive\s+officer|cto|chief\s+technology\s+officer|coo|chief\s+operating\s+officer|cfo|"
        r"chief\s+financial\s+officer|managing\s+director|chairman|chairperson|president|general\s+manager|"
        r"director)")
ROLE_LINE = re.compile(rf"^\s*{ROLE}\s*(?:[,/|&-]\s*.*)?$")
NAME = re.compile(r"^(?:(?:Dr|Mr|Mrs|Ms|Prof)\.?\s+)?[A-Z][a-zA-Z'’-]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-zA-Z'’-]+){1,3}$")
NAME_THEN_ROLE = re.compile(rf"([A-Z][a-zA-Z'’-]+(?:\s+[A-Z][a-zA-Z'’-]+){{1,3}})\s*[,–—|-]\s*({ROLE})\b")
ROLE_THEN_NAME = re.compile(rf"\b({ROLE})\s*[:,–—-]\s*([A-Z][a-zA-Z'’-]+(?:\s+[A-Z][a-zA-Z'’-]+){{1,3}})")
NOT_A_NAME = {"our", "the", "meet", "about", "team", "contact", "with", "and", "for", "board", "message", "from",
              "view", "read", "more", "join", "us", "company", "leadership", "management", "founder", "director"}
# "people" or "engineers" alone is too often about customers ("trusted by 10,000 people"), so only after "team of"
EMPLOYEES = re.compile(r"\bteam\s+of\s+(?:over\s+|more\s+than\s+|about\s+)?(\d[\d,]{0,6})\+?|"
                       r"\b(?:over\s+|more\s+than\s+|about\s+)?(\d[\d,]{0,6})\+?\s*(?:employees|staff|team\s+members)\b",
                       re.I)


def social_links(links: list[dict]) -> tuple[dict[str, str], list[str]]:
    """The company's own account per platform, and personal LinkedIn pages it links to (team pages)."""
    accounts: dict[str, str] = {}
    people: list[str] = []
    for link in links:
        href = (link.get("href") or "").strip()
        root = root_domain(href) if href.startswith("http") else ""
        platform = SOCIAL.get(root)
        path = urlsplit(href).path.strip("/")
        if not platform or not path or NOT_AN_ACCOUNT.search(href):
            continue
        url = href.split("?")[0].split("#")[0]
        person = LINKEDIN_PERSON.search(href) if platform == "linkedin" else None
        if person:
            if "https://www." + person.group(0) not in people:
                people.append("https://www." + person.group(0))
        elif platform == "linkedin" and LINKEDIN_COMPANY.search(url) \
                and not LINKEDIN_COMPANY.search(accounts.get("linkedin", "")):
            accounts["linkedin"] = url               # a company page beats any other LinkedIn link
        else:
            accounts.setdefault(platform, url)
    return accounts, people


def _items(data) -> list[dict]:
    """Every object in a JSON-LD block (top level, lists and @graph)."""
    if isinstance(data, list):
        return [x for d in data for x in _items(d)]
    if isinstance(data, dict):
        return [data] + _items(data.get("@graph", []))
    return []


def _types(item: dict) -> list[str]:
    t = item.get("@type") or []
    return [x.lower() for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)]


def _names(value) -> list[str]:
    values = value if isinstance(value, list) else [value]
    out = []
    for v in values:
        name = v.get("name") if isinstance(v, dict) else v
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
    return out


def _employees(value) -> str | None:
    """numberOfEmployees is a number, a text, or a QuantitativeValue (value or minValue-maxValue)."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        if value.get("value") is not None:
            return str(value["value"])
        lo, hi = value.get("minValue"), value.get("maxValue")
        return f"{lo}-{hi}" if lo is not None and hi is not None else (str(lo or hi) if (lo or hi) else None)
    return str(value) if value not in (None, "") else None


def _address(value) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        country = value.get("addressCountry")
        country = country.get("name") if isinstance(country, dict) else country
        parts = [value.get(k) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode")] + [country]
        return ", ".join(str(p).strip() for p in parts if p and str(p).strip()) or None
    return value.strip() if isinstance(value, str) and value.strip() else None


def organization(html: str) -> dict:
    """The page's schema.org Organization (JSON-LD), if it has one. Empty fields are left out."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _items(data):
            if not any(t.endswith(ORG_TYPES) for t in _types(item)):
                continue
            contact = item.get("contactPoint")
            contact = contact[0] if isinstance(contact, list) and contact else contact
            contact = contact if isinstance(contact, dict) else {}
            same_as = item.get("sameAs") or []
            facts = {
                "name": _names(item.get("name"))[:1],
                "legal_name": item.get("legalName"),
                "founders": _names(item.get("founder") or item.get("founders")),
                "founding_date": item.get("foundingDate"),
                "employees": _employees(item.get("numberOfEmployees")),
                "address": _address(item.get("address")),
                "email": item.get("email") or contact.get("email"),
                "telephone": item.get("telephone") or contact.get("telephone"),
                "same_as": same_as if isinstance(same_as, list) else [same_as],
            }
            facts["name"] = facts["name"][0] if facts["name"] else None
            return {k: v for k, v in facts.items() if v}
    return {}


def contacts(html: str, text: str = "") -> dict[str, list[str]]:
    """Public emails and phone numbers. Phones only from tel: links (numbers in text are too often other things)."""
    soup = BeautifulSoup(html or "", "html.parser")
    emails: list[str] = []
    phones: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            emails.append(href[7:].split("?")[0])
        elif href.lower().startswith("tel:"):
            phone = re.sub(r"[^\d+]", "", href[4:])
            if len(phone.lstrip("+")) >= 7:
                phones.append(phone)
    emails += EMAIL.findall(text or "")
    clean = []
    for e in emails:
        e = e.strip().strip(".").lower()
        if EMAIL.fullmatch(e) and not NOT_EMAIL.search(e) and e not in clean:
            clean.append(e)
    return {"emails": clean[:10], "phones": list(dict.fromkeys(phones))[:5]}


def _lines(html: str) -> list[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template", "nav", "footer"]):
        tag.decompose()
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in soup.get_text("\n").splitlines()]
    return [ln for ln in lines if ln]


def _is_name(text: str) -> bool:
    words = text.split()
    return bool(NAME.match(text)) and not any(w.lower().strip(".") in NOT_A_NAME for w in words)


def people(html: str) -> list[dict]:
    """Leadership as the company's own page names it: "Jane Perera" next to "Co-founder & CEO",
    or "Jane Perera, CEO" / "CEO: Jane Perera" on one line."""
    found: list[dict] = []

    def add(name: str, role: str) -> None:
        name, role = name.strip(), re.sub(r"\s+", " ", role).strip(" ,|-")
        if _is_name(name) and not any(p["name"] == name for p in found):
            found.append({"name": name, "role": role[:60]})

    lines = _lines(html)
    for i, line in enumerate(lines):
        if len(line) <= 60 and ROLE_LINE.match(line):
            for j in (i - 1, i + 1):                 # the name is usually just above the role, else below
                if 0 <= j < len(lines) and len(lines[j]) <= 40 and _is_name(lines[j]):
                    add(lines[j], line)
                    break
        elif len(line) <= 200:
            for m in NAME_THEN_ROLE.finditer(line):
                add(m.group(1), m.group(2))
            for m in ROLE_THEN_NAME.finditer(line):
                add(m.group(2), m.group(1))
    return found[:15]


def employee_hint(text: str) -> str | None:
    """The first "team of 25" / "50+ employees" phrase in the text, as written (not a verified count)."""
    for m in EMPLOYEES.finditer(text or ""):
        number = m.group(1) or m.group(2)
        if 2 <= int(number.replace(",", "")) <= 500_000:
            return re.sub(r"\s+", " ", m.group(0)).strip()
    return None
