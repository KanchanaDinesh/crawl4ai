"""The day's text summary (Morning Brief style), filled from the facts with fixed sentences.

Rule-based on purpose: every number comes from facts.py, the wording never changes the numbers,
and there is no advice ("buy", "sell", "will rise"). An AI writer could later rephrase the same facts.
"""

CATEGORY = {"devtools": "developer tools", "ai_ml": "AI / ML", "saas_business": "business SaaS",
            "data_analytics": "data", "cloud_infra": "cloud", "cybersecurity": "security"}


def updown(v: float) -> str:
    return f"{'up' if v >= 0 else 'down'} {abs(v):.2f}%"


def price_text(sym: str, q: dict) -> str:
    p = q["price"]
    value = f"Rs {p:,.0f}" if sym == "GOLD24K_LKR_PAWN" else f"{p:,.2f}"
    if q["held_back"]:
        return f"{q['label']}: {value} (day change held back: unusual, waiting for a second source)"
    if q["change_pct"] is None:
        return f"{q['label']}: {value}"
    return f"{q['label']}: {value} ({updown(q['change_pct'])})"


def millions(v: float) -> str:
    return f"Rs {v / 1e6:.2f}M"


def build(facts: dict) -> str:
    lines = [f"Morning Brief · {facts['date_label']}", ""]
    m = facts.get("markets")
    if m:
        lines.append("MARKETS")
        lines += [price_text(sym, q) for sym, q in m.items()]
        three = [f"{q['label']} {q['change_3m']:+.1f}%" for sym, q in m.items()
                 if sym in ("^GSPC", "GC=F") and q.get("change_3m") is not None]
        if three:
            lines.append("3 months: " + ", ".join(three))
        lines.append("")
    n = facts.get("news")
    if n:
        lines += ["NEWS", f"{n['week_headlines']:,} headlines from {n['week_sources']} sources this week.", ""]
    t = facts.get("tech")
    if t and t["launches"]:
        cats = ", ".join(f"{CATEGORY.get(c, c)} {k}" for c, k in t["launches_by_category"][:4])
        lines += ["TECH", f"{t['launches']} new tech products on domains under 7 days old ({cats}).", ""]
    v = facts.get("vehicles")
    if v and v["makes"]:
        makes = ", ".join(f"{name} {millions(p)} ({k} ads)" for name, k, p in v["makes"] if p)
        lines += ["VEHICLES", f"{v['ads']} ads tracked. Median asking price: {makes}.", ""]
    lines.append("Information only, not financial advice.")
    return "\n".join(lines) + "\n"
