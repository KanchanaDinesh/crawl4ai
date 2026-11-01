"""Stage 6: REPORT: render the analysis as Markdown and a standalone HTML page.

Writes output/techintel/reports/report-<date>.md and .html.
"""

import html
import logging
from datetime import datetime, timezone

from .analyze import analyze

logger = logging.getLogger(__name__)

CHANGE_LABELS = {
    "site_down": "went offline", "site_up": "came back online", "page_added": "added page",
    "page_removed": "removed page", "tech_added": "started using", "tech_removed": "stopped using",
    "signal_on": "now has", "signal_off": "no longer has", "job_links_changed": "job links",
    "name_changed": "renamed", "description_changed": "new description", "content_changed": "updated page",
}


def _change_text(c: dict) -> str:
    label = CHANGE_LABELS.get(c["change_type"], c["change_type"])
    if c["change_type"] in ("tech_added", "page_added"):
        return f"{label} {c['field'] if c['change_type'] == 'page_added' else c['after']}"
    if c["change_type"] == "tech_removed":
        return f"{label} {c['before']}"
    if c["change_type"] in ("signal_on", "signal_off", "page_removed"):
        return f"{label} {c['field'].replace('_', ' ')}"
    if c["change_type"] == "job_links_changed":
        return f"{label}: {c['before']} → {c['after']}"
    return label


def render_markdown(a: dict) -> str:
    t, p = a["totals"], a["period"]
    lines = [
        f"# Tech Intelligence Report: last {a['period_days']} days",
        f"_Generated {a['generated_at']} (period from {a['period_start']})_",
        "",
        "## Summary",
        f"- **{p['new_candidates']}** new domains discovered, **{p['new_qualified']}** qualified as tech sites",
        f"- **{p['changes']}** changes detected on monitored sites",
        f"- All time: {t['candidates']} domains discovered, {t['checked']} checked, "
        f"{t['qualified']} qualified ({t['qualification_rate_pct']}%), {t['watch']} on watch, "
        f"{t['rejected']} rejected, {t['profiled']} profiled",
    ]
    if a["warnings"]:
        lines += ["", "## ⚠️ Pipeline warnings", *[f"- {w}" for w in a["warnings"]]]

    def table(title, data: dict, col="Count"):
        if not data:
            return []
        return ["", f"## {title}", "", f"| | {col} |", "|---|---:|", *[f"| {k} | {v} |" for k, v in data.items()]]

    lines += table("New qualified sites by category", p["new_qualified_by_category"], "Sites")
    lines += table("Discovered by source", p["candidates_by_source"], "Domains")
    lines += table("Discovered by TLD", p["candidates_by_tld"], "Domains")

    if a["new_sites"]:
        lines += ["", "## New tech sites"]
        for s in a["new_sites"]:
            lines += ["", f"### [{s['name']}]({s['url']}) · `{s['category']}` · score {s['score']}",
                      s["description"] or "_No description._"]
            meta = []
            if s["tech_stack"]:
                meta.append("**Tech:** " + ", ".join(s["tech_stack"]))
            if s["signals"]:
                meta.append("**Signals:** " + ", ".join(x.replace("_", " ") for x in s["signals"]))
            if s["keywords"]:
                meta.append("**Keywords:** " + ", ".join(s["keywords"]))
            src = s["source"] + (f" ({s['source_detail'].get('title')})" if s["source_detail"].get("title") else "")
            meta.append(f"**Found via:** {src} on {(s['discovered_at'] or '')[:10]}")
            lines += [f"- {m}" for m in meta]

    if p["top_keywords_new_sites"]:
        lines += ["", "## Trending keywords on new sites", ", ".join(p["top_keywords_new_sites"])]
    if a["tech_stack"]:
        lines += ["", "## Tech stack across profiled sites", "", "| Technology | Type | Sites | % |", "|---|---|---:|---:|",
                  *[f"| {x['name']} | {x['group']} | {x['sites']} | {x['pct']}% |" for x in a["tech_stack"]]]
    if a["signals"]:
        lines += table("Business signals (profiled sites)",
                       {k.replace("_", " "): f"{v['sites']} ({v['pct']}%)" for k, v in a["signals"].items()}, "Sites")
    if a["notable_changes"]:
        lines += ["", "## Notable changes", *[f"- **{c['domain']}** {_change_text(c)} ({c['detected_at'][:10]})"
                                              for c in a["notable_changes"]]]
    lines += table("Why sites were not qualified", a["not_qualified_reasons"], "Sites")
    return "\n".join(lines) + "\n"


