"""A chart's numbers as a Plotly figure, for the screen. The colours and axes are the dashboard's (insights/charts.py), so they follow the theme."""

from webapp.insights.charts import base_layout, series_color

from .data import ChartData
from .models import Chart

GAP = {"color": "token:surface", "width": 1}
HEIGHT = 340
TIME_FORMAT = {"day": "%b %e", "week": "%b %e", "month": "%b %Y", "year": "%Y"}


def _xaxis(layout: dict, data: ChartData) -> None:
    if data.is_time:
        layout["xaxis"].update(type="date", tickformat=TIME_FORMAT.get(data.time_unit, "%b %Y"))
    else:
        layout["xaxis"].update(type="category")


def _values_axis(axis: dict) -> None:
    axis.update(exponentformat="B", hoverformat=",.6~r", rangemode="tozero")     # 1.2B, not d3's 1.2G


def _columns(data: ChartData) -> dict:
    layout = base_layout(HEIGHT, legend=len(data.series) > 1)
    layout.update(barmode="stack", bargap=0.3, barcornerradius=3)
    _xaxis(layout, data)
    _values_axis(layout["yaxis"])
    traces = [{"type": "bar", "x": data.categories, "y": s["values"], "name": s["name"], "marker": {"color": series_color(s["slot"]), "line": GAP}} for s in data.series]
    return {"data": traces, "layout": layout}


def _bars(data: ChartData) -> dict:
    """Longest at the top; a list of names reads better across than up."""
    layout = base_layout(max(HEIGHT, 26 * len(data.categories) + 60), legend=len(data.series) > 1)
    layout.update(barmode="stack", bargap=0.3, barcornerradius=3, hovermode="y unified")
    layout["yaxis"].update(type="category", autorange="reversed", rangemode="normal", showgrid=False)
    layout["xaxis"].update(showgrid=True, exponentformat="B", hoverformat=",.6~r", rangemode="tozero")
    traces = [{"type": "bar", "orientation": "h", "y": data.categories, "x": s["values"], "name": s["name"], "marker": {"color": series_color(s["slot"]), "line": GAP}} for s in data.series]
    return {"data": traces, "layout": layout}


def _lines(data: ChartData, stacked: bool) -> dict:
    layout = base_layout(HEIGHT, legend=len(data.series) > 1)
    _xaxis(layout, data)
    _values_axis(layout["yaxis"])
    traces = []
    for s in data.series:
        trace = {"type": "scatter", "mode": "lines+markers" if len(data.categories) < 30 else "lines", "x": data.categories, "y": s["values"], "name": s["name"],
                 "line": {"color": series_color(s["slot"]), "width": 2}, "marker": {"color": series_color(s["slot"]), "size": 6}, "connectgaps": False}
        if stacked:
            trace.update(stackgroup="one", fillcolor=series_color(s["slot"]))
        traces.append(trace)
    return {"data": traces, "layout": layout}


def _pie(data: ChartData) -> dict:
    layout = base_layout(HEIGHT, legend=True)
    layout["legend"] = {"orientation": "v", "font": {"size": 12}}
    layout["margin"] = {"l": 8, "r": 8, "t": 8, "b": 8}
    values = data.series[0]["values"] if data.series else []
    colors = [series_color(i) for i in range(len(data.categories))]
    trace = {"type": "pie", "labels": data.categories, "values": values, "hole": 0.55, "sort": False, "textinfo": "percent", "textfont": {"color": "#ffffff"},
             "marker": {"colors": colors, "line": {"color": "token:surface", "width": 2}}, "hovertemplate": "%{label}: %{value:,.6~r} (%{percent})<extra></extra>"}
    return {"data": [trace], "layout": layout}


def figure(data: ChartData) -> dict:
    if not data.categories:
        return {"empty": "No rows match, so there's nothing to draw."}
    if data.kind == Chart.BAR:
        return _bars(data)
    if data.kind == Chart.LINE:
        return _lines(data, stacked=False)
    if data.kind == Chart.AREA:
        return _lines(data, stacked=True)
    if data.kind == Chart.PIE:
        return _pie(data)
    return _columns(data)
