"""Daily news report from a collection snapshot.

Writes output/worldnews/news-<date>_<time>.{html,md,csv,json} and latest.html.
Sections follow the group order in settings.json (priority countries first, then World and
Technology). Within each source, headlines are newest first. The Technology section also
lists tech headlines found in every other section. Website links open in a new tab.
"""

import csv
import html
import json
import shutil
from datetime import datetime, timezone

E = html.escape


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def local_time(value: str | None) -> str:
    dt = _dt(value)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else ""


def ago(value: str | None, now: datetime) -> str:
    dt = _dt(value)
    if not dt:
        return ""
    minutes = max(0, int((now - dt).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes}m ago"
    if minutes < 48 * 60:
        return f"{minutes // 60}h ago"
    return f"{minutes // 1440}d ago"


def link(url: str, label: str, cls: str = "") -> str:
    c = f" class='{cls}'" if cls else ""
    return f"<a{c} href='{E(url)}' target='_blank' rel='noopener noreferrer'>{E(label)}</a>"


def groups_in_order(snapshot: dict, settings) -> list[tuple[str, str, list[dict]]]:
    names = settings.group_names
    order = [g["code"] for g in settings.groups]
    by_group: dict[str, list[dict]] = {}
    for src in snapshot["sources"]:
        by_group.setdefault(src.get("group") or "OTHER", []).append(src)
    codes = [c for c in order if c in by_group] + sorted(c for c in by_group if c not in order)
    return [(c, names.get(c, c), by_group[c]) for c in codes]


def tech_from_other_sections(snapshot: dict) -> list[dict]:
    items = [i for s in snapshot["sources"] if s.get("group") != "TECH" for i in s["items"] if "tech" in i.get("topics", [])]
    return sorted(items, key=lambda i: i.get("published_at") or "", reverse=True)


CSS = """
:root{--bg:#fff;--fg:#1d2433;--muted:#5d6678;--card:#f5f7fa;--line:#e3e7ee;--accent:#2f6fde;--new:#0f7b3f;--warn:#fff4e5}
@media (prefers-color-scheme:dark){:root{--bg:#12151c;--fg:#e6e9ef;--muted:#9aa3b2;--card:#1b2029;--line:#2a313d;--accent:#6ea0ff;--new:#5fd38d;--warn:#3a2d16}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1200px;margin:0 auto;padding:20px 16px 60px}h1{font-size:26px;margin:0}
h2{font-size:21px;margin:34px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--line)}
.muted{color:var(--muted)}.small{font-size:13px}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:14px 0}
.kpi{background:var(--card);border-radius:10px;padding:10px 14px}.kpi b{display:block;font-size:22px}
.bar{position:sticky;top:0;z-index:5;background:var(--bg);padding:10px 0;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.bar input[type=search]{flex:1 1 220px;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
.chip{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;padding:4px 11px;font-size:13px;white-space:nowrap}
label.chip{cursor:pointer}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,540px),1fr));gap:14px}
.src{background:var(--card);border-radius:12px;padding:12px 14px}
.src h3{margin:0 0 8px;font-size:16px;display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}
.src ol{margin:0;padding-left:22px}.src li{margin:0 0 9px}.src li .meta{font-size:12px;color:var(--muted)}
.src li .sum{font-size:13px;color:var(--muted);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.new{background:var(--new);color:var(--bg);border-radius:4px;padding:0 5px;font-size:11px;font-weight:700;margin-right:4px}
.tech{border:1px solid var(--accent);color:var(--accent);border-radius:4px;padding:0 4px;font-size:11px;margin-left:4px}
.via{font-size:12px;color:var(--muted);font-weight:400}.fail{background:var(--warn);border-radius:10px;padding:10px 14px}
.fail li{margin:3px 0}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
td.t{white-space:nowrap;color:var(--muted);font-size:13px}
body.newonly li[data-new='0'],body.newonly tr[data-new='0']{display:none}
"""

JS = """<script>
(() => {
  const q = document.getElementById('q'), newOnly = document.getElementById('newonly');
  const rows = [...document.querySelectorAll('li[data-item], tr[data-item]')];
  q.addEventListener('input', () => { const t = q.value.toLowerCase();
    rows.forEach(r => r.style.display = !t || r.textContent.toLowerCase().includes(t) ? '' : 'none'); });
  newOnly.addEventListener('change', () => document.body.classList.toggle('newonly', newOnly.checked));
})();
</script>"""


def _item_li(item: dict, now: datetime, show_source: bool = False) -> str:
    when = item.get("published_at")
    time_txt = f"{local_time(when)} · {ago(when, now)}" if when else "time not given"
    badges = ("<span class='new'>NEW</span>" if item.get("is_new") else "")
    tech = "<span class='tech'>tech</span>" if "tech" in item.get("topics", []) and item.get("group") != "TECH" else ""
    src = f" · {E(item['source'])}" if show_source else ""
    summary = f"<div class='sum'>{E(item['summary'])}</div>" if item.get("summary") else ""
    return (f"<li data-item data-new='{1 if item.get('is_new') else 0}'>{badges}{link(item['url'], item['title'])}{tech}"
            f"<div class='meta'>{E(time_txt)}{src}</div>{summary}</li>")


def render_html(snapshot: dict, settings) -> str:
    now = _dt(snapshot["run_at"]) or datetime.now(timezone.utc)
    t = snapshot["totals"]
    groups = groups_in_order(snapshot, settings)
    failed = [s for s in snapshot["sources"] if s["status"] != "ok"]
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>World News</title>"
           f"<style>{CSS}</style></head><body><main>",
           f"<h1>World News</h1><div class='muted'>Collected {E(local_time(snapshot['run_at']))} · "
           f"latest {snapshot['limit']} per source · newest first</div>",
           "<div class='kpis'>" + "".join(f"<div class='kpi'><b>{v}</b>{E(k)}</div>" for k, v in [
               ("Headlines", t["headlines"]), ("New since last run", t["new"]),
               ("Sources OK", f"{t['ok']}/{t['sources']}"), ("Tech headlines", sum(
                   1 for s in snapshot["sources"] for i in s["items"] if "tech" in i.get("topics", [])))]) + "</div>",
           "<div class='bar'><input id='q' type='search' placeholder='Search headlines…'>"
           "<label class='chip'><input id='newonly' type='checkbox'> New only</label>"
           + "".join(f"<a class='chip' href='#{E(code)}'>{E(name)}</a>" for code, name, _ in groups) + "</div>"]

    for code, name, sources in groups:
        count = sum(len(s["items"]) for s in sources)
        out.append(f"<h2 id='{E(code)}'>{E(name)} <span class='muted small'>{count} headlines · "
                   f"{sum(s['status'] == 'ok' for s in sources)}/{len(sources)} sources</span></h2><div class='grid'>")
        for s in sources:
            if s["status"] != "ok":
                continue
            out.append(f"<section class='src'><h3>{link(s['homepage'], s['name'])}"
                       f"<span class='via'>{E(s['via'] or '')}</span></h3><ol>"
                       + "".join(_item_li(i, now) for i in s["items"]) + "</ol></section>")
        out.append("</div>")
        if code == "TECH":
            others = tech_from_other_sections(snapshot)
            if others:
                out.append(f"<h3>Tech headlines from other sections ({len(others)})</h3><div class='src'><ol>"
                           + "".join(_item_li(i, now, show_source=True) for i in others) + "</ol></div>")

    if failed:
        out.append(f"<h2 id='failed'>Sources with no headlines ({len(failed)})</h2><div class='fail'>"
                   "<p class='small'>Check these on the site; fix the feed URL or add selectors in sources.json.</p><ul>"
                   + "".join(f"<li><b>{link(s['homepage'], s['name'])}</b> <span class='muted'>({E(s.get('group') or '')})</span>: "
                             f"{E((s.get('error') or '')[:300])}</li>" for s in failed) + "</ul></div>")

    every = sorted((i for s in snapshot["sources"] for i in s["items"]), key=lambda i: i.get("published_at") or "", reverse=True)
    out.append(f"<h2 id='all'>All headlines, newest first ({len(every)})</h2><table><tr><th>Time</th><th>Headline</th>"
               "<th>Source</th></tr>" + "".join(
                   f"<tr data-item data-new='{1 if i.get('is_new') else 0}'><td class='t'>{E(local_time(i.get('published_at')) or '–')}</td>"
                   f"<td>{'<span class=new>NEW</span>' if i.get('is_new') else ''}{link(i['url'], i['title'])}</td>"
                   f"<td class='t'>{E(i['source'])}</td></tr>" for i in every) + "</table>")
    out.append(JS + "</main></body></html>")
    return "\n".join(out)