CSS = """
:root{--bg:#fff;--fg:#1d2433;--muted:#5d6678;--card:#f5f7fa;--line:#e3e7ee;--accent:#2f6fde;--warn:#fff4e5}
@media (prefers-color-scheme:dark){:root{--bg:#12151c;--fg:#e6e9ef;--muted:#9aa3b2;--card:#1b2029;--line:#2a313d;--accent:#6ea0ff;--warn:#3a2d16}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:32px 0 12px;border-bottom:1px solid var(--line);padding-bottom:6px}
.muted{color:var(--muted)}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:16px}
.kpi{background:var(--card);border-radius:10px;padding:12px 14px}.kpi b{display:block;font-size:24px}
.bar{display:grid;grid-template-columns:minmax(110px,180px) 1fr 50px;gap:10px;align-items:center;margin:4px 0}
.bar i{display:block;height:12px;border-radius:6px;background:var(--accent)}
.site{background:var(--card);border-radius:10px;padding:14px 16px;margin:10px 0}.site h3{margin:0 0 4px;font-size:16px}
.tag{display:inline-block;background:var(--line);border-radius:999px;padding:1px 9px;margin:2px 4px 2px 0;font-size:12px}
.warn{background:var(--warn);border-radius:10px;padding:10px 14px}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left}
a{color:var(--accent)}
"""


def _bars(data: dict) -> str:
    if not data:
        return "<p class='muted'>No data yet.</p>"
    top = max(data.values()) or 1
    return "".join(f"<div class='bar'><span>{html.escape(str(k))}</span><i style='width:{100 * v / top:.0f}%'></i>"
                   f"<span>{v}</span></div>" for k, v in data.items())


def render_html(a: dict) -> str:
    e = html.escape
    t, p = a["totals"], a["period"]
    kpis = [("New domains", p["new_candidates"]), ("New tech sites", p["new_qualified"]), ("Changes", p["changes"]),
            ("Qualified (all time)", t["qualified"]), ("Profiled", t["profiled"]), ("Qualify rate", f"{t['qualification_rate_pct']}%")]
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<title>Tech Intelligence Report</title><style>{CSS}</style></head><body><main>",
           f"<h1>Tech Intelligence Report</h1><div class='muted'>Last {a['period_days']} days · generated {e(a['generated_at'])}</div>",
           "<div class='kpis'>" + "".join(f"<div class='kpi'><b>{e(str(v))}</b>{e(k)}</div>" for k, v in kpis) + "</div>"]
    if a["warnings"]:
        out.append("<h2>Pipeline warnings</h2><div class='warn'>" + "<br>".join(e(w) for w in a["warnings"]) + "</div>")
    out += ["<h2>New tech sites by category</h2>", _bars(p["new_qualified_by_category"]),
            "<h2>Discovered by source</h2>", _bars(p["candidates_by_source"])]
    out.append(f"<h2>New tech sites ({len(a['new_sites'])})</h2>")
    if not a["new_sites"]:
        out.append("<p class='muted'>None in this period.</p>")
    for s in a["new_sites"]:
        tags = "".join(f"<span class='tag'>{e(x)}</span>" for x in s["tech_stack"])
        sig = ", ".join(x.replace("_", " ") for x in s["signals"])
        out.append(f"<div class='site'><h3><a href='{e(s['url'])}'>{e(s['name'])}</a> "
                   f"<span class='muted'>· {e(str(s['category']))} · score {s['score']}</span></h3>"
                   f"<div>{e(s['description'] or 'No description.')}</div>"
                   f"<div style='margin-top:6px'>{tags}</div>"
                   + (f"<div class='muted'>Signals: {e(sig)}</div>" if sig else "")
                   + (f"<div class='muted'>Keywords: {e(', '.join(s['keywords']))}</div>" if s["keywords"] else "")
                   + f"<div class='muted'>Found via {e(s['source'] or '')} on {e((s['discovered_at'] or '')[:10])}</div></div>")
    if p["top_keywords_new_sites"]:
        out.append("<h2>Trending keywords on new sites</h2><div>" +
                   "".join(f"<span class='tag'>{e(k)}</span>" for k in p["top_keywords_new_sites"]) + "</div>")
    out += ["<h2>Tech stack across profiled sites</h2>", _bars({x["name"]: x["sites"] for x in a["tech_stack"]})]
    if a["notable_changes"]:
        out.append("<h2>Notable changes</h2><table><tr><th>Site</th><th>Change</th><th>Date</th></tr>" +
                   "".join(f"<tr><td>{e(c['domain'])}</td><td>{e(_change_text(c))}</td><td>{e(c['detected_at'][:10])}</td></tr>"
                           for c in a["notable_changes"]) + "</table>")
    out += ["<h2>Why sites were not qualified</h2>", _bars(a["not_qualified_reasons"]), "</main></body></html>"]
    return "\n".join(out)


async def run(settings, store, days: int | None = None, **_) -> dict:
    report_cfg = settings.section("report")
    days = days or int(report_cfg.get("default_days", 7))
    a = analyze(store, days, int(report_cfg.get("max_sites_listed", 50)))
    folder = settings.output_dir / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now(timezone.utc):%Y-%m-%d}"
    md, page = folder / f"report-{stamp}.md", folder / f"report-{stamp}.html"
    md.write_text(render_markdown(a), encoding="utf-8")
    page.write_text(render_html(a), encoding="utf-8")
    logger.info("report saved to %s and %s", md, page)
    return {"markdown": str(md), "html": str(page), "new_sites": len(a["new_sites"])}
