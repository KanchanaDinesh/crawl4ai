"""A page's text, made readable: the PDF breaks every line where it was set, so this joins lines back into paragraphs, finds headings,
subheadings and bullet points, mends words split by a hyphen at a line end, and leaves out the running header and the page number.

Only for showing. What's saved (pages.text) stays exactly as the PDF has it, and the report page offers that too ("as printed").
Rules, all from what the reports do:
- a line ending a sentence ('.', '!', '?', ':') that is clearly shorter than the lines around it ends its paragraph; so does any
  sentence end once a paragraph is long (the text keeps no gaps between paragraphs, and narrow columns are ragged);
- a short line in capitals, or one of the page's headings (bigger or bold font), is a heading; several in a row are one heading,
  or a pull quote when it's long;
- a short Title-like line with no full stop, after a finished sentence and before more text, is a subheading ("Taste", "Our Vision");
- a paragraph starting with a short label and a colon ("Education: ...") keeps the label apart, to show it in bold;
- a line starting with a bullet ('•', '–', '-', '●', '▪', '(a)', '1.') starts a list item; the lines after it continue it;
- lines of one letter (a word printed downwards, letter by letter) are left out.
"""

import re
import statistics

from .identify import is_running_header

BULLET = re.compile(r"^(?:[•●▪◦·*–—-]\s+|\(?[a-z]\)\s+|\(?[ivx]{1,4}\)\s+|\d{1,2}[.)]\s+)", re.I)
LONE_BULLET = re.compile(r"^[•●▪◦·*–—-]$")
SENTENCE_END = re.compile(r"[.!?:;][\"'’”)]*$")
PAGE_NUMBER = re.compile(r"^(?:page\s+)?\d{1,4}$|^[ivxlc]{1,6}$", re.I)
LEAD = re.compile(r"^([A-Z][\w’'&/()\- ]{1,38}?):\s+(?=\S)")      # "Areas of expertise: ..." (not "Note 5: ..." mid-sentence)
LEAD_LINE = re.compile(r"^[A-Z][\w’'&/()\- ]{1,38}:$")
LONG_PARAGRAPH = 700                         # characters: past this, any sentence that ends a line ends the paragraph
SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with", "&", "-", "–"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _is_caps(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 3 and sum(c.isupper() for c in letters) / len(letters) > 0.85 and len(line.split()) <= 12


def _is_title(line: str) -> bool:
    """Short and in Title Case, with no sentence or clause punctuation: "Business Values", "Areas of Expertise", "Taste"."""
    words = line.split()
    if not 1 <= len(words) <= 6 or len(line) > 50 or SENTENCE_END.search(line) or line.endswith((",", "-", "–", "—", "&")):
        return False
    if not line[:1].isupper() or BULLET.match(line) or ":" in line or sum(c.isalpha() for c in line) < 3:
        return False
    return all(w[:1].isupper() or w.lower() in SMALL_WORDS or not w[:1].isalpha() for w in words)


def _join(a: str, b: str) -> str:
    """Two lines of one paragraph: 're-' + 'strategized' -> 're-strategized'; 'compa-' + 'ny' -> 'company'."""
    if a.endswith("-") and len(a) > 1 and a[-2].isalpha() and b[:1].islower():
        word = re.search(r"(\w+)-$", a)
        if word and len(word.group(1)) > 2 and b.split()[0].isalpha():
            return a[:-1] + b                       # a word split at the line end
        return a + b                                # a real hyphen: "re-" + "strategized"
    return f"{a} {b}"


def _local_typical(lengths: list[int]) -> list[float]:
    """Each line's usual length: the median of the lines around it. A page can have a narrow column next to a wide one, so one
    median for the whole page would call every line of the narrow one short."""
    return [statistics.median(lengths[max(0, i - 4):i + 5]) or 1 for i in range(len(lengths))]


def _clean_lines(text: str, running_headers, skip) -> list[str]:
    lines = [_norm(ln) for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln and not PAGE_NUMBER.match(ln) and not (running_headers and is_running_header(ln, running_headers))
             and not (len(ln) == 1 and ln.isalpha())]
    if skip:
        lines = [ln for ln in lines if not skip(ln)]
    return lines


def blocks(text: str, headings: list[str] | None = None, running_headers: set[str] | None = None,
           skip=None) -> list[dict]:
    """The text as blocks: {"kind": "h" | "sub" | "p" | "li" | "quote", "text": ...}; a "p" or "li" may also have "lead" (a label
    before a colon, given without it in "text"). `skip(line)` can leave lines out (table rows shown as tables)."""
    heads = {_norm(h).lower() for h in (headings or [])}
    lines = _clean_lines(text, running_headers, skip)
    if not lines:
        return []
    typical = _local_typical([len(ln) for ln in lines])
    out: list[dict] = []
    open_block = False                              # can the next line still join the last block?
    after_heading = False                           # was the line before a heading? (a heading over two lines is one)
    sentence_done = True                            # did the line before end a sentence (or is this the first line)?
    bullet_next = False                             # the line before was a bullet on its own
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        last = out[-1] if out else None
        if LONE_BULLET.match(ln):
            bullet_next, open_block = True, False
            continue
        heading = ln.lower() in heads or (_is_caps(ln) and not BULLET.match(ln))
        if heading:
            if after_heading and last and last["kind"] in ("h", "quote"):
                last["text"] = _join(last["text"], ln)
            else:
                out.append({"kind": "h", "text": ln})
            after_heading, open_block, sentence_done = True, False, True
            continue
        if (not bullet_next and (sentence_done or after_heading or not open_block) and _is_title(ln)
                and nxt is not None and nxt[:1].isupper() and not _is_title(nxt)):
            out.append({"kind": "sub", "text": ln})
            after_heading, open_block, sentence_done = True, False, True
            continue
        after_heading = False
        ends = bool(SENTENCE_END.search(ln))
        if bullet_next or BULLET.match(ln):
            out.append({"kind": "li", "text": BULLET.sub("", ln, count=1)})
            bullet_next = False
            open_block = not ends or len(ln) >= typical[i] * 0.75
            sentence_done = ends
            continue
        long_enough = last is not None and len(last["text"]) > LONG_PARAGRAPH and sentence_done and ln[:1].isupper()
        label = LEAD_LINE.match(ln)                             # "Other appointments:" on its own line: the text after it is its own
        lead = LEAD.match(ln)                                   # "Areas of expertise: ..." after a finished sentence; "Education: ..." always
        new_lead = (sentence_done and (bool(label) or bool(lead))) or bool(lead and len(lead.group(1).split()) <= 2)
        joins = (last is not None and last["kind"] in ("p", "li") and (open_block or ln[:1].islower()) and not long_enough
                 and not new_lead)
        if joins:
            last["text"] = _join(last["text"], ln)
        else:
            out.append({"kind": "p", "text": ln})
        # a sentence that ends on a short line ends the paragraph; a full line usually runs on
        open_block = bool(label) or not (ends and len(ln) < typical[i] * 0.8)
        sentence_done = ends and not label
    for b in out:
        if b["kind"] == "h" and len(b["text"].split()) > 14:
            b["kind"] = "quote"
        if b["kind"] in ("p", "li"):
            m = LEAD.match(b["text"])
            if m and len(m.group(1).split()) <= 6 and not any(c.isdigit() for c in m.group(1)):
                b["lead"], b["text"] = m.group(1), b["text"][m.end():]
    return out
