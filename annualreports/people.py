"""Directors and other named people, from the pages about the board and management only.

Two ways a name is recognised, both strict, so a sentence is never taken for a person:
- `title`: a line that is a title and a name and nothing else ("Mr. S.D.R. Arudpragasam", "Dr. Harsha Cabral, PC - Chairman").
- `heading`: on those pages, a short heading of 2-5 capitalised words with a role in the next lines ("Sir Mark E Tucker" / "Group Chairman").
The role is the text on the same line after the name, or the next line if it reads like a role. Names elsewhere in the report (the
chairman's letter, notes) aren't collected: they're in the page text."""

import re

BOARD_PAGE = re.compile(r"board of directors|directors['’]? profiles?|profiles? of (?:the )?(?:board|directors)|our board|the board|"
                        r"corporate information|senior management|management team|leadership team|key management|"
                        r"executive committee|group executive|corporate management|management committee|board committees?", re.I)
HON = (r"(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof|Professor|Sir|Dame|Lord|Lady|Hon|Rev|Ven|Deshamanya|Deshabandu|Kala Keerthi|Eng|Capt|Captain|"
       r"Commodore|Rear Admiral|Vice Admiral|Admiral|Gen|Maj|Brig|Col|Air Vice Marshal|Justice|Chev)\.?")
TOKEN = r"(?:[A-Z][A-Za-z'’\-]*\.?|[A-Z]\.(?:[A-Z]\.)*)"
TITLE_LINE = re.compile(rf"^(?P<hon>{HON}(?:\s+{HON})*)\s+(?P<name>{TOKEN}(?:\s*{TOKEN}){{0,6}})\s*(?:[,\-–—|(:]\s*(?P<role>.{{2,150}}))?$")
HEADING_NAME = re.compile(rf"^(?:{HON}\s+)?{TOKEN}(?:\s+{TOKEN}){{1,4}}$")
ROLE = re.compile(r"\b(?:chairman|chairperson|chair|director|chief|ceo|cfo|coo|cto|president|secretary|managing|executive|independent|"
                  r"non[- ]executive|deputy|vice|officer|head of|general manager|manager|founder|treasurer|board member|senior|group)\b", re.I)
NOT_NAME = re.compile(r"\b(?:annual|report|board|company|plc|limited|ltd|committee|statement|directors?|management|group|bank|holdings|"
                      r"policy|review|profile|governance|audit|risk|information|chairman|chairperson|chairwoman|message|the|of|and|for|our|"
                      r"responsibilit\w*|duties|attendance|meetings?|members?|composition|remuneration|nomination|sustainability|"
                      r"highlights|year|statement|corporate|financial|terms|reference|charter)\b", re.I)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def name_key(name: str) -> str:
    return re.sub(r"[^A-Z]", "", name.upper())


ROLE_WORD = re.compile(r"(?i)^(?:non-?|non-executive|non-independent|independent|executive|director|chairman|chairperson|chair|ceo|cfo|coo|"
                       r"managing|deputy|senior|secretary|president|alternate|vice|group|chief|company|lead)\b")
LOWER_OK = {"of", "and", "the", "to", "on", "w.e.f", "wef", "from", "with", "effect", "in", "for", "as", "at"}


def _clean_role(text: str | None) -> str | None:
    """A role as printed, without what a table puts after it (attendance '6', '3/4', dates' leftovers). A line with many ordinary
    words is a sentence, not a role."""
    text = norm(re.sub(r"(?:\s+(?:\d{1,2}(?:/\d{1,2})?|-))+\s*$", "", norm(text)))
    if not text or not ROLE.search(text):
        return None
    lower = [w for w in text.split() if w[:1].islower() and w.strip("(),.").lower() not in LOWER_OK]
    if len(lower) >= 3 or len(text.split()) > 16:
        return None
    return text


