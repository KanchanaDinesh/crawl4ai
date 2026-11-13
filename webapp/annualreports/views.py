"""The Annual reports page: read what `python -m annualreports` saved from the PDFs. It only reads.

Who sees it: the menu right Annual reports, plus the right to see the dataset Annual reports. The page shows that data, so taking the
dataset away from a role in the catalog also closes this page, or it would be a way around the rights.
"""

import re
from dataclasses import dataclass
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.html import escape
from django.utils.safestring import mark_safe

from annualreports import queries as q
from annualreports import reflow
from webapp.accounts.rights import menu_required
from webapp.catalog.models import Dataset
from webapp.catalog.rights import can_view, is_admin
from webapp.insights.common import PAGE, Reader, int_param, kpis, list_response, shell, shown, text_param, url_with
from webapp.insights.templatetags.insights_ui import num

STATEMENTS = {"income": "Income statement", "balance": "Statement of financial position", "cash_flow": "Cash flows",
              "equity": "Changes in equity", "summary": "Multi-year summary", "highlights": "Highlights"}


@dataclass(frozen=True)
class Tab:
    code: str
    label: str
    url_name: str


TABS = (Tab("reports", "Reports", "annualreports"), Tab("search", "Search the pages", "annualreports_search"),
        Tab("people", "People", "annualreports_people"))


def _may_read(user) -> bool:
    if is_admin(user):
        return True
    dataset = Dataset.objects.filter(schema="annualreports", name="v_documents").first()
    return dataset is not None and can_view(user, dataset)


def readers_only(view):
    @wraps(view)
    @menu_required("annualreports")
    def wrapper(request, *args, **kwargs):
        if not _may_read(request.user):
            raise PermissionDenied("Your roles don't include the dataset Annual reports. An administrator can grant it on the Catalog page.")
        return view(request, *args, **kwargs)
    return wrapper


def _shell(request, code: str, db, **context):
    ctx = shell(request, code, db, **context)
    ctx.update(tabs=TABS, tab=code)
    return ctx


def _company(request, db) -> tuple[str, list[dict]]:
    """The company filter, only one of the companies there are (anything else is ignored), and the choices."""
    choices = db.get(q.company_choices, default=[])
    company = text_param(request, "company")
    return (company if company in {c["name"] for c in choices} else ""), choices


def marked(snippet: str | None) -> str:
    """ts_headline's extract with the found words marked. The text is the PDF's: escaped first, so it can't add HTML."""
    text = escape(" ".join((snippet or "").split()))
    return mark_safe(text.replace(escape(q.MARK_START), "<mark>").replace(escape(q.MARK_END), "</mark>"))


# ── reports ─────────────────────────────────────────────────────────────────────────────────────────────────
def _figure(value: str | None, unit: str | None, note: str = "") -> dict | None:
    """The number as printed, and under it its unit (and what it is, when it stands in for revenue)."""
    return {"value": value, "unit": ", ".join(x for x in (note, unit or "") if x)} if value else None


def _report_rows(db, f: dict) -> dict:
    rows = db.get(q.reports, f["company"] or None, f["q"] or None, default=[])
    for r in rows:
        r["shown"] = {m: _figure(r[m], r[f"{m}_unit"]) for m in q.LIST_METRICS}
        plain = f"{r['company']} Annual Report {r['fiscal_year'] or ''}".strip()
        r["part"] = "" if (r["title"] or "") == plain else (r["title"] or r["file_name"])      # HSBC's parts: "Pillar 3 Disclosures ..."
        if not r["shown"]["revenue"] and r["other_income"]:            # a bank or finance company: its income line instead
            r["shown"]["revenue"] = _figure(r["other_income"], r["other_income_unit"],
                                            q.METRICS[r["other_income_metric"]].lower())
    return {"rows": rows, "count": f"{len(rows):,} · click one to open it"}


def _report_filters(request, db) -> dict:
    company, choices = _company(request, db)
    return {"company": company, "q": text_param(request, "q"), "choices": choices}


@readers_only
def page_view(request):
    with Reader() as db:
        f = _report_filters(request, db)
        s = db.get(q.summary, default=None)
        context = {"f": f, "s": s if s and s["reports"] else None, "metric_names": q.METRICS}
        if context["s"]:
            context.update(kpis=kpis((num(s["reports"]), "Reports"), (num(s["companies"]), "Companies"), (num(s["pages"]), "Pages"),
                                     (num(s["tables"]), "Tables"), (num(s["figures"]), "Key figures"), (num(s["people"]), "People")),
                           **_report_rows(db, f))
    return render(request, "annualreports/page.html", _shell(request, "reports", db, **context))


