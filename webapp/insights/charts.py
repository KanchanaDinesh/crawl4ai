"""Plotly figures for the dashboard tabs, as plain dicts: that's all plotly.js needs, and it keeps the plotly Python
package (large, and slow to import) off the server until the chart builder of phase 6 needs it.

Colours are written "token:<name>"; static/js/insights.js fills in the app's CSS variable just before drawing, so the
same figure follows the light and dark themes. Series colours come from --series-1..8 in fixed order and were checked
with the dataviz palette validator, for both themes (colour-blind separation included).
"""

from datetime import datetime, timedelta

from django.utils import timezone

GAP = {"color": "token:surface", "width": 1}     # a thin line in the panel's colour keeps stacked parts apart


def local_time(iso: str) -> str:
    """"2026-11-02T08:15:00Z" as Sri Lanka time for an axis: Plotly draws date strings as they are."""
    moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return timezone.localtime(moment).strftime("%Y-%m-%d %H:%M:%S")


def last_days(n: int = 14) -> list[str]:
    today = timezone.localdate()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def last_moment(days: int) -> str:
    """The start of the last `days` days, in the same form as the saved times, so the two compare as text."""
    return (timezone.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def series_color(slot: int) -> str:
    return f"token:series-{slot % 8 + 1}"


def _layout(height: int, legend: bool) -> dict:
    axis = {"gridcolor": "token:line", "linecolor": "token:line", "zerolinecolor": "token:muted", "automargin": True,
            "tickfont": {"size": 11, "color": "token:muted"}}
    layout = {"height": height, "margin": {"l": 8, "r": 8, "t": 28 if legend else 8, "b": 8},
              "paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)",
              "font": {"color": "token:fg", "size": 12}, "hovermode": "x unified", "showlegend": legend,
              "hoverlabel": {"bgcolor": "token:surface", "bordercolor": "token:line", "font": {"color": "token:fg"}},
              "xaxis": {**axis, "showgrid": False, "spikecolor": "token:muted", "spikethickness": 1},
              "yaxis": {**axis, "rangemode": "tozero"}}
    if legend:
        layout["legend"] = {"orientation": "h", "x": 0, "y": 1, "yanchor": "bottom", "font": {"size": 12}}
    return layout


def day_columns(rows, total: str, part: str | None = None, part_label: str = "", rest_label: str = "",
                height: int = 150) -> dict:
    """Columns for the last 14 days; `part` of each day's `total` is stacked at the bottom in its own colour."""
    days = last_days()
    by_day = {r["day"]: r for r in rows or []}
    totals = [(by_day.get(d) or {}).get(total) or 0 for d in days]
    traces = []
    if part:
        parts = [(by_day.get(d) or {}).get(part) or 0 for d in days]
        traces.append({"type": "bar", "x": days, "y": parts, "name": part_label,
                       "marker": {"color": series_color(1), "line": GAP}})
        totals = [t - p for t, p in zip(totals, parts)]
    traces.append({"type": "bar", "x": days, "y": totals, "name": rest_label or "count",
                   "marker": {"color": series_color(0), "line": GAP}})
    layout = _layout(height, legend=bool(part))
    layout.update(barmode="stack", bargap=0.3, barcornerradius=3)
    layout["xaxis"].update(type="date", tickformat="%b %e", fixedrange=True, dtick=86400000 * 3)
    layout["yaxis"].update(fixedrange=True, tickformat=",d", nticks=4)
    return {"data": traces, "layout": layout}


def hour_columns(hours: list[int], height: int = 150) -> dict:
    """How many headlines came out in each hour of the day."""
    trace = {"type": "bar", "x": [f"{h:02d}:00" for h in range(24)], "y": hours, "name": "headlines",
             "marker": {"color": series_color(0), "line": GAP}}
    layout = _layout(height, legend=False)
    layout.update(bargap=0.25, barcornerradius=3)
    layout["xaxis"].update(type="category", tickmode="array", fixedrange=True,
                           tickvals=[f"{h:02d}:00" for h in range(0, 24, 3)], ticktext=[str(h) for h in range(0, 24, 3)])
    layout["yaxis"].update(fixedrange=True, tickformat=",d", nticks=4)
    return {"data": [trace], "layout": layout}


def lines(series: list[dict], height: int = 260, percent: bool = False, whole: bool = False, value_format: str | None = None,
          empty: str = "Not enough history yet: each run adds a point.") -> dict:
    """series: [{"name", "slot", "x": [...], "y": [...]}]. A line needs 2 points; with none, the figure says why."""
    series = [s for s in series if s["x"]]
    if not any(len(s["x"]) >= 2 for s in series):
        return {"empty": empty}
    traces = [{"type": "scatter", "mode": "lines" if len(s["x"]) >= 2 else "markers", "x": s["x"], "y": s["y"],
               "name": s["name"], "line": {"color": series_color(s["slot"]), "width": 2},
               "marker": {"color": series_color(s["slot"]), "size": 8}} for s in series]
    layout = _layout(height, legend=len(series) > 1)
    layout["xaxis"].update(type="date", showspikes=True, spikemode="across", spikesnap="cursor", spikedash="solid")
    if percent:
        layout["yaxis"].update(ticksuffix="%", tickformat="+.1f", hoverformat="+.2f", rangemode="normal", zeroline=True)
    elif whole:
        layout["yaxis"].update(tickformat=",d", hoverformat=",d", nticks=5)
    else:
        layout["yaxis"].update(rangemode="normal", tickformat=",.5~r", hoverformat=value_format or ",.2f")
    return {"data": traces, "layout": layout}


def stack_parts(parts: list[dict]) -> list[dict]:
    """One bar split into parts, drawn in HTML: [{"label", "n", "tone"}] plus each part's share."""
    total = sum(p["n"] for p in parts) or 1
    return [{**p, "share": round(100 * p["n"] / total, 1), "pct": int(100 * p["n"] / total + 0.5)} for p in parts]
