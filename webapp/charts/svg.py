"""A chart's numbers as an SVG picture, for PDF reports. A PDF can't run plotly.js, and drawing here means no browser and no extra
programs on the server. Light colours only, because paper is white; the series colours are the dashboard's, in the same order."""

import math
from xml.sax.saxutils import escape, quoteattr

from .data import ChartData, show_number
from .models import Chart

SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
INK, MUTED, LINE = "#18232b", "#5b6a75", "#d7dfe4"
FONT = "Helvetica, Arial, sans-serif"
WIDTH, HEIGHT = 680, 260


def _text(x, y, text, anchor="start", size=11, color=MUTED, weight="normal", rotate=None) -> str:
    turn = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate is not None else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-size="{size}" font-family={quoteattr(FONT)} fill="{color}" '
            f'font-weight="{weight}"{turn}>{escape(str(text))}</text>')


def _short(text: str, n: int) -> str:
    text = str(text)
    return text if len(text) <= n else text[:n - 1] + "…"


def _nice_max(top: float) -> float:
    if top <= 0:
        return 1
    power = 10 ** math.floor(math.log10(top))
    for step in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if step * power >= top:
            return step * power
    return top


def _short_number(value: float) -> str:
    size = abs(value)
    for limit, letter in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if size >= limit:
            return f"{value / limit:.3g}{letter}"
    return f"{value:.3g}" if value % 1 else f"{int(value)}"


def _legend(series: list, y: float) -> str:
    parts, x = [], 0.0
    for s in series:
        name = _short(s["name"], 26)
        parts.append(f'<rect x="{x:.1f}" y="{y - 9:.1f}" width="10" height="10" rx="2" fill="{SERIES[s["slot"] % 8]}"/>')
        parts.append(_text(x + 14, y, name, color=INK))
        x += 22 + 6.2 * len(name)
        if x > WIDTH - 120:
            break
    return "".join(parts)


def _frame(body: str, height: int = HEIGHT) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" height="{height}">{body}</svg>'


def _stack_max(data: ChartData) -> float:
    return max((sum(s["values"][i] or 0 for s in data.series) for i in range(len(data.categories))), default=0)


def _grid(left: float, right: float, top: float, bottom: float, biggest: float) -> str:
    parts = []
    for i in range(5):
        y = bottom - (bottom - top) * i / 4
        parts.append(f'<line x1="{left}" x2="{right}" y1="{y:.1f}" y2="{y:.1f}" stroke="{LINE}" stroke-width="0.6"/>')
        parts.append(_text(left - 6, y + 4, _short_number(biggest * i / 4), anchor="end"))
    return "".join(parts)


def _x_labels(data: ChartData, positions: list[float], y: float) -> str:
    n = len(positions)
    step = max(1, math.ceil(n / 12))
    tilt = n > 6 and not data.is_time
    parts = []
    for i in range(0, n, step):
        label = data.categories[i][:7 if data.time_unit == "month" else 4 if data.time_unit == "year" else 10]
        if tilt:
            parts.append(_text(positions[i], y, _short(label, 14), anchor="end", rotate=-30))
        else:
            parts.append(_text(positions[i], y, _short(label, 16), anchor="middle"))
    return "".join(parts)


def _columns(data: ChartData) -> str:
    left, right, top, bottom = 52, WIDTH - 6, 26 if len(data.series) > 1 else 8, HEIGHT - 44
    biggest = _nice_max(_stack_max(data))
    n = len(data.categories)
    slot = (right - left) / n
    width = max(2, slot * 0.68)
    parts = [_legend(data.series, 12) if len(data.series) > 1 else "", _grid(left, right, top, bottom, biggest)]
    positions = []
    for i in range(n):
        x = left + slot * (i + 0.5)
        positions.append(x)
        y = bottom
        for s in data.series:
            value = s["values"][i] or 0
            h = (bottom - top) * max(value, 0) / biggest
            if h > 0:
                y -= h
                parts.append(f'<rect x="{x - width / 2:.1f}" y="{y:.1f}" width="{width:.1f}" height="{h:.1f}" fill="{SERIES[s["slot"] % 8]}" stroke="#ffffff" stroke-width="0.6"/>')
    parts.append(_x_labels(data, positions, bottom + 16))
    return _frame("".join(parts))