@readers_only
def rows_view(request):
    with Reader() as db:
        f = _report_filters(request, db)
        context = _report_rows(db, f)
    return list_response(request, "annualreports/_reports.html", context, context["count"],
                         url_with("annualreports", {"company": f["company"], "q": f["q"]}))


# ── a report: one long page with every page of it, each under a line with its number ─────────────────────────
NUMBER = re.compile(r"^\(?[-–−]?[\d,.]+\)?%?$|^[-–—]$")


def _terms(words: str) -> list[str]:
    """The words of a search to mark on the page: phrases in quotes whole, -words left out."""
    found = re.findall(r'"([^"]+)"|(\S+)', words or "")
    return [a or b for a, b in found if (a or b) and not (b or "").startswith("-") and len(a or b) > 1]


def _marked_html(text: str, terms: list[str]) -> str:
    html = escape(text)
    for t in sorted(terms, key=len, reverse=True):
        html = re.sub(rf"(?i)(?<![\w>])({re.escape(escape(t))})(?![\w<])", r"<mark>\1</mark>", html)
    return mark_safe(html)


def _table_rows_text(tables: list[dict]) -> set[str]:
    return {re.sub(r"\s+", "", " ".join(r["cells"])) for t in tables for r in t["rows"]}


def _prose(text: str, headings: list[str], running_headers: set[str], tables: list[dict], terms: list[str]) -> list[dict]:
    """The text as paragraphs, headings and lists. Lines that are rows of the tables shown above it are left out (they'd be the same
    numbers twice, without their columns); "as printed" shows everything."""
    rows = _table_rows_text(tables)
    heading_words = {w for t in tables for r in t["rows"] if r["kind"] == "header" for c in r["cells"] for w in c.split()}

    def table_line(line: str) -> bool:
        words = line.split()
        trailing = 0
        for w in reversed(words):
            if not NUMBER.match(w):
                break
            trailing += 1
        return bool(tables) and (re.sub(r"\s+", "", line) in rows or trailing >= 2
                                 or bool(words) and all(w in heading_words for w in words))      # "Group  Company" over the columns

    out: list[dict] = []
    for b in reflow.blocks(text, headings, running_headers, skip=table_line if tables else None):
        b["html"] = _marked_html(b["text"], terms)
        b["lead_html"] = _marked_html(b["lead"], terms) if b.get("lead") else ""
        if b["kind"] == "li":                               # bullet points in a row are one list
            if out and out[-1]["kind"] == "ul":
                out[-1]["items"].append(b)
            else:
                out.append({"kind": "ul", "items": [b]})
        else:
            out.append(b)
    return out


def _tables_named(tables: list[dict]) -> list[dict]:
    for t in tables:
        t["statement_name"] = STATEMENTS.get(t["statement"], "")
    return tables


def _at(request, doc_id: int, anchor: str):
    """The report's page, keeping its search words and "as printed", at one place on it."""
    keep = {k: request.GET[k] for k in ("q", "raw") if request.GET.get(k)}
    return redirect(url_with("annualreports_report", keep, doc_id) + f"#{anchor}")


def _whole(d: dict, whole: dict, terms: list[str], raw: bool) -> list[dict]:
    """Every page in order, with its tables, its text (formatted, or as printed) and the sections that start on it."""
    headers = set(d.get("running_headers") or [])
    top = min((s["level"] for s in whole["sections"]), default=0)
    starts: dict[int, list[dict]] = {}
    for s in whole["sections"]:
        starts.setdefault(s["page_no"], []).append({**s, "top": s["level"] == top})
    tables: dict[int, list[dict]] = {}
    for t in _tables_named(whole["tables"]):
        tables.setdefault(t["page_no"], []).append(t)
    out = []
    for p in whole["pages"]:
        n, own = p["page_no"], tables.get(p["page_no"], [])
        firsts = [s["title"] for s in starts.get(n, []) if s["top"]]
        page = {"no": n, "sections": starts.get(n, []), "chapter": firsts[0] if firsts else "", "tables": own, "note": p["note"], "raw": raw,
                "text": p["text"] or "", "ocr_text": p["ocr_text"] or "", "ocr_confidence": p["ocr_confidence"],
                "source": q.TEXT_SOURCE.get(p["text_source"], p["text_source"]) if p["text_source"] != "pdf" else ""}
        if not raw:
            page["prose"] = _prose(page["text"], p["headings"] or [], headers, own, terms)
            page["ocr_prose"] = _prose(page["ocr_text"], [], headers, [], terms) if page["ocr_text"] else []
        elif terms:
            page["text"], page["ocr_text"] = _marked_html(page["text"], terms), _marked_html(page["ocr_text"], terms)
        page["empty"] = not (own or page.get("prose") or page.get("ocr_prose") or (raw and (page["text"].strip() or page["ocr_text"])))
        out.append(page)
    return out


