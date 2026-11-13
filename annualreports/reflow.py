"""A page's text, made readable: the PDF breaks every line where it was set, so this joins lines back into paragraphs, finds headings
and bullet points, mends words split by a hyphen at a line end, and leaves out the running header and the page number.

Only for showing. What's saved (pages.text) stays exactly as the PDF has it, and the page view offers that too ("as printed").
Rules, all from what the reports do:
- a line ending a sentence ('.', '!', '?', ':') that is clearly shorter than the page's usual line ends its paragraph;
- a short line in capitals, or one of the page's headings (bigger or bold font), is a heading; several in a row are one heading,
  or a pull quote when it's long;
- a line starting with a bullet ('•', '–', '-', '●', '▪', '(a)', '1.') starts a list item; the lines after it continue it.
"""

import re
import statistics

from .identify import is_running_header

BULLET = re.compile(r"^(?:[•●▪◦·*–—-]\s+|\(?[a-z]\)\s+|\(?[ivx]{1,4}\)\s+|\d{1,2}[.)]\s+)", re.I)
SENTENCE_END = re.compile(r"[.!?:;][\"'’”)]*$")
PAGE_NUMBER = re.compile(r"^(?:page\s+)?\d{1,4}$|^[ivxlc]{1,6}$", re.I)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _is_caps(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 3 and sum(c.isupper() for c in letters) / len(letters) > 0.85 and len(line.split()) <= 12


def _join(a: str, b: str) -> str:
    """Two lines of one paragraph: 're-' + 'strategized' -> 're-strategized'; 'compa-' + 'ny' -> 'company'."""
    if a.endswith("-") and len(a) > 1 and a[-2].isalpha() and b[:1].islower():
        word = re.search(r"(\w+)-$", a)
        if word and len(word.group(1)) > 2 and b.split()[0].isalpha():
            return a[:-1] + b                       # a word split at the line end
        return a + b                                # a real hyphen: "re-" + "strategized"
    return f"{a} {b}"


def blocks(text: str, headings: list[str] | None = None, running_headers: set[str] | None = None,
           skip=None) -> list[dict]:
    """The text as blocks: {"kind": "h" | "p" | "li" | "quote", "text": ...}. `skip(line)` can leave lines out (table rows shown as
    tables)."""
    heads = {_norm(h).lower() for h in (headings or [])}
    lines = [_norm(ln) for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln and not PAGE_NUMBER.match(ln) and not (running_headers and is_running_header(ln, running_headers))]
    if skip:
        lines = [ln for ln in lines if not skip(ln)]
    if not lines:
        return []
    typical = statistics.median(len(ln) for ln in lines) or 1
    out: list[dict] = []
    open_block = False                              # can the next line still join the last block?
    after_heading = False                           # was the line before a heading? (a heading over two lines is one)
    for ln in lines:
        heading = ln.lower() in heads or (_is_caps(ln) and not BULLET.match(ln))
        last = out[-1] if out else None
        if heading:
            if after_heading and last and last["kind"] in ("h", "quote"):
                last["text"] = _join(last["text"], ln)
            else:
                out.append({"kind": "h", "text": ln})
            after_heading, open_block = True, False
            continue
        after_heading = False
        if BULLET.match(ln):
            out.append({"kind": "li", "text": BULLET.sub("", ln, count=1)})
            open_block = not SENTENCE_END.search(ln) or len(ln) >= typical * 0.75
            continue
        joins = last is not None and last["kind"] in ("p", "li") and (open_block or ln[:1].islower())
        if joins:
            last["text"] = _join(last["text"], ln)
        else:
            out.append({"kind": "p", "text": ln})
        # a sentence that ends on a short line ends the paragraph; a full line usually runs on
        open_block = not (SENTENCE_END.search(ln) and len(ln) < typical * 0.8)
    for b in out:
        if b["kind"] == "h" and len(b["text"].split()) > 14:
            b["kind"] = "quote"
    return out
