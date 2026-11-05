"""World News tab: the latest headlines, and our own analysis of them (worldnews/analysis.py)."""

from django.shortcuts import render
from django.utils import timezone

from . import charts, queries
from .common import (PAGE, Reader, bars, int_param, kpis, list_response, page, period_label, period_param, query_string,
                     shown, tab_required, text_param, url_with)
from .templatetags.insights_ui import ago, num, when

TOPIC_NAME = {"crime_justice": "crime & justice", "accidents_disasters": "accidents & disasters",
              "climate_environment": "climate & environment", "science_space": "science & space"}
GROUP_TOPIC = {"tech": "TECH", "crypto": "CRYPTO", "markets": "MARKETS"}


def _groups() -> list[dict]:
    try:
        from worldnews.settings import load_settings
        return load_settings().groups
    except Exception:  # noqa: BLE001 - the names are only a nicety: the codes still work
        return []


def _filters(request) -> dict:
    return {"days": period_param(request, 7), "group": text_param(request, "group"), "source": text_param(request, "source"),
            "topic": text_param(request, "topic"), "new": request.GET.get("new") == "1", "q": text_param(request, "q")}


def _item_args(f: dict, offset: int = 0) -> dict:
    return {"group": f["group"] or None, "source": f["source"] or None, "topic": f["topic"] or None, "new": f["new"],
            "q": f["q"] or None, "days": f["days"], "limit": PAGE, "offset": offset}


def _items_context(f: dict, data, names: dict, offset: int = 0) -> dict:
    data = data or {"total": 0, "items": []}
    items = []
    for i in data["items"]:
        items.append({**i, "when": i["published_at"] or i["first_seen_at"], "group_name": names.get(i["group_code"]) or i["group_code"] or "—",
                      "badges": [t for t in (i["topics"] or []) if GROUP_TOPIC.get(t) != i["group_code"]]})
    end = offset + len(items)
    more = url_with("insights_news_items", f, offset=end) if end < data["total"] else None
    return {"items": items, "more": more, "count": shown(data["total"], end, "newest first")}


def _names(groups) -> dict:
    return {g["code"]: g["name"] for g in groups}


def _hours(by_hour) -> list[int]:
    """Headlines per hour of the local day. The query counts by UTC hour, so shift by the local offset."""
    offset = int(timezone.localtime().utcoffset().total_seconds() // 60)
    hours = [0] * 24
    for r in by_hour or []:
        hours[((r["hour"] * 60 + offset) % 1440) // 60] += r["n"]
    return hours


def _topics_chart(rows):
    by_day = {r["day"]: r for r in rows or []}
    days = charts.last_days()
    return charts.lines([{"name": topic, "slot": i, "x": [f"{d} 12:00" for d in days],
                          "y": [(by_day.get(d) or {}).get(topic) or 0 for d in days]}
                         for i, topic in enumerate(("tech", "crypto", "markets"))], height=180, whole=True)


@tab_required("news")
def page_view(request):
    f = _filters(request)
    groups = _groups()
    names = _names(groups)
    with Reader() as db:
        s = db.get(queries.news_summary, f["days"])
        sources = db.get(queries.news_sources, default=[])
        runs = db.get(queries.news_runs, default=[])
        data = db.get(queries.news_items, **_item_args(f)) if s else None
    context = {"f": f, "s": s, "qs": query_string({"days": f["days"], "group": f["group"]})}
    if s:
        last = s["last_run"] or {}
        label = period_label(f["days"])
        counts = {r["group_code"]: r["n"] for r in s["by_group"]}
        context.update(
            period=label, runs=runs, health=[{**r, "group_name": names.get(r["group_code"]) or r["group_code"] or "—"} for r in sources],
            kpis=kpis((num(s["totals"]["in_period"]), f"Headlines, {label}"), (num(last.get("new_headlines")), "New in last run"),
                      (num(s["totals"]["tech_in_period"]), f"Tech headlines, {label}"),
                      (f"{last.get('sources_ok') or 0}/{last.get('sources') or 0}", "Sources OK (last run)"),
                      (ago(last.get("run_at")) or "never", f"Last run {when(last.get('run_at'))}"),
                      (num(s["totals"]["headlines"]), "Headlines stored (all time)")),
            chips=[{"code": "", "name": "All", "n": sum(counts.values())}] +
                  [{"code": g["code"], "name": g["name"], "n": counts.get(g["code"], 0)} for g in groups],
            source_names=sorted((r["source"] for r in sources), key=str.lower),
            day_chart=charts.day_columns(s["by_day"], "n", "tech", "tech", "other"),
            topics_chart=_topics_chart(s["topics_by_day"]), hours_chart=charts.hour_columns(_hours(s["by_hour"])),
            by_group=bars(s["by_group"], lambda r: names.get(r["group_code"]) or r["group_code"]),
            top_sources=bars(s["top_sources"], "source"), **_items_context(f, data, names))
    return page(request, "news", db, **context)


@tab_required("news")
def items_view(request):
    """The headlines list alone, for a changed filter or the next page."""
    f = _filters(request)
    offset = int_param(request, "offset", 0, lo=0)
    with Reader() as db:
        data = db.get(queries.news_items, **_item_args(f, offset))
    context = _items_context(f, data, _names(_groups()), offset)
    return list_response(request, "insights/_news_items.html", context, context["count"],
                         url_with("insights_news", f) if offset == 0 else None)


def _tone_row(label: str, tone: dict) -> dict:
    total = tone.get("headlines") or 1
    return {"label": label, "negative": 100 * tone["negative"] / total, "neutral": 100 * tone["neutral"] / total,
            "positive": 100 * tone["positive"] / total, "tone": tone.get("label") or "",
            "title": f"{tone['negative']} negative, {tone['neutral']} neutral, {tone['positive']} positive"}


@tab_required("news")
def analysis_view(request):
    """It reads every headline of the period (a few seconds for a week), so it loads after the page, not with it."""
    days = period_param(request, 7)
    group = text_param(request, "group")
    groups = [g for g in group.upper().split(",") if g] or None
    with Reader() as db:
        a = db.get(queries.news_analysis, days, groups)
    context = {"days": days, "banner": db.error}
    if a and a.get("headlines"):
        context.update(
            a=a, label=f"{a['headlines']:,} headlines · {a['sources']} sources", stories=[
                {**s, "first_sources": ", ".join(s["sources"][:6]), "more_sources": max(0, len(s["sources"]) - 6),
                 "all_sources": ", ".join(s["sources"])} for s in (a.get("stories") or [])[:8]],
            topics=bars([t for t in a.get("topics") or [] if t["topic"] != "other"][:12],
                        lambda r: f"{TOPIC_NAME.get(r['topic'], r['topic'])} · {r['share']}%", value="headlines"),
            names=bars((a.get("people") or [])[:15], "name", value="headlines", search=True),
            countries=bars((a.get("countries") or [])[:15], "country", value="headlines", search=True),
            tones=[_tone_row(g["name"], g) for g in a["tone"].get("by_group") or []],
            by_group=[{"name": g["name"], "top": [{"name": TOPIC_NAME.get(t["topic"], t["topic"]), "n": t["headlines"]}
                                                  for t in g["top"][:4]]} for g in a.get("topics_by_group") or []],
            rising=a.get("rising") or [])
    return render(request, "insights/_news_analysis.html", context)