def render_markdown(snapshot: dict, settings) -> str:
    t = snapshot["totals"]
    lines = [f"# World News: {local_time(snapshot['run_at'])}",
             f"{t['headlines']} headlines ({t['new']} new) from {t['ok']}/{t['sources']} sources, newest first.", ""]
    for code, name, sources in groups_in_order(snapshot, settings):
        lines += [f"## {name}", ""]
        for s in sources:
            if s["status"] != "ok":
                continue
            lines.append(f"### [{s['name']}]({s['homepage']})")
            for n, i in enumerate(s["items"], 1):
                new = "**NEW** " if i.get("is_new") else ""
                when = local_time(i.get("published_at")) or "time not given"
                lines.append(f"{n}. {new}[{i['title']}]({i['url']}) · {when}")
            lines.append("")
        if code == "TECH":
            others = tech_from_other_sections(snapshot)
            if others:
                lines += ["### Tech headlines from other sections",
                          *[f"- [{i['title']}]({i['url']}) · {i['source']} · {local_time(i.get('published_at')) or '–'}"
                            for i in others], ""]
    failed = [s for s in snapshot["sources"] if s["status"] != "ok"]
    if failed:
        lines += ["## Sources with no headlines", *[f"- {s['name']}: {s.get('error')}" for s in failed]]
    return "\n".join(lines) + "\n"


def write_csv(snapshot: dict, path) -> None:
    fields = ["group", "source", "rank", "published_local", "title", "url", "summary", "is_new", "topics", "published_at"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for s in snapshot["sources"]:
            for i in s["items"]:
                w.writerow({**i, "published_local": local_time(i.get("published_at")), "topics": ",".join(i.get("topics", []))})


def write_report(snapshot: dict, settings) -> dict:
    folder = settings.output_dir
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (_dt(snapshot["run_at"]) or datetime.now(timezone.utc)).astimezone().strftime("%Y-%m-%d_%H%M")
    paths = {ext: folder / f"news-{stamp}.{ext}" for ext in ("html", "md", "csv", "json")}
    paths["html"].write_text(render_html(snapshot, settings), encoding="utf-8")
    paths["md"].write_text(render_markdown(snapshot, settings), encoding="utf-8")
    write_csv(snapshot, paths["csv"])
    paths["json"].write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copyfile(paths["html"], folder / "latest.html")
    return {k: str(v) for k, v in paths.items()} | {"latest": str(folder / "latest.html")}
