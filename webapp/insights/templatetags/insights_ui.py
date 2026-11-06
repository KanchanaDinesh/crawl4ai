"""How numbers, times and small marks look on the dashboard tabs. Times are saved in UTC and shown in local time."""

import json
from datetime import datetime
from numbers import Number
from urllib.parse import urlencode

from django import template
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join, json_script
from django.utils.safestring import mark_safe

register = template.Library()

SOCIAL = {"linkedin": ("in", "LinkedIn"), "facebook": ("f", "Facebook"), "x": ("X", "X (Twitter)"),
          "instagram": ("ig", "Instagram"), "youtube": ("yt", "YouTube"), "github": ("gh", "GitHub"), "tiktok": ("tt", "TikTok")}
PILL_TONE = {"qualified": "ok", "ok": "ok", "read": "ok", "rejected": "bad", "failed": "bad", "watch": "warn",
             "interrupted": "warn", "unreachable": "warn", "robots.txt: no": "warn"}


def _moment(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _number(value):
    """A template passes "" for something missing (an instrument with no quote yet): show that as no number."""
    return value if isinstance(value, Number) and not isinstance(value, bool) else None


def _rounded(x: float) -> int:
    return int(x + 0.5)


@register.filter
def when(value) -> str:
    moment = _moment(value)
    if moment is None:
        return ""
    return (timezone.localtime(moment) if timezone.is_aware(moment) else moment).strftime("%Y-%m-%d %H:%M")


@register.filter
def day(value) -> str:
    return when(value)[:10]


@register.filter
def ago(value) -> str:
    moment = _moment(value)
    if moment is None:
        return ""
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    minutes = max(0, _rounded((timezone.now() - moment).total_seconds() / 60))
    if minutes < 60:
        return f"{minutes}m ago"
    if minutes < 48 * 60:
        return f"{_rounded(minutes / 60)}h ago"
    return f"{_rounded(minutes / 1440)}d ago"


@register.filter
def safe_url(value) -> str:
    """Links here come from collected web pages, so only plain web addresses become links (never javascript:)."""
    text = str(value or "").strip()
    return text if text.lower().startswith(("http://", "https://")) else ""


@register.filter
def num(value) -> str:
    return f"{_number(value) or 0:,}"


def format_price(value, asset_class: str = "") -> str:
    value = _number(value)
    if value is None:
        return "–"
    size = abs(value)
    if 0 < size < 1:
        return f"{float(f'{value:.4g}')}"            # small coins: 4 significant digits
    decimals = 4 if asset_class == "fx" and size < 20 else 2     # EUR/USD 1.0832; everything else 2
    return f"{value:,.{decimals}f}"


@register.filter
def price(value, asset_class="") -> str:
    return format_price(value, asset_class)


@register.filter
def big(value) -> str:
    value = _number(value)
    if value is None:
        return "–"
    for size, letter in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= size:
            return f"{value / size:.2f}{letter}"
    return f"{value:,.0f}"


@register.filter
def lkr(value) -> str:
    value = _number(value)
    return "–" if value is None else f"Rs {_rounded(value):,}"


@register.filter
def pct(value):
    """+1.23% in green or -1.23% in red; a dash when there's no number."""
    value = _number(value)
    if value is None:
        return mark_safe('<span class="muted">–</span>')
    tone = "up" if value > 0 else "down" if value < 0 else ""
    return format_html('<span class="{}">{}{}%</span>', tone, "+" if value > 0 else "", f"{value:.2f}")


@register.filter
def pct_cell(value):
    """A % table cell tinted green or red by its size."""
    value = _number(value)
    if value is None:
        return mark_safe('<td class="pc muted">–</td>')
    strength = min(45, abs(value) * 7)
    return format_html('<td class="pc" style="background:color-mix(in srgb, var(--{}) {}%, transparent)">{}{}%</td>',
                       "ok" if value >= 0 else "bad", f"{strength:.1f}", "+" if value > 0 else "", f"{value:.2f}")


@register.filter
def pill(value):
    tone = PILL_TONE.get(str(value), "")
    return format_html('<span class="pill {}">{}</span>', tone, value)


def _as_dict(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


@register.filter
def social_links(value):
    """The small in / f / X / gh links of a company's accounts. LinkedIn and Facebook are links only: never fetched."""
    social = _as_dict(value)
    return format_html_join("", '<a class="soc" href="{}" target="_blank" rel="noopener noreferrer" title="{}">{}</a>',
                            ((safe_url(social[k]), name, short) for k, (short, name) in SOCIAL.items() if safe_url(social.get(k))))


@register.filter
def social_name(value) -> str:
    return SOCIAL.get(value, (value, value))[1]


@register.filter
def domain_age(value):
    """"3 days", "5 weeks", "2 years" since a domain was registered, with a NEW badge under 30 days."""
    moment = _moment(value)
    if moment is None:
        return mark_safe('<span class="muted">–</span>')
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    days = (timezone.now() - moment).total_seconds() / 86400
    text = ("today" if days < 1 else f"{_rounded(days)} days" if days < 14 else f"{_rounded(days / 7)} weeks" if days < 60
            else f"{_rounded(days / 30)} months" if days < 730 else f"{_rounded(days / 365)} years")
    badge = mark_safe('<span class="badge young">NEW</span>') if days < 30 else ""
    return format_html("{}{}", badge, text)


@register.filter
def spark(values):
    """A small price line. Plain SVG: a table can have dozens of them, too many for chart objects."""
    points = [v for v in (values or []) if v is not None]
    if len(points) < 2:
        return ""
    low, high = min(points), max(points)
    span = (high - low) or 1
    line = " ".join(f"{i / (len(points) - 1) * 100:.1f},{30 - (v - low) / span * 28:.1f}" for i, v in enumerate(points))
    trend = "up" if points[-1] > points[0] else "down" if points[-1] < points[0] else "flat"
    return format_html('<svg class="spark {}" viewBox="0 0 100 32" preserveAspectRatio="none" aria-hidden="true">'
                       '<polyline points="{}" vector-effect="non-scaling-stroke"/></svg>', trend, line)


@register.simple_tag
def outlink(url, label=None):
    """A link that opens in a new tab; plain text if the address isn't a web address."""
    href = safe_url(url)
    text = label if label not in (None, "") else str(url or "").rstrip("/")
    return format_html('<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>', href, text) if href else text


@register.simple_tag
def chart(figure, label=""):
    """A Plotly figure from charts.py; static/js/insights.js draws it. A figure with too little data says so."""
    if not figure:
        return ""
    if figure.get("empty"):
        return format_html('<div class="muted small">{}</div>', figure["empty"])
    return format_html('<div class="plot" role="img" aria-label="{}">{}</div>', label, json_script(figure))


@register.simple_tag
def symbol_url(row, days=None):
    params = {"class": row["asset_class"], "symbol": row["symbol"]}
    if days:
        params["days"] = days
    return f"{reverse('insights_symbol')}?{urlencode(params)}"


@register.filter
def short_symbol(row) -> str:
    return row["symbol"].split(".")[0] if row["asset_class"] == "stock_lk" else row["symbol"]


@register.simple_tag
def quote_cell(row, key):
    """One cell of the prices table, per column kind."""
    value = row.get(key)
    cls = row.get("asset_class", "")
    if key == "name" and cls == "crypto":
        image = format_html('<img src="{}" alt="" referrerpolicy="no-referrer">', safe_url(row["image"])) if safe_url(row.get("image")) else ""
        return format_html('<td class="coin">{}{} <span class="muted">{}</span></td>', image, row.get("name") or "", row.get("symbol"))
    if key in ("change_pct", "pct_1h", "pct_7d"):
        return format_html('<td class="num">{}</td>', pct(value))
    if key == "change":
        tone = "up" if (value or 0) > 0 else "down" if (value or 0) < 0 else ""
        text = "–" if value is None else ("+" if value > 0 else "") + format_price(value, cls)
        return format_html('<td class="num {}">{}</td>', tone, text)
    if key == "price":
        return format_html('<td class="num"><b>{}</b></td>', format_price(value, cls))
    if key in ("volume", "market_cap", "turnover"):
        return format_html('<td class="num">{}</td>', big(value))
    if key == "as_of":
        return format_html('<td class="nowrap muted small">{}</td>', when(value))
    if key == "rank":
        return format_html('<td class="num muted">{}</td>', "" if value is None else value)
    if key == "symbol":
        return format_html('<td class="nowrap"><b>{}</b></td>', value or "")
    if key in ("stoch_rsi", "pct_k", "pct_d"):
        return _stoch_cell(row, key)
    if key == "last_day":
        if not value:
            return format_html('<td class="nowrap muted small">{}</td>', "–")
        short = value[5:] if value[:4] == str(timezone.localdate().year) else value      # the table is tight for room
        return format_html('<td class="nowrap muted small" title="{}">{}</td>', value, short)
    return format_html("<td>{}</td>", "" if value is None else value)


def _stoch_cell(row, key):
    """With no number at all, one cell across the three columns says why. Never a made-up number."""
    numbers = (row.get("stoch_rsi"), row.get("pct_k"), row.get("pct_d"))
    if all(n is None for n in numbers):
        if key != "stoch_rsi":
            return ""
        return format_html('<td class="muted small" colspan="3">{}</td>', row.get("message") or "")
    value = row.get(key)
    text = "–" if value is None else f"{value:.1f}"
    if key == "pct_k" and row.get("zone") in ("overbought", "oversold"):
        return format_html('<td class="num nowrap"><span class="badge {0}">{0}</span>{1}</td>', row["zone"], text)
    return format_html('<td class="num">{}</td>', text)


@register.filter
def link_or_text(value):
    text = str(value)
    if text.startswith(("http://", "https://")):
        return format_html('<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>', text, text.rstrip("/"))
    return text
