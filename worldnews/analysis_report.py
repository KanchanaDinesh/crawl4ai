"""`python -m worldnews analyze`: read the saved headlines, analyse them, write the report.

Writes output/worldnews/analysis/analysis-<date>_<time>.{html,md,json} and analysis-latest.html,
and prints a short summary. Reads the database read-only, so it can run while a collection runs.
"""

import html
import json
import shutil
from datetime import datetime, timedelta, timezone

from .analysis import analyze, balance_label

E = html.escape
TOPIC_LABEL = {"crime_justice": "crime & justice", "accidents_disasters": "accidents & disasters",
               "climate_environment": "climate & environment", "science_space": "science & space"}


def topic_label(t: str) -> str:
    return TOPIC_LABEL.get(t, t)


def load_items(settings, days: float, groups: list[str] | None = None, now: datetime | None = None) -> tuple[list, list]:
    """Headlines first seen in the last `days`, and in the `days` before that (for "rising")."""
    import warehouse
    path = settings.data_dir / "worldnews.duckdb"
    if not path.exists():
        raise SystemExit("No news saved yet. Run:  uv run python -m worldnews run")
    now = now or datetime.now(timezone.utc)
    start, before = now - timedelta(days=days), now - timedelta(days=2 * days)
    con = warehouse.connect(path, read_only=True, wait_seconds=30)
    try:
        where = " and group_code in (" + ",".join("?" * len(groups)) + ")" if groups else ""
        cur = con.execute(f"""select url, title, summary, source, group_code, published_at, first_seen_at
            from news_items where first_seen_at >= ? {where} order by first_seen_at""",
                          [before, *(g.upper() for g in groups or [])])
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()
    return [r for r in rows if r["first_seen_at"] >= start], [r for r in rows if r["first_seen_at"] < start]


def run(settings, days: float | None = None, groups: list[str] | None = None) -> tuple[dict, dict]:
    config = settings.section("analysis")
    days = days or float(config.get("default_days", 1))
    items, previous = load_items(settings, days, groups)
    if not items:
        raise SystemExit(f"No headlines in the last {days:g} day(s). Run:  uv run python -m worldnews run")
    result = analyze(items, previous, config, settings.group_names)
    now = datetime.now().astimezone()
    result["period"] = {"days": days, "until": now.isoformat(timespec="minutes"),
                        "label": f"last {days:g} day{'s' if days != 1 else ''}, until {now:%d %b %Y %H:%M}",
                        "groups": groups or []}
    return result, write(result, settings, now)


def write(result: dict, settings, now: datetime) -> dict:
    folder = settings.output_dir / "analysis"
    folder.mkdir(parents=True, exist_ok=True)
    paths = {ext: folder / f"analysis-{now:%Y-%m-%d_%H%M}.{ext}" for ext in ("html", "md", "json")}
    paths["json"].write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    paths["md"].write_text(render_markdown(result), encoding="utf-8")
    paths["html"].write_text(render_html(result), encoding="utf-8")
    shutil.copyfile(paths["html"], folder / "analysis-latest.html")
    return {k: str(v) for k, v in paths.items()} | {"latest": str(folder / "analysis-latest.html")}


def summary_lines(r: dict) -> list[str]:
    """The short version, for the terminal."""
    tone = r["tone"]["all"]
    lines = [f"News analysis, {r['period']['label']}: {r['headlines']} headlines from {r['sources']} sources",
             "", "Biggest stories (same story, several sources):"]
    lines += [f"  {len(s['sources']):>2} sources  {s['headline']}" for s in r["stories"][:8]] or ["  none yet"]
    lines += ["", "Topics:  " + ", ".join(f"{topic_label(t['topic'])} {t['share']:g}%" for t in r["topics"][:8])]
    lines += ["Names:   " + ", ".join(f"{p['name']} ({p['headlines']})" for p in r["people"][:10])]
    lines += ["Countries: " + ", ".join(f"{c['country']} ({c['headlines']})" for c in r["countries"][:8])]
    if r["rising"]:
        lines += ["Rising:  " + ", ".join(f"{w['word']} ({w['before']}→{w['now']})" for w in r["rising"][:10])]
    else:
        lines += ["Rising:  no earlier period to compare with yet"]
    lines += [f"Tone:    {balance_label(tone['balance'])} ({tone['negative']} negative, {tone['positive']} positive "
              f"of {tone['headlines']} headlines; word-based, rough)"]
    lk = r["sri_lanka"]
    lines += [f"Sri Lanka: {lk['headlines']} headlines; talked about: " + ", ".join(w["word"] for w in lk["words"][:10])]
    return lines


