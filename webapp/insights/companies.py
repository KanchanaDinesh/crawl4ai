"""Companies tab: the company directory (python -m companies): where each came from and what its website says."""

from urllib.parse import urlsplit

from django.shortcuts import render

from . import charts, queries
from .common import PAGE, Reader, bars, int_param, kpis, list_response, page, shown, tab_required, text_param, url_with
from .templatetags.insights_ui import SOCIAL, num

SITE_STATUS = {"ok": "read", "not read": "not read", "no website": "no website", "unreachable": "unreachable", "not_allowed": "robots.txt: no"}
SOURCE_NAME = {"wikidata": "Wikidata", "gleif": "GLEIF (LEI)", "cse": "CSE (Sri Lanka)", "companies_house": "Companies House (UK)",
               "asic": "ASIC (Australia)", "sec": "SEC EDGAR (US)"}
EXTRA_LABEL = {"type": "Type (Wikidata)", "wikidata": "Wikidata", "city": "City", "legal_form": "Legal form", "registered_as": "Registration no.",
               "jurisdiction": "Jurisdiction", "created": "Created", "symbol": "CSE symbol", "established": "Established", "board": "Board",
               "address": "Registered office", "business": "Business", "status": "Status", "category": "Category", "incorporated": "Incorporated",
               "town": "Town", "postcode": "Postcode", "companies_house": "Companies House", "abn": "ABN", "company_class": "Class",
               "registered": "Registered", "state": "State", "ticker": "Ticker", "edgar": "SEC EDGAR",
               "english_name": "Name in English", "latin_name": "Name in Latin letters"}


STATUS_CHOICES = (("website", "Has a website"), ("read", "Website read"), ("not_read", "Website not read yet"), ("failed", "Website couldn't be read"),
                  ("no_website", "No website"), ("social", "Has social links"), ("linkedin", "Has LinkedIn"), ("people", "Names its people"),
                  ("employees", "Gives employees"))


def source_name(code) -> str:
    return SOURCE_NAME.get(code, code)


def _filters(request) -> dict:
    """names=all is the switch "Show names in other scripts" (off: only names in English letters, Kanchana's choice)."""
    status, sort = request.GET.get("status"), request.GET.get("sort")
    return {"country": text_param(request, "country").upper(), "source": text_param(request, "source"),
            "status": status if status in queries.COMPANY_FILTERS else "", "q": text_param(request, "q"),
            "sort": sort if sort in queries.COMPANY_SORT and sort != "best" else "",
            "names": "all" if request.GET.get("names") == "all" else ""}


def _list(db, f, offset=0):
    return db.get(queries.companies_list, country=f["country"] or None, source=f["source"] or None, status=f["status"] or None,
                  q=f["q"] or None, sort=f["sort"] or "best", limit=PAGE, offset=offset, everything=bool(f["names"]))


def _rows_context(f: dict, data, offset: int = 0) -> dict:
    data = data or {"total": 0, "items": []}
    end = offset + len(data["items"])
    more = url_with("insights_company_rows", f, offset=end) if end < data["total"] else None
    rest = "scroll the list for more · click a row for details" if more else "click a row for details"
    # The name as the source gives it ("国泰金融控股股份有限公司") goes under the English one only with the switch on:
    # otherwise the list shows English names only. The details panel always has it.
    rows = [{**r, "n": offset + i + 1, "status_label": SITE_STATUS.get(r["site_status"], r["site_status"]),
             "source_label": source_name(r["source"]), "original_name": r.get("original_name") if f["names"] else None}
            for i, r in enumerate(data["items"])]
    return {"rows": rows, "more": more, "count": shown(data["total"], end, rest)}


@tab_required("companies")
def page_view(request):
    f = _filters(request)
    with Reader() as db:
        s = db.get(queries.companies_summary, everything=bool(f["names"]))
        data = _list(db, f) if s and (s["counts"]["companies"] or s["counts"]["other_scripts"]) else None
    context = {"f": f, "s": s if s and (s["counts"]["companies"] or s["counts"]["other_scripts"]) else None}
    if context["s"]:
        c = s["counts"]
        countries = [r for r in s["by_country"] if r["country"]]
        context.update(
            kpis=kpis((num(c["companies"]), "Companies"), (num(c["with_website"]), "With a website"), (num(c["sites_ok"]), "Websites read"),
                      (num(c["sites_with_social"]), "With social links"), (num(c["countries"]), "Countries"), (num(c["new_24h"]), "Added in 24 hours")),
            total=c["companies"], countries=countries, other_scripts=c["other_scripts"], english_given=c["english_given"],
            names_switch=url_with("insights_companies", {**f, "names": "" if f["names"] else "all"}), status_choices=STATUS_CHOICES, chips=[{"code": "", "name": "All", "n": c["companies"]}] +
            [{"code": x["source"], "name": source_name(x["source"]), "n": x["n"]} for x in s["by_source"]],
            day_chart=charts.day_columns(s["by_day"], "n", "with_website", "with a website", "without"),
            top_countries=bars(s["by_country"][:12], "name", set_field="country", set_value=lambda r: r["country"] or "",
                               title=lambda r: f"{r['name']}: {r['n']:,} companies, {r['with_website']:,} with a website, {r['sites_ok']:,} read"),
            by_status=bars(s["by_status"], lambda r: SITE_STATUS.get(r["status"], r["status"])),
            social=bars(s["social"][:10], lambda r: SOCIAL.get(r["platform"], (0, r["platform"]))[1]),
            industries=bars(s["industries"], "industry"),
            sources=[{**x, "label": source_name(x["source"])} for x in s["by_source"]],
            progress=[{**p, "label": source_name(p["source"])} for p in s["progress"]], runs=s["runs"], **_rows_context(f, data))
    return page(request, "companies", db, **context)


@tab_required("companies")
def rows_view(request):
    f = _filters(request)
    offset = int_param(request, "offset", 0, lo=0)
    with Reader() as db:
        data = _list(db, f, offset)
    context = _rows_context(f, data, offset)
    return list_response(request, "insights/_company_rows.html", context, context["count"],
                         url_with("insights_companies", f) if offset == 0 else None)


@tab_required("companies")
def detail_view(request, company_id: str):
    with Reader() as db:
        d = db.get(queries.company_detail, company_id, default={})
    c, site = d.get("company"), d.get("site")
    context = {"c": c, "site": site, "banner": db.error}
    if c:
        status = site["status"] if site else "not read" if c["website"] else "no website"
        context.update(
            status=SITE_STATUS.get(status, status), source=source_name(c["source"]),
            facts=[(EXTRA_LABEL.get(k, k), v) for k, v in (c["extra"] or {}).items()],
            pages=[(u, urlsplit(u).path or "/") for u in (site or {}).get("pages") or []],
            same=[{**x, "source_label": source_name(x["source"])} for x in d.get("same_website") or []],
            site_status=SITE_STATUS.get(site["status"], site["status"]) if site else "")
    return render(request, "insights/_company_detail.html", context)