def _bars(data: ChartData) -> str:
    n = len(data.categories)
    row = 20
    legend = 22 if len(data.series) > 1 else 0
    height = legend + row * n + 8
    left, right = 170, WIDTH - 60
    biggest = _nice_max(_stack_max(data))
    parts = [_legend(data.series, 12) if legend else ""]
    for i, c in enumerate(data.categories):
        y = legend + row * i
        parts.append(_text(left - 6, y + 14, _short(c, 28), anchor="end", color=INK))
        x = left
        total = 0.0
        for s in data.series:
            value = s["values"][i] or 0
            total += value
            w = (right - left) * max(value, 0) / biggest
            if w > 0:
                parts.append(f'<rect x="{x:.1f}" y="{y + 4:.1f}" width="{w:.1f}" height="{row - 8}" fill="{SERIES[s["slot"] % 8]}"/>')
                x += w
        parts.append(_text(x + 5, y + 14, show_number(total)))
    return _frame("".join(parts), height)


def _lines(data: ChartData, stacked: bool) -> str:
    left, right, top, bottom = 52, WIDTH - 10, 26 if len(data.series) > 1 else 8, HEIGHT - 44
    n = len(data.categories)
    biggest = _nice_max(_stack_max(data) if stacked else max((v or 0 for s in data.series for v in s["values"]), default=0))
    xs = [left + (right - left) * (i / (n - 1) if n > 1 else 0.5) for i in range(n)]
    y_of = lambda v: bottom - (bottom - top) * max(v, 0) / biggest  # noqa: E731
    parts = [_legend(data.series, 12) if len(data.series) > 1 else "", _grid(left, right, top, bottom, biggest)]
    base = [0.0] * n
    for s in data.series:
        color = SERIES[s["slot"] % 8]
        values = [v or 0 for v in s["values"]] if stacked else s["values"]
        if stacked:
            upper = [b + v for b, v in zip(base, values)]
            shape = " ".join(f"{xs[i]:.1f},{y_of(upper[i]):.1f}" for i in range(n)) + " " + " ".join(f"{xs[i]:.1f},{y_of(base[i]):.1f}" for i in reversed(range(n)))
            parts.append(f'<polygon points="{shape}" fill="{color}" fill-opacity="0.75" stroke="#ffffff" stroke-width="0.6"/>')
            base = upper
            continue
        points = [(xs[i], y_of(v)) for i, v in enumerate(values) if v is not None]
        parts.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}" fill="none" stroke="{color}" stroke-width="2"/>')
        if n < 30:
            parts += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="{color}"/>' for x, y in points]
    parts.append(_x_labels(data, xs, bottom + 16))
    return _frame("".join(parts))


def _pie(data: ChartData) -> str:
    values = [max(v or 0, 0) for v in (data.series[0]["values"] if data.series else [])]
    total = sum(values) or 1
    cx, cy, outer, inner = 130, 125, 110, 62
    parts, angle = [], -math.pi / 2
    for i, value in enumerate(values):
        sweep = 2 * math.pi * value / total
        if sweep >= 2 * math.pi - 1e-6:                  # one slice is the whole ring: an arc can't draw that
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{(outer + inner) / 2}" fill="none" stroke="{SERIES[i % 8]}" stroke-width="{outer - inner}"/>')
        elif sweep > 0:
            end = angle + sweep
            big = 1 if sweep > math.pi else 0
            p = [(cx + outer * math.cos(angle), cy + outer * math.sin(angle)), (cx + outer * math.cos(end), cy + outer * math.sin(end)),
                 (cx + inner * math.cos(end), cy + inner * math.sin(end)), (cx + inner * math.cos(angle), cy + inner * math.sin(angle))]
            parts.append(f'<path d="M{p[0][0]:.1f} {p[0][1]:.1f} A{outer} {outer} 0 {big} 1 {p[1][0]:.1f} {p[1][1]:.1f} L{p[2][0]:.1f} {p[2][1]:.1f} '
                         f'A{inner} {inner} 0 {big} 0 {p[3][0]:.1f} {p[3][1]:.1f} Z" fill="{SERIES[i % 8]}" stroke="#ffffff" stroke-width="1.5"/>')
        angle += sweep
        y = 30 + 24 * i
        parts.append(f'<rect x="280" y="{y - 10}" width="11" height="11" rx="2" fill="{SERIES[i % 8]}"/>')
        parts.append(_text(297, y, _short(data.categories[i], 40), color=INK, size=12))
        parts.append(_text(WIDTH - 6, y, f"{show_number(value)}  ·  {100 * value / total:.0f}%", anchor="end", size=12))
    return _frame("".join(parts))


def chart_svg(data: ChartData) -> str:
    if not data.categories:
        return ""
    if data.kind == Chart.BAR:
        return _bars(data)
    if data.kind == Chart.LINE:
        return _lines(data, stacked=False)
    if data.kind == Chart.AREA:
        return _lines(data, stacked=True)
    if data.kind == Chart.PIE:
        return _pie(data)
    return _columns(data)
