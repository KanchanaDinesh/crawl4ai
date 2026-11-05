"""Tech Intel tab: new tech websites found, qualified, dated, profiled and monitored."""

from django.shortcuts import render

from . import charts, queries
from .common import (PAGE, Reader, bars, int_param, kpis, list_response, page, period_label, period_param, shown, tab_required,
                     text_param, url_with)
from .templatetags.insights_ui import num

STATUS_ORDER = ("qualified", "watch", "rejected", "not checked")
STATUS_TONE = {"qualified": "ok", "watch": "warn", "rejected": "bad", "not checked": "muted"}
CHANGE = {"tech_added": "started using", "tech_removed": "stopped using", "page_added": "added page", "page_removed": "removed page",
          "signal_on": "now has", "signal_off": "no longer has", "site_down": "went offline", "site_up": "back online",
          "description_changed": "changed description", "name_changed": "renamed", "job_links_changed": "job links",
          "content_changed": "updated page"}


def change_text(x: dict) -> str:
    label = CHANGE.get(x["change_type"], x["change_type"])
    kind = x["change_type"]
    if kind == "tech_added":
        return f"{label} {x['after']}"
    if kind == "tech_removed":
        return f"{label} {x['before']}"
    if kind == "job_links_changed":
        return f"{label}: {x['before']} → {x['after']}"
    if kind in ("signal_on", "signal_off", "page_added", "page_removed", "content_changed"):
        return f"{label} {str(x['field']).replace('_', ' ')}"
    return label


def _filters(request) -> dict:
    sort, company = request.GET.get("sort"), request.GET.get("company")
    return {"days": period_param(request, 30), "status": text_param(request, "status"), "category": text_param(request, "category"),
            "source": text_param(request, "source"), "company": company if company in queries.TECH_COMPANY else "",
            "q": text_param(request, "q"), "sort": sort if sort in queries.TECH_SORT and sort != "seen" else ""}


def _rows_context(f: dict, data, offset: int = 0) -> dict:
    data = data or {"total": 0, "items": []}
    end = offset + len(data["items"])
    more = url_with("insights_tech_rows", f, offset=end) if end < data["total"] else None
    rest = "scroll the list for more · click a row for details" if more else "click a row for details"
    rows = [{**r, "n": offset + i + 1, "bar": round(40 * min(r["score"], 100) / 100) if r["score"] is not None else 0}
            for i, r in enumerate(data["items"])]
    return {"rows": rows, "more": more, "count": shown(data["total"], end, rest)}


def _rows(db, f, offset=0):
    return db.get(queries.tech_domains, status=f["status"] or None, category=f["category"] or None, source=f["source"] or None,
                  q=f["q"] or None, days=f["days"], limit=PAGE, offset=offset, sort=f["sort"] or "seen", company=f["company"] or None)


@tab_required("tech")
def page_view(request):
    f = _filters(request)
    with Reader() as db:
        s = db.get(queries.tech_summary, f["days"])
        changes = db.get(queries.tech_changes, f["days"], default=[])
        runs = db.get(queries.tech_runs, default=[])
        data = _rows(db, f) if s else None
    context = {"f": f, "s": s}
    if s:
        c, label = s["counts"], period_label(f["days"])
        st = {r["status"]: r["n"] for r in s["by_status"]}
        order = [k for k in STATUS_ORDER if st.get(k)]
        context.update(
            kpis=kpis((num(c["new_candidates"]), f"New domains, {label}"), (num(c["qualified"]), "Qualified tech sites"), (num(c["watch"]), "On watch"),
                      (num(c["rejected"]), "Rejected"), (num(c["profiled"]), "Profiled"), (num(c["new_domains_30d"]), "Qualified on domains under 30 days old"),
                      (num(c["changes"]), f"Changes, {label}"), (num(c["candidates"]), "Domains (all time)"),
                      (num(c["with_linkedin"]), f"With LinkedIn, of {num(c['companies'])} companies read"), (num(c["with_people"]), "Name their leadership")),
            period=label, companies=c["companies"], chips=[{"code": "", "name": "All", "n": sum(st.values())}] + [{"code": k, "name": k, "n": st[k]} for k in order],
            categories=[r for r in s["by_category"] if r["category"]], sources=s["by_source"],
            day_chart=charts.day_columns(s["by_day"], "n", "qualified", "qualified", "other"),
            age=bars(s["domain_age"], "age"), by_category=bars(s["by_category"][:10], "category"),
            status_parts=charts.stack_parts([{"label": k, "n": st[k], "tone": STATUS_TONE[k]} for k in order]),
            stack=bars(s["tech_stack"], "tech"), social=bars(s["social_platforms"], "platform"), by_source=bars(s["by_source"], "source"),
            changes=[{**x, "text": change_text(x)} for x in changes[:25]],
            runs=[{**r, "took": _took(r["seconds"])} for r in runs], **_rows_context(f, data))
    return page(request, "tech", db, **context)


def _took(seconds) -> str:
    if seconds is None:
        return ""
    seconds = float(seconds)
    return f"{int(seconds // 60)}m{int(seconds % 60 + 0.5):02d}s" if seconds >= 60 else f"{int(seconds + 0.5)}s"


@tab_required("tech")
def rows_view(request):
    f = _filters(request)
    offset = int_param(request, "offset", 0, lo=0)
    with Reader() as db:
        data = _rows(db, f, offset)
    context = _rows_context(f, data, offset)
    return list_response(request, "insights/_tech_rows.html", context, context["count"],
                         url_with("insights_tech", f) if offset == 0 else None)


@tab_required("tech")
def site_view(request, domain: str):
    """One site's details, in the side panel."""
    with Reader() as db:
        d = db.get(queries.tech_domain, domain, default={})
    checks = d.get("qualifications") or []
    q, p, c = (checks[-1] if checks else {}), d.get("profile"), d.get("candidate") or {}
    context = {
        "domain": domain, "q": q, "p": p, "c": c, "reg": d.get("registration") or {}, "company": d.get("company"),
        "detail": c.get("detail") or {}, "name": (p and p.get("name")) or q.get("title") or domain,
        "url": (p and p.get("final_url")) or q.get("final_url") or c.get("url") or f"https://{domain}/",
        "description": (p and p.get("description")) or q.get("description"),
        "signals": [k.replace("_", " ") for k, v in ((p or {}).get("signals") or {}).items() if v],
        "key_pages": list(((p or {}).get("key_pages") or {}).items()), "social": list(((p or {}).get("social") or {}).items()),
        "changes": [{**x, "text": change_text(x)} for x in d.get("changes") or []], "checks": list(reversed(checks)), "banner": db.error}
    return render(request, "insights/_tech_site.html", context)
