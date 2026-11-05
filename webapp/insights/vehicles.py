"""Vehicles tab: the latest ads from ikman.lk and riyasewana.com."""

from . import charts, queries
from .common import (PAGE, Reader, bars, int_param, kpis, list_response, page, period_param, shown, tab_required, text_param,
                     url_with)
from .templatetags.insights_ui import lkr, num


def _short_price(value) -> str:
    if value >= 1e6:
        return f"Rs {value / 1e6:.1f}M"
    return f"Rs {int(value / 1e3 + 0.5)}K" if value >= 1e3 else f"Rs {value}"


def _filters(request) -> dict:
    return {"days": period_param(request, 30), "site": text_param(request, "site"),
            "new": request.GET.get("new") == "1", "q": text_param(request, "q")}


def _ads_context(f: dict, data, offset: int = 0) -> dict:
    data = data or {"total": 0, "items": []}
    ads = []
    for a in data["items"]:
        changed = a["previous_price_lkr"] and a["price_lkr"] is not None and a["price_lkr"] != a["previous_price_lkr"]
        ads.append({**a, "meta": " · ".join(str(x) for x in (a["location"], a["year"], a["mileage"]) if x) or a["posted"] or "",
                    "was": a["previous_price_lkr"] if changed else None,
                    "dropped": bool(changed and a["price_lkr"] < a["previous_price_lkr"])})
    end = offset + len(ads)
    more = url_with("insights_vehicle_ads", f, offset=end) if end < data["total"] else None
    return {"ads": ads, "more": more, "count": shown(data["total"], end, "newest first")}


@tab_required("vehicles")
def page_view(request):
    f = _filters(request)
    with Reader() as db:
        s = db.get(queries.vehicles_summary)
        changes = db.get(queries.vehicle_price_changes, default=[])
        data = db.get(queries.vehicle_ads, site=f["site"] or None, q=f["q"] or None, new=f["new"], days=f["days"],
                      limit=PAGE, offset=0) if s else None
    context = {"f": f, "s": s}
    if s:
        c = s["counts"]
        context.update(
            kpis=kpis((num(c["ads"]), "Ads tracked"), (num(c["new_last_run"]), "New in last run"), (num(c["new_24h"]), "New in 24 hours"),
                      (num(c["price_drops"]), "Price drops (7 days)"), (lkr(c["median_price"]), "Median price")),
            chips=[{"code": "", "name": "All", "n": c["ads"]}] + [{"code": x["site"], "name": x["site"], "n": x["ads"]} for x in s["sites"]],
            sites=s["sites"], day_chart=charts.day_columns(s["by_day"], "n", rest_label="new ads"),
            prices=bars(s["price_ranges"], "range"),
            makes=bars(s["makes"], lambda r: r["make"] + (f" · {_short_price(r['median_price'])}" if r["median_price"] else "")),
            changes=[{**x, "down": x["price_lkr"] < x["previous_price_lkr"]} for x in changes[:15]], **_ads_context(f, data))
    return page(request, "vehicles", db, **context)


@tab_required("vehicles")
def ads_view(request):
    f = _filters(request)
    offset = int_param(request, "offset", 0, lo=0)
    with Reader() as db:
        data = db.get(queries.vehicle_ads, site=f["site"] or None, q=f["q"] or None, new=f["new"], days=f["days"],
                      limit=PAGE, offset=offset)
    context = _ads_context(f, data, offset)
    return list_response(request, "insights/_vehicle_ads.html", context, context["count"],
                         url_with("insights_vehicles", f) if offset == 0 else None)