def _split_name(name: str) -> tuple[str, str]:
    """'Suresh Dilhan Perera Non-Executive' -> ('Suresh Dilhan Perera', 'Non-Executive'): a role word ends the name."""
    words = name.split()
    for i, w in enumerate(words):
        if i >= 1 and ROLE_WORD.match(w):
            return " ".join(words[:i]), " ".join(words[i:])
    return name, ""


def _role_after(lines: list[str], i: int) -> tuple[str, str | None]:
    """What's printed under a name: the rest of a name that ran onto the next line ("Mr. Sumal Joseph" / "Sanjiva Perera Executive"),
    and the role, joined when it runs over two lines ("Senior Independent" / "Non-Executive Director"). Returns (more name, role)."""
    more, parts = "", []
    for k, nxt in enumerate(lines[i + 1:i + 3]):
        nxt = norm(nxt)
        if not nxt or len(nxt) > 120 or TITLE_LINE.match(nxt) or nxt.endswith("."):
            break
        if k == 0 and not ROLE_WORD.match(nxt):
            head, rest = _split_name(nxt)
            if rest and re.fullmatch(rf"{TOKEN}(?:\s+{TOKEN}){{0,2}}", head) and not NOT_NAME.search(head):
                more, nxt = head, rest
        if not ROLE.search(nxt):
            break
        parts.append(nxt)
    return more, _clean_role(" ".join(parts))


def _role_on_line(text: str | None) -> str | None:
    """Text after the name on its line, if it's a role. 'Mrs. S T Ratwatte and ...' is a list of names, not a role."""
    text = norm(text)
    if not text or re.search(HON + r"(?:\s|(?=[A-Z]))", text):
        return None
    return _clean_role(text)


def is_board_page(page) -> bool:
    top = "\n".join(norm(ln) for ln in page.text.splitlines()[:12])
    return any(BOARD_PAGE.search(h["text"]) for h in page.headings) or bool(BOARD_PAGE.search(top))


def people_on_page(page) -> list[dict]:
    lines = [norm(ln) for ln in page.full_text.splitlines() if norm(ln)]
    found = []
    for i, line in enumerate(lines):
        m = TITLE_LINE.match(line)
        if not m:
            continue
        name, role_part = _split_name(norm(m.group("name")))
        if NOT_NAME.search(name) or len(name) > 60:
            continue
        role = _role_on_line(" ".join(x for x in (role_part, m.group("role") or "") if x))
        more, below = _role_after(lines, i)
        if role and role_part and below and not m.group("role"):
            role = _clean_role(role + " " + below)              # "... Non-Independent Non" / "Executive Director 6"
        found.append({"honorific": norm(m.group("hon")), "name": norm(f"{name} {more}") if more and not role_part else name,
                      "role": role or below, "found_by": "title"})
    heading_texts = {norm(h["text"]) for h in page.headings}
    for i, line in enumerate(lines):
        if line in heading_texts and HEADING_NAME.match(line) and not NOT_NAME.search(line) and not TITLE_LINE.match(line):
            _, role = _role_after(lines, i)
            if role:
                found.append({"honorific": None, "name": line, "role": role, "found_by": "heading"})
    for p in found:
        p["page_no"] = page.page_no
    return found


def collect(pages) -> list[dict]:
    """One row per person in the document, with every role and page they were found with."""
    people: dict[str, dict] = {}
    for page in pages:
        if not is_board_page(page):
            continue
        for p in people_on_page(page):
            key = name_key(p["name"])
            if len(key) < 4:
                continue
            have = people.setdefault(key, {"name_key": key, "name": p["name"], "honorific": p["honorific"], "roles": [], "pages": [],
                                           "found_by": p["found_by"]})
            have["honorific"] = have["honorific"] or p["honorific"]
            if p["role"] and p["role"] not in have["roles"]:
                have["roles"].append(p["role"])
            if p["page_no"] not in have["pages"]:
                have["pages"].append(p["page_no"])
    return list(people.values())
