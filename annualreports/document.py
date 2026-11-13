"""One PDF read into what the store saves: the document, its pages, sections, tables, figures and people."""

import hashlib
import re
import statistics
import time
from pathlib import Path

from . import figures as fig
from . import identify as ident
from . import people as ppl
from .reading import READER_VERSION, metadata, ocr_pages, outline, read_pages


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _find(text: str, title: str) -> int:
    """Where a heading starts in the page's text (spaces and capitals ignored), or -1."""
    words = re.findall(r"\w+", title)[:6]
    if not words:
        return -1
    m = re.search(r"\W+".join(re.escape(w) for w in words), text, re.I)
    return m.start() if m else -1


def sections(pages, toc: list[dict], headers: set[str]) -> list[dict]:
    """The PDF's own outline if it has one (3 entries or more), otherwise the headings found on the pages. Each section's text runs
    from its heading to the next one's, across pages."""
    if len(toc) >= 3:
        found = [{"title": t["title"], "level": t["level"], "page_no": t["page_no"], "source": "outline"} for t in toc]
    else:
        sizes = [p.body_size for p in pages if p.body_size]
        body = statistics.median(sizes) if sizes else 10
        found = [{"title": h["text"], "level": 1 if h["size"] >= body * 1.8 else 2, "page_no": p.page_no, "source": "headings"}
                 for p in pages for h in p.headings if not ident.is_running_header(h["text"], headers)]
    texts = {p.page_no: p.full_text for p in pages}
    last_page = max(texts) if texts else 0
    found = [s for s in found if 1 <= s["page_no"] <= last_page]
    found.sort(key=lambda s: s["page_no"])            # stable: the outline's order within a page stays
    for s in found:
        s["start"] = max(0, _find(texts.get(s["page_no"], ""), s["title"]))
    for i, s in enumerate(found):
        nxt = found[i + 1] if i + 1 < len(found) else None
        if nxt is None:
            end_page = last_page
        elif nxt["page_no"] == s["page_no"] or nxt["start"] > 0:
            end_page = nxt["page_no"]               # the next section starts part way down that page
        else:
            end_page = nxt["page_no"] - 1           # ... or at its top
        parts = []
        for n in range(s["page_no"], end_page + 1):
            t = texts.get(n, "")
            a = s["start"] if n == s["page_no"] else 0
            b = nxt["start"] if nxt and n == nxt["page_no"] else len(t)
            if b > a:
                parts.append(t[a:b].strip())
        s["page_to"] = max(s["page_no"], end_page)
        s["text"] = "\n".join(x for x in parts if x)
    return [{k: s[k] for k in ("title", "level", "page_no", "page_to", "source", "text")} for s in found]


def continues(prev: dict, t: dict) -> bool:
    """A table without headings of its own, right after one with the same columns in the same places (the same page, or the next):
    the rest of that table (a balance sheet's equity and liabilities, a statement carried over to the next page)."""
    if any(c.strip() and not re.match(r"(?i)^notes?\b", c) for c in t["columns"]) or not any(c.strip() for c in prev["columns"]):
        return False
    if t["page_no"] - prev["page_no"] > 1 or len(t.get("spans", [])) != len(prev.get("spans", [])):
        return False
    return all(abs(a[1] - b[1]) <= 8 for a, b in zip(t["spans"], prev["spans"]))


def _statement(page, table: dict, headers: set[str]) -> str | None:
    texts = [h["text"] for h in page.headings] + [table.get("title") or ""]
    texts += [ln for ln in page.text.splitlines()[:8] if not ident.is_running_header(ln, headers)]
    return fig.statement_of(texts)


def build(path: Path, settings: dict, sha256: str | None = None, log=None) -> dict:
    """Read the PDF at `path` completely. Nothing is saved here."""
    import pypdfium2 as pdfium
    started = time.monotonic()
    pdf = pdfium.PdfDocument(str(path))
    try:
        meta, toc, page_count = metadata(pdf), outline(pdf), len(pdf)
    finally:
        pdf.close()
    pages = []
    for page in read_pages(path, settings):
        pages.append(page)
        if log and page.page_no % 50 == 0:
            log(f"  page {page.page_no} of {page_count}")
    headers = ident.running_headers([p.text for p in pages])
    ocr_pages(path, pages, settings, lambda p: "\n".join(ln for ln in p.text.splitlines() if not ident.is_running_header(ln, headers)))
    texts = [p.full_text for p in pages]
    by_hand = settings.get("documents", {}).get(path.name, {})
    doc = ident.identify(path.name, texts, meta, by_hand)

    tables, figures = [], []
    prev = None
    for page in pages:
        page.headings = [h for h in page.headings if not ident.is_running_header(h["text"], headers)]
        for t in page.tables:
            if t.get("title") and ident.is_running_header(t["title"], headers):
                t["title"] = None
            t["page_no"], t["table_no"] = page.page_no, len(tables) + 1
            if t["method"] == "aligned":
                if prev and continues(prev, t):
                    t["columns"], t["columns_from"] = list(prev["columns"]), prev["table_no"]
                    if not t["unit_text"]:
                        t["unit_text"], t["currency"], t["multiplier"] = prev["unit_text"], prev["currency"], prev["multiplier"]
                prev = t
            tables.append(t)
            statement = _statement(page, t, headers)
            t["statement"] = statement
            if statement:
                figures += fig.figures_in_table(t, statement, page.page_no, t["table_no"])
    people = ppl.collect(pages)
    doc.update(sha256=sha256 or sha256_of(path), file_name=path.name, file_size=path.stat().st_size, page_count=page_count,
               pdf_meta=meta, outline=toc, running_headers=sorted(headers), reader_version=READER_VERSION,
               link_by_hand={k: by_hand[k] for k in ("company_id", "cse_symbol") if by_hand.get(k)})
    counts = {s: sum(1 for p in pages if p.text_source == s) for s in ("pdf", "ocr", "empty", "needs_ocr", "ocr_failed")}
    doc.update(pages_text=counts["pdf"], pages_ocr=counts["ocr"], pages_empty=counts["empty"],
               pages_needs_ocr=counts["needs_ocr"] + counts["ocr_failed"])
    secs = sections(pages, toc, headers)
    doc["read_seconds"] = round(time.monotonic() - started, 1)
    return {"document": doc, "pages": pages, "sections": secs, "tables": tables, "figures": figures, "people": people}