def render_markdown(r: dict) -> str:
    out = [f"# News analysis: {r['period']['label']}", "",
           f"{r['headlines']} headlines from {r['sources']} sources. Our own counts of what the sources wrote about.", ""]
    out += ["## Biggest stories", "Stories told by several sources (headlines that share their key words).", ""]
    for s in r["stories"][:15]:
        out.append(f"- **{s['headline']}** · {len(s['sources'])} sources: {', '.join(s['sources'][:8])}")
    out += ["", "## Topics", "", "| Topic | Headlines | Share | Tone |", "|---|---:|---:|---|"]
    out += [f"| {topic_label(t['topic'])} | {t['headlines']} | {t['share']:g}% | {balance_label(t['tone']['balance'])} |"
            for t in r["topics"]]
    out += ["", "## What each country's news is about", ""]
    out += [f"- **{g['name']}** ({g['headlines']}): " + ", ".join(f"{topic_label(t['topic'])} {t['headlines']}" for t in g["top"])
            for g in r["topics_by_group"]]
    out += ["", "## Names and places in the news", "", ", ".join(f"{p['name']} ({p['headlines']})" for p in r["people"]),
            "", "## Countries mentioned", "", ", ".join(f"{c['country']} ({c['headlines']})" for c in r["countries"])]
    out += ["", "## Rising words", ""]
    out += [", ".join(f"{w['word']} ({w['before']} → {w['now']})" for w in r["rising"])
            or "No earlier period to compare with yet."]
    out += ["", "## Tone (word-based, rough)", "", "| Group | Negative | Positive | Overall |", "|---|---:|---:|---|"]
    out += [f"| {g['name']} | {g['negative']} | {g['positive']} | {balance_label(g['balance'])} |" for g in r["tone"]["by_group"]]
    lk = r["sri_lanka"]
    out += ["", "## Sri Lanka", "", f"{lk['headlines']} headlines ({lk['from_lk_sources']} from Sri Lankan sources).",
            "Talked about: " + ", ".join(f"{w['word']} ({w['headlines']})" for w in lk["words"]),
            "Names: " + ", ".join(f"{p['name']} ({p['headlines']})" for p in lk["people"])]
    out += [f"- **{s['headline']}** · {', '.join(s['sources'])}" for s in lk["stories"]]
    return "\n".join(out) + "\n"


CSS = """
:root{--bg:#f6f7f9;--panel:#fff;--ink:#1d2330;--muted:#677084;--line:#e3e6ec;--accent:#2f6fde;--neg:#c2410c;--pos:#15803d}
@media (prefers-color-scheme:dark){:root{--bg:#11141a;--panel:#191d25;--ink:#e6e9ef;--muted:#98a1b3;--line:#2a303b;--accent:#6ea0ff;--neg:#fb923c;--pos:#4ade80}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1200px;margin:0 auto;padding:20px 16px 60px}h1{font-size:24px;margin:0 0 4px}.muted{color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px;margin-top:14px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.panel h2{font-size:15px;margin:0 0 10px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.wide{grid-column:1/-1}.bar{display:grid;grid-template-columns:170px 1fr 52px;gap:8px;align-items:center;margin:3px 0;font-size:14px}
.bar i{display:block;height:10px;border-radius:5px;background:var(--accent)}.bar b{text-align:right;font-weight:600}
.story{padding:8px 0;border-bottom:1px solid var(--line)}.story:last-child{border:0}.story .n{display:inline-block;margin-right:8px;
font-weight:700;color:var(--accent)}details summary{cursor:pointer;color:var(--muted);font-size:13px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}.tag{display:inline-block;border:1px solid var(--line);
border-radius:999px;padding:0 9px;margin:2px 4px 2px 0;font-size:13px}.tone{display:flex;height:10px;border-radius:5px;overflow:hidden;
background:var(--line)}.tone .neg{background:var(--neg)}.tone .pos{background:var(--pos)}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:4px 6px;border-bottom:1px solid var(--line);text-align:left}
"""


def _link(url, label):
    return f"<a href='{E(url or '')}' target='_blank' rel='noopener noreferrer'>{E(label or '')}</a>" if url else E(label or "")


def _bars(rows, label_key, value_key, fmt=lambda r: "", label=lambda v: v):
    top = max([r[value_key] for r in rows] or [1]) or 1
    return "".join(f"<div class='bar'><span>{E(str(label(r[label_key])))}{fmt(r)}</span><i style='width:{r[value_key] / top * 100:.0f}%'>"
                   f"</i><b>{r[value_key]}</b></div>" for r in rows)


def _tone_bar(t):
    n = t["headlines"] or 1
    return (f"<div class='tone' title='{t['negative']} negative, {t['positive']} positive, {t['neutral']} neutral'>"
            f"<span class='neg' style='width:{t['negative'] / n * 100:.0f}%'></span>"
            f"<span style='width:{t['neutral'] / n * 100:.0f}%'></span><span class='pos' style='width:{t['positive'] / n * 100:.0f}%'></span></div>")


