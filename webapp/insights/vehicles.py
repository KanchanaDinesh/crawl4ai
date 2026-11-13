"""Vehicles tab: the latest ads from ikman.lk and riyasewana.com, cars and motorcycles.

The category switch at the top reloads the whole tab (numbers, charts and list all follow it); the other filters only
swap the list."""

from . import charts, queries
from .common import (PAGE, Reader, bars, int_param, kpis, list_response, page, period_param, shown, tab_required, text_param,
                     url_with)
from .templatetags.insights_ui import lkr, num


def _short_price(value) -> str:
    if value >= 1e6:
        return f"Rs {value / 1e6:.1f}M"
    return f"Rs {int(value / 1e3 + 0.5)}K" if value >= 1e3 else f"Rs {value}"


def _filters(request) -> dict:
    category = text_param(request, "category")
    return {"days": period_param(request, 30), "category": category if category in queries.VEHICLE_CATEGORIES else "",
            "site": text_param(request, "site"), "new": request.GET.get("new") == "1", "q": text_param(request, "q")}


def category_name(code) -> str:
    return queries.VEHICLE_CATEGORIES.get(code or "", code or "unknown")


def _category_links(f: dict, categories: list[dict]) -> list[dict]:
    """All · Cars · Motorcycles · ...: links, each with its number of ads, the chosen one marked."""
    total = sum(c["ads"] for c in categories)
    links = [{"name": "All vehicles", "n": total, "on": not f["category"], "href": url_with("insights_vehicles", f, category="")}]
    order = list(queries.VEHICLE_CATEGORIES)            # cars, motorcycles, ... other last
    for c in sorted(categories, key=lambda c: order.index(c["category"]) if c["category"] in order else len(order)):
        if c["category"]:
            links.append({"name": category_name(c["category"]), "n": c["ads"], "on": f["category"] == c["category"],
                          "href": url_with("insights_vehicles", f, category=c["category"])})
    return links


def _ads_query(f: dict) -> dict:
    return {"site": f["site"] or None, "category": f["category"] or None, "q": f["q"] or None, "new": f["new"],
            "days": f["days"]}


def _ads_context(f: dict, data, offset: int = 0) -> dict:
    data = data or {"total": 0, "items": []}
    ads = []
    for a in data["items"]:
        changed = a["previous_price_lkr"] and a["price_lkr"] is not None and a["price_lkr"] != a["previous_price_lkr"]
        ads.append({**a, "meta": " · ".join(str(x) for x in (a["location"], a["year"], a["mileage"]) if x) or a["posted"] or "",
                    "category_name": a.get("category") or "unknown",   # "motorcycle", one ad
                    "was": a["previous_price_lkr"] if changed else None,
                    "dropped": bool(changed and a["price_lkr"] < a["previous_price_lkr"])})
    end = offset + len(ads)
    more = url_with("insights_vehicle_ads", f, offset=end) if end < data["total"] else None
    return {"ads": ads, "more": more, "count": shown(data["total"], end, "newest first")}


@tab_required("vehicles")
def page_view(request):
    f = _filters(request)
    with Reader() as db:
        s = db.get(queries.vehicles_summary, category=f["category"] or None)
        changes = db.get(queries.vehicle_price_changes, category=f["category"] or None, default=[])
        data = db.get(queries.vehicle_ads, **_ads_query(f), limit=PAGE, offset=0) if s else None
    context = {"f": f, "s": s}
    if s:
        c = s["counts"]
        context.update(
            kpis=kpis((num(c["ads"]), "Ads tracked"), (num(c["new_last_run"]), "New in last run"), (num(c["new_24h"]), "New in 24 hours"),
                      (num(c["price_drops"]), "Price drops (7 days)"), (lkr(c["median_price"]), "Median price")),
            categories=_category_links(f, s["categories"]),
            chips=[{"code": "", "name": "All", "n": c["ads"]}] + _site_chips(s["sites"]),
            sites=[{**x, "category_name": category_name(x["category"])} for x in s["sites"]], day_chart=charts.day_columns(s["by_day"], "n", rest_label="new ads"),
            prices=bars(s["price_ranges"], "range"),
            makes=bars(s["makes"], lambda r: r["make"] + (f" · {_short_price(r['median_price'])}" if r["median_price"] else "")),
            changes=[{**x, "down": x["price_lkr"] < x["previous_price_lkr"]} for x in changes[:15]], **_ads_context(f, data))
    return page(request, "vehicles", db, **context)


@tab_required("vehicles")
def ads_view(request):
    f = _filters(request)
    offset = int_param(request, "offset", 0, lo=0)
    with Reader() as db:
        data = db.get(queries.vehicle_ads, **_ads_query(f), limit=PAGE, offset=offset)
    context = _ads_context(f, data, offset)
    return list_response(request, "insights/_vehicle_ads.html", context, context["count"],
                         url_with("insights_vehicles", f) if offset == 0 else None)


def _site_chips(sites: list[dict]) -> list[dict]:
    """One chip per site (ikman, riyasewana), adding up its listings."""
    n: dict[str, int] = {}
    for x in sites:
        n[x["site"]] = n.get(x["site"], 0) + x["ads"]
    return [{"code": name, "name": name, "n": count} for name, count in n.items()]