@readers_only
def report_view(request, doc_id: int):
    """A whole report on one page: its key figures and contents at the top, then every page in order, each under a line with its
    number. ?page=N goes to that page's place on it; ?q= marks words; ?raw=1 shows the text as printed."""
    wanted = request.GET.get("page", "")
    if wanted.isdigit():
        return _at(request, doc_id, f"page-{max(1, int(wanted))}")
    terms, raw = _terms(text_param(request, "q")), request.GET.get("raw") == "1"
    with Reader() as db:
        d = db.get(q.report, doc_id, default=None)
        if d is None and not db.error:
            raise Http404
        whole = db.get(q.pages, doc_id, default=None) if d else None
        context = _shell(request, "reports", db, missing=d is None)
    if d and whole:
        order = list(q.METRICS)
        figures = sorted(d["key_figures"], key=lambda k: order.index(k["metric"]) if k["metric"] in order else 99)
        for k in figures:
            k["name"] = q.METRICS.get(k["metric"], k["metric"])
            k["statement_name"] = STATEMENTS.get(k["statement"], k["statement"])
        context.update(d=d, figures=figures, raw=raw, q=text_param(request, "q"), pages=_whole(d, whole, terms, raw),
                       statements=[{**s, "name": STATEMENTS.get(s["statement"], s["statement"])} for s in d["statements"]])
    return render(request, "annualreports/report.html", context)


@readers_only
def page_text_view(request, doc_id: int, page_no: int):
    """Older links to one page (and search results): the report's page, at that page."""
    return _at(request, doc_id, f"page-{page_no}")


@readers_only
def section_view(request, doc_id: int, section_no: int):
    return _at(request, doc_id, f"section-{section_no}")


# ── search ──────────────────────────────────────────────────────────────────────────────────────────────────
def _search_rows(db, f: dict, offset: int = 0) -> dict:
    if not f["q"]:
        return {"hits": [], "more": None, "count": "type words to search every page", "searched": False}
    data = db.get(q.search, f["q"], f["company"] or None, PAGE, offset, default={"total": 0, "items": []})
    hits = [{**h, "snippet_html": marked(h["snippet"])} for h in data["items"]]
    end = offset + len(hits)
    more = url_with("annualreports_hits", {"company": f["company"], "q": f["q"]}, offset=end) if end < data["total"] else None
    return {"hits": hits, "more": more, "count": shown(data["total"], end, "pages; click one to read it"), "searched": True, "q": f["q"]}


@readers_only
def search_view(request):
    with Reader() as db:
        company, choices = _company(request, db)
        f = {"company": company, "q": text_param(request, "q"), "choices": choices}
        context = {"f": f, **_search_rows(db, f)}
    return render(request, "annualreports/search.html", _shell(request, "search", db, **context))


@readers_only
def hits_view(request):
    offset = int_param(request, "offset", 0, lo=0)
    with Reader() as db:
        company, _ = _company(request, db)
        f = {"company": company, "q": text_param(request, "q")}
        context = _search_rows(db, f, offset)
    return list_response(request, "annualreports/_hits.html", context, context["count"],
                         url_with("annualreports_search", f) if offset == 0 else None)


# ── people ──────────────────────────────────────────────────────────────────────────────────────────────────
def _people_rows(db, f: dict, offset: int = 0) -> dict:
    data = db.get(q.people, f["company"] or None, f["q"] or None, PAGE, offset, default={"total": 0, "items": []})
    end = offset + len(data["items"])
    more = url_with("annualreports_people_rows", {"company": f["company"], "q": f["q"]}, offset=end) if end < data["total"] else None
    return {"rows": data["items"], "more": more, "count": shown(data["total"], end, "as printed on the board and management pages")}


@readers_only
def people_view(request):
    with Reader() as db:
        company, choices = _company(request, db)
        f = {"company": company, "q": text_param(request, "q"), "choices": choices}
        context = {"f": f, **_people_rows(db, f)}
    return render(request, "annualreports/people.html", _shell(request, "people", db, **context))


@readers_only
def people_rows_view(request):
    offset = int_param(request, "offset", 0, lo=0)
    with Reader() as db:
        company, _ = _company(request, db)
        f = {"company": company, "q": text_param(request, "q")}
        context = _people_rows(db, f, offset)
    return list_response(request, "annualreports/_people.html", context, context["count"],
                         url_with("annualreports_people", f) if offset == 0 else None)