def render_html(r: dict) -> str:
    stories = "".join(
        f"<div class='story'><span class='n'>{len(s['sources'])} sources</span>{_link(s['url'], s['headline'])}"
        f"<div class='muted' style='font-size:13px'>{E(', '.join(s['sources'][:10]))}</div>"
        f"<details><summary>{len(s['headlines'])} of its {s['count']} headlines</summary>"
        + "".join(f"<div>{_link(h['url'], h['title'])} <span class='muted'>· {E(h['source'] or '')}</span></div>" for h in s["headlines"])
        + "</details></div>" for s in r["stories"][:20]) or "<div class='muted'>No story was told by several sources yet.</div>"
    topics = _bars(r["topics"], "topic", "headlines", label=topic_label)
    by_group = "<table><tr><th>News from</th><th>Headlines</th><th>Mostly about</th></tr>" + "".join(
        f"<tr><td>{E(g['name'])}</td><td>{g['headlines']}</td><td>"
        + ", ".join(f"{E(topic_label(t['topic']))} {t['headlines']}" for t in g["top"]) + "</td></tr>"
        for g in r["topics_by_group"]) + "</table>"
    tone = "<table><tr><th>News from</th><th style='width:45%'>Negative · neutral · positive</th><th>Overall</th></tr>" + "".join(
        f"<tr><td>{E(g['name'])}</td><td>{_tone_bar(g)}</td><td>{balance_label(g['balance'])}</td></tr>"
        for g in r["tone"]["by_group"]) + "</table>"
    rising = "".join(f"<span class='tag'>{E(w['word'])} <span class='muted'>{w['before']}→{w['now']}</span></span>"
                     for w in r["rising"]) or "<span class='muted'>No earlier period to compare with yet: try again after another day of collecting.</span>"
    lk = r["sri_lanka"]
    lk_html = (f"<p>{lk['headlines']} headlines, {lk['from_lk_sources']} of them from Sri Lankan sources.</p>"
               "<p>" + "".join(f"<span class='tag'>{E(w['word'])} <span class='muted'>{w['headlines']}</span></span>" for w in lk["words"]) + "</p>"
               + ("<p>" + ", ".join(f"{E(p['name'])} ({p['headlines']})" for p in lk["people"]) + "</p>" if lk["people"] else "")
               + "".join(f"<div class='story'><span class='n'>{len(s['sources'])} sources</span>{_link(s['url'], s['headline'])}</div>"
                         for s in lk["stories"]))
    hours = r["activity"]["by_hour"]
    top_hour = max([h["headlines"] for h in hours] or [1]) or 1
    hour_html = ("<div style='display:flex;align-items:flex-end;gap:3px;height:90px'>" + "".join(
        f"<div title='{h['hour']:02d}:00 · {h['headlines']}' style='flex:1;background:var(--accent);opacity:.8;border-radius:3px 3px 0 0;"
        f"height:{max(2, h['headlines'] / top_hour * 100):.0f}%'></div>" for h in hours)
        + "</div><div class='muted' style='display:flex;justify-content:space-between;font-size:12px'><span>00</span><span>06</span>"
        "<span>12</span><span>18</span><span>23</span></div>")
    t = r["tone"]["all"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>News analysis</title><style>{CSS}</style></head><body><main>
<h1>News analysis</h1><div class="muted">{E(r['period']['label'])} · {r['headlines']} headlines from {r['sources']} sources ·
our own counts of what the sources wrote about</div>
<div class="grid">
<div class="panel wide"><h2>Biggest stories <span class="muted">· the same story from several sources</span></h2>{stories}</div>
<div class="panel"><h2>Topics</h2>{topics}</div>
<div class="panel"><h2>What each country's news is about</h2>{by_group}</div>
<div class="panel"><h2>Names and places in the news</h2>{_bars(r['people'][:20], 'name', 'headlines')}</div>
<div class="panel"><h2>Countries mentioned</h2>{_bars(r['countries'][:20], 'country', 'headlines')}</div>
<div class="panel"><h2>Rising words <span class="muted">· before → now</span></h2>{rising}</div>
<div class="panel"><h2>Tone <span class="muted">· word-based, rough</span></h2><p>Overall: <b>{balance_label(t['balance'])}</b>
({t['negative']} negative, {t['positive']} positive, {t['neutral']} neutral)</p>{tone}</div>
<div class="panel"><h2>Sri Lanka</h2>{lk_html}</div>
<div class="panel"><h2>Publishing hour <span class="muted">· your local time</span></h2>{hour_html}</div>
<div class="panel"><h2>Busiest sources</h2>{_bars(r['activity']['by_source'], 'source', 'headlines')}</div>
</div></main></body></html>"""
