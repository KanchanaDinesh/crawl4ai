// Intelligence Dashboard: World News, Tech Intel, Markets, Vehicles and Companies, reading from the local DuckDB-backed API.
// Charts are plain SVG drawn here (no library, works offline).
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(2, "0");
const PAGE = 100;
// "780 · showing 100": lists load PAGE rows at a time, so say how many of the total are on the page
const shown = (total, n, rest) => `${fmtNum(total)}${n < total ? ` · showing ${fmtNum(n)}` : ""} · ${rest}`;

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function fmtDate(iso) { return fmtTime(iso).slice(0, 10); }
function ago(iso) {
  if (!iso) return "";
  const m = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
  if (m < 60) return `${m}m ago`;
  if (m < 48 * 60) return `${Math.round(m / 60)}h ago`;
  return `${Math.round(m / 1440)}d ago`;
}
const urlLabel = (u) => String(u || "").replace(/\/$/, "");
const link = (url, label, cls = "") => url ? `<a ${cls ? `class="${cls}" ` : ""}href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label ?? urlLabel(url))}</a>` : esc(label || "");
const pill = (s) => `<span class="pill p-${esc(String(s).replace(/\s+/g, "-"))}">${esc(s)}</span>`;
const fmtNum = (n) => (n ?? 0).toLocaleString();

async function api(path, params = {}) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined && v !== false));
  const res = await fetch(`${path}${qs.toString() ? "?" + qs : ""}`);
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

function bars(el, rows, label, value, labelFn) {
  if (!rows || !rows.length) { el.innerHTML = `<div class="muted small">No data yet.</div>`; return; }
  const max = Math.max(...rows.map((r) => r[value])) || 1;
  el.innerHTML = rows.map((r) => `<div class="row"><span title="${esc(labelFn ? labelFn(r) : r[label])}">${esc(labelFn ? labelFn(r) : (r[label] ?? "—"))}</span>
    <i style="width:${Math.max(2, (100 * r[value]) / max)}%"></i><span class="n">${fmtNum(r[value])}</span></div>`).join("");
}

function last14Days() {
  const days = [], today = new Date();
  for (let i = 13; i >= 0; i--) {
    const d = new Date(today); d.setDate(today.getDate() - i);
    days.push(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`);
  }
  return days;
}

function dayColumns(el, rows, total, part, partLabel = "tech") {
  const days = last14Days();
  const byDay = Object.fromEntries((rows || []).map((r) => [r.day, r]));
  const max = Math.max(1, ...days.map((d) => byDay[d]?.[total] || 0));
  el.innerHTML = `<div class="cols">${days.map((d) => {
    const r = byDay[d] || {}; const t = r[total] || 0; const p = part ? r[part] || 0 : 0;
    return `<div class="c" title="${d}: ${t}${part ? ` (${p} ${partLabel})` : ""}">
      ${part ? `<i class="t" style="height:${(100 * p) / max}%"></i>` : ""}<i style="height:${(100 * (t - p)) / max}%"></i><em>${d.slice(8)}</em></div>`;
  }).join("")}</div>`;
}

// ───────────────────────────── charts ─────────────────────────────
const PALETTE = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--c7", "--c8"];
const color = (i) => `var(${PALETTE[i % PALETTE.length]})`;
const charts = new Set();                                  // redrawn when the window size changes
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function fmtAxis(v) {
  const a = Math.abs(v);
  if (a >= 1e6) return fmtBig(v);
  if (a >= 1000) return Math.round(v).toLocaleString();
  if (a >= 1) return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return Number(v.toPrecision(3)).toString();
}

// series: [{name, color, points: [{t: ms, v: number}]}]; opts: {height, pct, fmt, min0, empty}
function lineChart(el, series, opts = {}) {
  el._chart = [series, opts]; charts.add(el);
  series = series.filter((s) => s.points.length);
  if (!series.some((s) => s.points.length >= 2)) {
    el.innerHTML = `<div class="muted small">${opts.empty || "Not enough history yet: each run adds a point."}</div>`; return;
  }
  const W = Math.max(300, el.clientWidth || 600), H = opts.height || 260, L = 58, R = 14, T = 10, B = 24;
  const all = series.flatMap((s) => s.points);
  const t0 = Math.min(...all.map((p) => p.t)), t1 = Math.max(...all.map((p) => p.t));
  let lo = Math.min(...all.map((p) => p.v)), hi = Math.max(...all.map((p) => p.v));
  if (opts.pct || opts.min0) { lo = Math.min(lo, 0); }
  if (opts.pct) hi = Math.max(hi, 0);
  const span = hi - lo || Math.abs(hi) * 0.02 || 1;
  if (!opts.min0) lo -= span * 0.06;
  hi += span * 0.06;
  const X = (t) => L + ((t - t0) / (t1 - t0 || 1)) * (W - L - R);
  const Y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const fmtV = opts.fmt || ((v) => opts.pct ? `${v > 0 ? "+" : ""}${v.toFixed(hi - lo < 6 ? 2 : 1)}%` : fmtAxis(v));
  const days = (t1 - t0) / 864e5;
  const fmtT = (t) => { const d = new Date(t); return days < 2 ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : `${MONTHS[d.getMonth()]} ${d.getDate()}`; };
  let g = "";
  for (let i = 0; i <= 4; i++) {
    const v = lo + ((hi - lo) * i) / 4, y = Y(v);
    g += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y}" y2="${y}"/><text class="axis" x="${L - 6}" y="${y + 4}" text-anchor="end">${esc(fmtV(v))}</text>`;
  }
  for (let i = 0; i <= 4; i++) {
    const t = t0 + ((t1 - t0) * i) / 4;
    g += `<text class="axis" x="${X(t)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === 4 ? "end" : "middle"}">${esc(fmtT(t))}</text>`;
  }
  if (opts.pct && lo < 0 && hi > 0) g += `<line class="zero" x1="${L}" x2="${W - R}" y1="${Y(0)}" y2="${Y(0)}"/>`;
  const lines = series.map((s) => `<polyline class="series" style="stroke:${s.color}" points="${s.points.map((p) => `${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}`).join(" ")}"/>`).join("");
  el.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${g}${lines}<line class="cursor" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>
    <rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}"/></svg><div class="tip" hidden></div>`
    + (series.length > 1 ? `<div class="legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>` : "");
  const svg = el.querySelector("svg"), tip = el.querySelector(".tip"), cur = el.querySelector(".cursor");
  const nearest = (pts, t) => pts.reduce((a, b) => Math.abs(b.t - t) < Math.abs(a.t - t) ? b : a);
  el.querySelector(".hit").addEventListener("mousemove", (e) => {
    const r = svg.getBoundingClientRect(), px = ((e.clientX - r.left) * W) / r.width;
    const t = t0 + ((px - L) / (W - L - R)) * (t1 - t0);
    const near = Math.max(2 * 864e5, (t1 - t0) * 0.04);          // further away: that line has no data here
    const hits = series.map((s) => { const p = nearest(s.points, t); return [s, Math.abs(p.t - t) <= near ? p : null]; });
    const at = (hits.find(([, p]) => p) || [null, nearest(series[0].points, t)])[1].t;
    cur.setAttribute("x1", X(at)); cur.setAttribute("x2", X(at)); cur.setAttribute("visibility", "visible");
    const when = new Date(at).toISOString();
    tip.innerHTML = `<b>${esc(days < 2 ? fmtTime(when) : fmtDate(when))}</b>` + hits.map(([s, p]) =>
      `<div><span style="color:${s.color}">●</span> ${esc(s.name)}: <b style="display:inline">${p ? esc(fmtV(p.v)) : "–"}</b></div>`).join("");
    tip.hidden = false;
    const left = (X(at) / W) * r.width;
    tip.style.left = `${left > r.width - 200 ? left - tip.offsetWidth - 12 : left + 12}px`; tip.style.top = "8px";
  });
  el.querySelector(".hit").addEventListener("mouseleave", () => { tip.hidden = true; cur.setAttribute("visibility", "hidden"); });
}

// One bar split into parts: [{label, n, color}]
function stackBar(el, parts) {
  const total = parts.reduce((a, p) => a + p.n, 0);
  if (!total) { el.innerHTML = `<div class="muted small">No data yet.</div>`; return; }
  el.innerHTML = `<div class="stack">${parts.map((p) => `<i style="width:${(100 * p.n) / total}%;background:${p.color}" title="${esc(p.label)}: ${p.n}"></i>`).join("")}</div>
    <div class="legend">${parts.map((p) => `<span><i style="background:${p.color}"></i>${esc(p.label)} <b>${fmtNum(p.n)}</b> <span class="muted">${Math.round((100 * p.n) / total)}%</span></span>`).join("")}</div>`;
}

// Simple columns with labels under them: values[i] for labels[i]
function columns(el, labels, values, titleFn) {
  const max = Math.max(1, ...values);
  el.innerHTML = `<div class="cols">${values.map((v, i) => `<div class="c" title="${esc(titleFn ? titleFn(i, v) : `${labels[i]}: ${v}`)}">
    <i style="height:${(100 * v) / max}%"></i><em>${esc(labels[i])}</em></div>`).join("")}</div>`;
}

function setBanner(results) {
  const busy = results.some((r) => r && r.busy);
  const err = results.map((r) => r && r.error).find(Boolean);
  const b = $("banner");
  b.hidden = !busy && !err;
  b.textContent = err || (busy ? "A collection run is writing to the database right now; showing the last loaded data. It will update when the run finishes." : "");
}

const debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
window.addEventListener("resize", debounce(() => charts.forEach((el) => {
  if (el.isConnected && el.offsetParent) lineChart(el, ...el._chart);
}), 150));
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// ───────────────────────────── NEWS ─────────────────────────────
const news = { days: 7, group: "", source: "", topic: "", new: false, q: "", offset: 0, groups: [] };
const groupName = (code) => (news.groups.find((g) => g.code === code) || {}).name || code || "—";

function emptyNews() {
  return `<div class="empty">No news collected yet.<br>Run <code>uv run python -m worldnews run</code>, then refresh this page.</div>`;
}

async function loadNews() {
  const [summary, sources, runs] = await Promise.all([
    api("/api/news/summary", { days: news.days }), api("/api/news/sources"), api("/api/news/runs")]);
  setBanner([summary, sources, runs]);
  news.groups = summary.groups || [];
  const s = summary.data;
  if (!s) { $("news-kpis").innerHTML = ""; $("news-list").innerHTML = emptyNews(); $("news-count").textContent = ""; return; }
  const last = s.last_run || {};
  const periodLabel = $("news-days").selectedOptions[0].text.toLowerCase();
  $("news-kpis").innerHTML = [
    [fmtNum(s.totals.in_period), `Headlines, ${periodLabel}`],
    [fmtNum(last.new_headlines), "New in last run"],
    [fmtNum(s.totals.tech_in_period), `Tech headlines, ${periodLabel}`],
    [`${last.sources_ok ?? 0}/${last.sources ?? 0}`, "Sources OK (last run)"],
    [last.run_at ? ago(last.run_at) : "never", `Last run ${last.run_at ? fmtTime(last.run_at) : ""}`],
    [fmtNum(s.totals.headlines), "Headlines stored (all time)"],
  ].map(([v, l]) => `<div class="kpi"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join("");
  $("news-period-label").textContent = periodLabel;

  const counts = Object.fromEntries((s.by_group || []).map((r) => [r.group_code, r.n]));
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  $("news-groups").innerHTML = [`<button class="chip ${news.group ? "" : "active"}" data-group="">All<small>${total}</small></button>`]
    .concat(news.groups.map((g) => `<button class="chip ${news.group === g.code ? "active" : ""}" data-group="${esc(g.code)}">${esc(g.name)}<small>${counts[g.code] || 0}</small></button>`)).join(" ");

  const srcList = (sources.data || []).map((r) => r.source).sort((a, b) => a.localeCompare(b));
  const sel = $("news-source");
  sel.innerHTML = `<option value="">All sources</option>` + srcList.map((n) => `<option ${n === news.source ? "selected" : ""}>${esc(n)}</option>`).join("");

  dayColumns($("news-days-chart"), s.by_day, "n", "tech");
  const tday = Object.fromEntries((s.topics_by_day || []).map((r) => [r.day, r]));
  lineChart($("news-topics-chart"), ["tech", "crypto", "markets"].map((k, i) => ({
    name: k, color: color(i), points: last14Days().map((d) => ({ t: +new Date(`${d}T12:00:00`), v: (tday[d] || {})[k] || 0 })),
  })), { height: 170, min0: true, fmt: (v) => String(Math.round(v)) });
  const off = -new Date().getTimezoneOffset();                    // minutes ahead of UTC (Sri Lanka: +330)
  const hours = Array(24).fill(0);
  (s.by_hour || []).forEach((r) => { hours[Math.floor((((r.hour * 60 + off) % 1440) + 1440) % 1440 / 60)] += r.n; });
  columns($("news-hours-chart"), hours.map((_, h) => (h % 3 === 0 ? String(h) : "")), hours,
    (h, v) => `${pad(h)}:00-${pad(h)}:59: ${v} headlines`);
  bars($("news-by-group"), s.by_group, "group_code", "n", (r) => groupName(r.group_code));
  bars($("news-top-sources"), s.top_sources, "source", "n");

  const health = (sources.data || []);
  $("news-health").innerHTML = health.length ? `<table><tr><th>Source</th><th>Status</th><th>Via</th><th class="num">N</th></tr>${health.map((r) => `
    <tr title="${esc(r.error || "")}"><td>${esc(r.source)}<div class="muted small">${esc(groupName(r.group_code))}${r.error ? " · " + esc(r.error.slice(0, 90)) : ""}</div></td>
    <td>${pill(r.status)}${r.failed_streak > 1 ? `<div class="muted small">${r.failed_streak} runs in a row</div>` : ""}</td>
    <td class="small muted">${esc(r.via || "")}</td><td class="num">${r.headlines}</td></tr>`).join("")}</table>` : `<div class="muted small">No runs yet.</div>`;

  $("news-runs").innerHTML = (runs.data || []).length ? `<table><tr><th>When</th><th class="num">OK</th><th class="num">Headlines</th><th class="num">New</th></tr>${runs.data.map((r) => `
    <tr><td class="nowrap">${fmtTime(r.run_at)}<div class="muted small">${r.seconds ?? "?"}s · limit ${r.item_limit}</div></td>
    <td class="num">${r.sources_ok}/${r.sources}</td><td class="num">${r.headlines}</td><td class="num">${r.new_headlines}</td></tr>`).join("")}</table>` : "";
  await loadNewsItems(true);
  await loadNewsAnalysis();                                // errors reach refresh(), which explains them
}

// Our own analysis of the headlines in the chosen period and country (worldnews/analysis.py)
const TOPIC_NAME = { crime_justice: "crime & justice", accidents_disasters: "accidents & disasters",
  climate_environment: "climate & environment", science_space: "science & space" };
let analysisReq = 0;
async function loadNewsAnalysis() {
  const ticket = ++analysisReq;
  $("na-label").textContent = "working…";
  let res;
  try {
    res = await api("/api/news/analysis", { days: news.days, group: news.group });
  } catch (err) {
    $("na-label").textContent = "could not load";
    throw err;
  }
  if (ticket !== analysisReq) return;
  const a = res.data;
  const none = `<div class="muted small">No headlines in this period yet.</div>`;
  if (!a || !a.headlines) {
    ["na-stories", "na-topics", "na-names", "na-countries", "na-tone", "na-by-group", "na-rising"].forEach((id) => { $(id).innerHTML = none; });
    $("na-label").textContent = ""; return;
  }
  $("na-label").textContent = `${fmtNum(a.headlines)} headlines · ${a.sources} sources`;
  $("na-stories").innerHTML = (a.stories || []).slice(0, 8).map((s) => `<div class="story">
      <span class="n" title="${esc(s.sources.join(", "))}">${s.sources.length}</span>${link(s.url, s.headline)}
      <details><summary>${esc(s.sources.slice(0, 6).join(", "))}${s.sources.length > 6 ? ` +${s.sources.length - 6}` : ""} · ${s.count} headlines</summary>
        ${s.headlines.map((h) => `<div>${link(h.url, h.title)} <span class="muted">· ${esc(h.source || "")}</span></div>`).join("")}</details></div>`).join("")
    || `<div class="muted small">No story was told by several sources in this period.</div>`;
  bars($("na-topics"), (a.topics || []).filter((t) => t.topic !== "other").slice(0, 12), "topic", "headlines",
    (r) => `${TOPIC_NAME[r.topic] || r.topic} · ${r.share}%`);
  bars($("na-names"), (a.people || []).slice(0, 15), "name", "headlines");
  bars($("na-countries"), (a.countries || []).slice(0, 15), "country", "headlines");
  const toneRow = (label, t) => `<div class="tone-row"><span>${esc(label)}</span>
    <div class="stack" title="${t.negative} negative, ${t.neutral} neutral, ${t.positive} positive">
      <i style="width:${(100 * t.negative) / (t.headlines || 1)}%;background:var(--bad)"></i>
      <i style="width:${(100 * t.neutral) / (t.headlines || 1)}%;background:transparent"></i>
      <i style="width:${(100 * t.positive) / (t.headlines || 1)}%;background:var(--ok)"></i></div><em>${esc(t.label || "")}</em></div>`;
  $("na-tone-label").textContent = `overall ${a.tone.label} · word-based, rough`;
  $("na-tone").innerHTML = (a.tone.by_group || []).map((g) => toneRow(g.name, g)).join("")
    + `<div class="muted small" style="margin-top:6px">red: words like killed, crisis · green: wins, record</div>`;
  $("na-by-group").innerHTML = `<table>${(a.topics_by_group || []).map((g) => `<tr><td class="nowrap">${esc(g.name)}</td>
    <td class="small">${g.top.slice(0, 4).map((t) => `${esc(TOPIC_NAME[t.topic] || t.topic)} <span class="muted">${t.headlines}</span>`).join(" · ")}</td></tr>`).join("")}</table>`;
  $("na-rising").innerHTML = (a.rising || []).length
    ? a.rising.map((w) => `<span class="tag click" data-q="${esc(w.word)}">${esc(w.word)} <span class="muted">${w.before}→${w.now}</span></span>`).join("")
    : `<div class="muted small">Needs an earlier period to compare with: shows up after another ${news.days === 1 ? "day" : "period"} of collecting.</div>`;
}
function searchNews(text) {
  news.q = text; $("news-q").value = text; loadNewsItems(true);
  $("news-list").scrollIntoView({ behavior: "smooth", block: "start" });
}
for (const id of ["na-names", "na-countries"]) {
  $(id).addEventListener("click", (e) => {
    const row = e.target.closest(".row"); if (row) searchNews(row.querySelector("span").textContent.trim());
  });
}
$("na-rising").addEventListener("click", (e) => { const t = e.target.closest("[data-q]"); if (t) searchNews(t.dataset.q); });

let newsReq = 0;
async function loadNewsItems(reset) {
  if (reset) news.offset = 0;
  const ticket = ++newsReq;                                // ignore answers to older requests
  const res = await api("/api/news/items", { days: news.days, group: news.group, source: news.source,
    topic: news.topic, new: news.new ? 1 : "", q: news.q, limit: PAGE, offset: news.offset });
  if (ticket !== newsReq) return;
  const d = res.data || { total: 0, items: [] };
  $("news-count").textContent = shown(d.total, news.offset + d.items.length, "newest first");
  const html = d.items.map((i) => {
    const when = i.published_at || i.first_seen_at;
    return `<div class="news-item">
      <div class="title">${i.is_new ? `<span class="badge b-new">NEW</span>` : ""}${link(i.url, i.title)}${(i.topics || []).filter((t) => ({ tech: "TECH", crypto: "CRYPTO", markets: "MARKETS" })[t] !== i.group_code).map((t) => ` <span class="badge b-tech">${esc(t)}</span>`).join("")}</div>
      <div class="meta">${esc(fmtTime(when))} · ${esc(ago(when))}${i.published_at ? "" : " (found)"} · <b>${esc(i.source)}</b> · ${esc(groupName(i.group_code))}${i.times_seen > 1 ? ` · seen in ${i.times_seen} runs` : ""}</div>
      ${i.summary ? `<div class="sum">${esc(i.summary)}</div>` : ""}</div>`;
  }).join("");
  if (reset) $("news-list").innerHTML = html || `<div class="empty">No headlines match these filters.</div>`;
  else $("news-list").insertAdjacentHTML("beforeend", html);
  news.offset += d.items.length;
  $("news-more").hidden = news.offset >= d.total;
}

$("news-groups").addEventListener("click", (e) => {
  const b = e.target.closest("[data-group]"); if (!b) return;
  news.group = b.dataset.group;
  document.querySelectorAll("#news-groups .chip").forEach((c) => c.classList.toggle("active", c === b));
  loadNewsItems(true);
  loadNewsAnalysis().catch(showLoadError);              // the analysis follows the chosen country
});
$("news-days").addEventListener("change", (e) => { news.days = +e.target.value; loadNews(); });
$("news-source").addEventListener("change", (e) => { news.source = e.target.value; loadNewsItems(true); });
$("news-topic").addEventListener("change", (e) => { news.topic = e.target.value; loadNewsItems(true); });
$("news-new").addEventListener("change", (e) => { news.new = e.target.checked; loadNewsItems(true); });
$("news-q").addEventListener("input", debounce((e) => { news.q = e.target.value.trim(); loadNewsItems(true); }));
$("news-more").addEventListener("click", () => loadNewsItems(false));

// ───────────────────────────── TECH ─────────────────────────────
const tech = { days: 30, status: "", category: "", source: "", company: "", q: "", sort: "seen", offset: 0, rows: [] };
// Company facts (techintel company): short labels for the social accounts shown in the list
const SOCIAL = { linkedin: ["in", "LinkedIn"], facebook: ["f", "Facebook"], x: ["X", "X (Twitter)"], instagram: ["ig", "Instagram"],
  youtube: ["yt", "YouTube"], github: ["gh", "GitHub"], tiktok: ["tt", "TikTok"] };
function socialLinks(social) {
  const s = typeof social === "string" ? JSON.parse(social || "{}") : social || {};
  return Object.keys(SOCIAL).filter((k) => s[k])
    .map((k) => `<a class="soc" href="${esc(s[k])}" target="_blank" rel="noopener noreferrer" title="${SOCIAL[k][1]}">${SOCIAL[k][0]}</a>`).join("");
}
const STATUS_COLOR = { qualified: "var(--ok)", watch: "var(--warn)", rejected: "var(--bad)", "not checked": "var(--muted)" };

// "3 days", "5 weeks", "2 years" since a domain was registered, and whether it's brand new (under 30 days)
function domainAge(iso) {
  if (!iso) return null;
  const d = (Date.now() - new Date(iso)) / 864e5;
  const text = d < 1 ? "today" : d < 14 ? `${Math.round(d)} days` : d < 60 ? `${Math.round(d / 7)} weeks`
    : d < 730 ? `${Math.round(d / 30)} months` : `${Math.round(d / 365)} years`;
  return { text, young: d < 30 };
}

async function loadTech() {
  const [summary, changes, runs] = await Promise.all([
    api("/api/tech/summary", { days: tech.days }), api("/api/tech/changes", { days: tech.days }), api("/api/tech/runs")]);
  setBanner([summary, changes, runs]);
  const s = summary.data;
  if (!s) {
    $("tech-kpis").innerHTML = "";
    $("tech-table").innerHTML = `<div class="empty">No tech-intel data yet.<br>Run <code>uv run python -m techintel run --limit 30</code>, then refresh this page.</div>`;
    $("tech-count").textContent = ""; return;
  }
  const c = s.counts, periodLabel = $("tech-days").selectedOptions[0].text.toLowerCase();
  $("tech-kpis").innerHTML = [
    [fmtNum(c.new_candidates), `New domains, ${periodLabel}`], [fmtNum(c.qualified), "Qualified tech sites"],
    [fmtNum(c.watch), "On watch"], [fmtNum(c.rejected), "Rejected"], [fmtNum(c.profiled), "Profiled"],
    [fmtNum(c.new_domains_30d), "Qualified on domains under 30 days old"],
    [fmtNum(c.changes), `Changes, ${periodLabel}`], [fmtNum(c.candidates), "Domains (all time)"],
    ...(c.companies != null ? [[fmtNum(c.with_linkedin), `With LinkedIn, of ${fmtNum(c.companies)} companies read`],
      [fmtNum(c.with_people), "Name their leadership"]] : []),
  ].map(([v, l]) => `<div class="kpi"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join("");

  const total = (s.by_status || []).reduce((a, r) => a + r.n, 0);
  const order = ["qualified", "watch", "rejected", "not checked"];
  const st = Object.fromEntries((s.by_status || []).map((r) => [r.status, r.n]));
  $("tech-status").innerHTML = [`<button class="chip ${tech.status ? "" : "active"}" data-status="">All<small>${total}</small></button>`]
    .concat(order.filter((k) => st[k]).map((k) => `<button class="chip ${tech.status === k ? "active" : ""}" data-status="${k}">${k}<small>${st[k]}</small></button>`)).join(" ");
  $("tech-category").innerHTML = `<option value="">All categories</option>` + (s.by_category || []).filter((r) => r.category)
    .map((r) => `<option value="${esc(r.category)}" ${r.category === tech.category ? "selected" : ""}>${esc(r.category)} (${r.n})</option>`).join("");
  $("tech-source").innerHTML = `<option value="">All sources</option>` + (s.by_source || [])
    .map((r) => `<option value="${esc(r.source)}" ${r.source === tech.source ? "selected" : ""}>${esc(r.source)} (${r.n})</option>`).join("");

  dayColumns($("tech-days-chart"), s.by_day, "n", "qualified", "qualified");
  bars($("tech-age"), s.domain_age, "age", "n");
  bars($("tech-by-category"), (s.by_category || []).slice(0, 10), "category", "n");
  $("tech-status-label").textContent = periodLabel;
  stackBar($("tech-status-chart"), order.filter((k) => st[k]).map((k) => ({ label: k, n: st[k], color: STATUS_COLOR[k] })));
  bars($("tech-stack"), s.tech_stack, "tech", "n");
  $("tech-social-label").textContent = c.companies != null ? `${fmtNum(c.companies)} companies read` : "company facts";
  if ((s.social_platforms || []).length) bars($("tech-social"), s.social_platforms, "platform", "n");
  else $("tech-social").innerHTML = `<div class="muted small">Not collected yet. Run <code>uv run python -m techintel company</code>.</div>`;
  bars($("tech-by-source"), s.by_source, "source", "n");
  $("tech-changes").innerHTML = (changes.data || []).length ? changes.data.slice(0, 25).map((x) => `
    <div class="news-item"><div><b>${esc(x.domain)}</b> ${esc(changeText(x))}</div><div class="meta">${fmtTime(x.detected_at)} · ${ago(x.detected_at)}</div></div>`).join("")
    : `<div class="muted small">No changes detected yet.</div>`;
  $("tech-runs").innerHTML = (runs.data || []).length ? `<table><tr><th>Stage</th><th>Status</th><th>Finished</th><th class="num">Took</th></tr>${runs.data.map((r) => `
    <tr title="${esc(r.summary || "")}"><td>${esc(r.stage)}</td><td>${pill(r.status)}</td><td class="nowrap">${fmtTime(r.finished_at)}</td>
    <td class="num nowrap">${r.seconds == null ? "" : r.seconds >= 60 ? `${Math.floor(r.seconds / 60)}m${String(Math.round(r.seconds % 60)).padStart(2, "0")}s` : `${Math.round(r.seconds)}s`}</td></tr>`).join("")}</table>`
    : `<div class="muted small">No runs yet.</div>`;
  await loadTechRows(true);
}

const CHANGE = { tech_added: "started using", tech_removed: "stopped using", page_added: "added page", page_removed: "removed page",
  signal_on: "now has", signal_off: "no longer has", site_down: "went offline", site_up: "back online",
  description_changed: "changed description", name_changed: "renamed", job_links_changed: "job links", content_changed: "updated page" };
function changeText(x) {
  const label = CHANGE[x.change_type] || x.change_type;
  if (x.change_type === "tech_added") return `${label} ${x.after}`;
  if (x.change_type === "tech_removed") return `${label} ${x.before}`;
  if (x.change_type === "job_links_changed") return `${label}: ${x.before} → ${x.after}`;
  if (["signal_on", "signal_off", "page_added", "page_removed", "content_changed"].includes(x.change_type)) return `${label} ${String(x.field).replace(/_/g, " ")}`;
  return label;
}

let techReq = 0;
async function loadTechRows(reset) {
  const ticket = ++techReq;                                // ignore answers to older requests
  const offset = reset ? 0 : tech.offset;
  const res = await api("/api/tech/domains", { days: tech.days, status: tech.status, category: tech.category,
    source: tech.source, company: tech.company, q: tech.q, sort: tech.sort, limit: PAGE, offset });
  if (ticket !== techReq) return;
  if (reset) { tech.offset = 0; tech.rows = []; }
  const d = res.data || { total: 0, items: [] };
  tech.rows = tech.rows.concat(d.items);
  $("tech-count").textContent = shown(d.total, tech.rows.length,
    tech.rows.length < d.total ? "scroll the list for more · click a row for details" : "click a row for details");
  const maxScore = Math.max(1, ...tech.rows.map((r) => r.score || 0));
  $("tech-table").innerHTML = tech.rows.length ? `<table class="dense"><thead><tr><th>#</th><th>Seen</th><th>Site</th><th>Status</th>
    <th>Category</th><th class="num">Score</th><th>Domain age</th><th>Company</th><th>Source</th></tr></thead><tbody>${tech.rows.map((r, n) => {
      const age = domainAge(r.registered_at);
      return `<tr class="click" data-domain="${esc(r.domain)}"><td class="num muted">${n + 1}</td>
    <td class="nowrap" title="${esc(fmtTime(r.seen_at))}">${ago(r.seen_at)}</td>
    <td class="site"><div class="nm">${esc(r.name || r.domain)} ${link(r.url || "https://" + r.domain + "/", r.domain)}</div>
      <div class="ds" title="${esc(r.description || r.reason || "")}">${esc(r.description || r.reason || "")}</div></td>
    <td>${pill(r.status)}</td><td class="nowrap">${esc(r.category || "")}</td>
    <td class="num">${r.score == null ? "" : `<span class="scorebar">${Math.round(r.score)}<i style="width:${Math.round((40 * r.score) / maxScore)}px"></i></span>`}</td>
    <td class="nowrap" title="${r.registered_at ? "registered " + esc(fmtDate(r.registered_at)) : "registration date unknown"}">${age
      ? `${age.young ? `<span class="badge b-young">NEW</span>` : ""}${esc(age.text)}` : `<span class="muted">–</span>`}</td>
    <td class="nowrap small">${socialLinks(r.social)}${r.people ? ` <span title="people named on its site">👤${r.people}</span>` : ""}${r.employees
      ? ` <span class="muted" title="employees, as the site says">${esc(String(r.employees).slice(0, 18))}</span>` : ""}</td>
    <td class="small nowrap" title="${esc(r.source_title || "")}">${esc(r.source || "")}</td></tr>`;
    }).join("")}</tbody></table>`
    : `<div class="empty">No domains match these filters.</div>`;
  tech.offset += d.items.length;
  $("tech-more").hidden = tech.offset >= d.total;
}

async function openDomain(domain) {
  $("drawer-body").innerHTML = `<div class="muted">Loading ${esc(domain)}…</div>`;
  $("drawer").classList.add("open"); $("drawer-bg").classList.add("open");
  const res = await api(`/api/tech/domain/${encodeURIComponent(domain)}`);
  const d = res.data || {};
  const q = (d.qualifications || []).slice(-1)[0] || {}, p = d.profile, c = d.candidate || {}, reg = d.registration || {};
  const url = (p && p.final_url) || q.final_url || c.url || `https://${domain}/`;
  const sig = p ? Object.entries(p.signals || {}).filter(([, v]) => v).map(([k]) => k.replace(/_/g, " ")) : [];
  $("drawer-body").innerHTML = `
    <h2>${esc((p && p.name) || q.title || domain)}</h2>
    <div>${link(url)} ${q.status ? pill(q.status) : pill("not checked")}</div>
    ${(p && p.description) || q.description ? `<p>${esc((p && p.description) || q.description)}</p>` : ""}
    <h4>Details</h4><div class="kv">
      <div>Category</div><div>${esc(q.category || "—")}${q.score ? ` · score ${q.score}` : ""}</div>
      <div>Status reason</div><div>${esc(q.reason || "—")}</div>
      <div>Seen online</div><div>${fmtTime(c.signal_at || c.discovered_at)}</div>
      <div>Found by us</div><div>${fmtTime(c.discovered_at)}</div>
      <div>Registered</div><div>${reg.registered_at ? fmtDate(reg.registered_at) + (reg.registrar ? ` · ${esc(reg.registrar)}` : "") : esc(reg.error ? "unknown (" + reg.error + ")" : "unknown")}</div>
      <div>Source</div><div>${esc(c.source || "")}${c.detail && c.detail.title ? ` · ${c.detail.hn_url ? link(c.detail.hn_url, c.detail.title) : esc(c.detail.title)}` : ""}${c.detail && c.detail.cert_name ? ` · cert ${esc(c.detail.cert_name)}` : ""}</div>
    </div>
    ${p ? `<h4>Tech stack</h4><div>${(p.tech_stack || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("") || '<span class="muted">none detected</span>'}</div>
      <h4>Signals</h4><div>${sig.map((t) => `<span class="tag">${esc(t)}</span>`).join("") || '<span class="muted">none</span>'}</div>
      <h4>Key pages</h4><div>${Object.entries(p.key_pages || {}).map(([k, u]) => `<div>${esc(k)}: ${link(u)}</div>`).join("") || '<span class="muted">none found</span>'}</div>
      ${(p.keywords || []).length ? `<h4>Keywords</h4><div>${p.keywords.map((k) => `<span class="tag">${esc(k)}</span>`).join("")}</div>` : ""}
      ${Object.keys(p.social || {}).length && !d.company ? `<h4>Social</h4><div>${Object.entries(p.social).map(([k, u]) => link(u, k)).join(" · ")}</div>` : ""}`
      : `<h4>Profile</h4><div class="muted">Not profiled yet${q.status === "qualified" ? " (run the profile stage)" : " (only qualified sites are profiled)"}.</div>`}
    ${companyHtml(d.company)}
    <h4>Changes</h4>${(d.changes || []).length ? d.changes.map((x) => `<div>${fmtTime(x.detected_at)} · ${esc(changeText(x))}</div>`).join("") : '<div class="muted">None detected.</div>'}
    <h4>Check history</h4><table>${(d.qualifications || []).slice().reverse().map((h) => `<tr><td class="nowrap">${fmtTime(h.checked_at)}</td><td>${pill(h.status)}</td><td class="small">${esc(h.reason)}</td></tr>`).join("")}</table>`;
}
// What the company publishes about itself (techintel company). LinkedIn/Facebook are links only: never fetched.
function companyHtml(co) {
  if (!co) return `<h4>Company</h4><div class="muted">Not read yet. Run <code>uv run python -m techintel company</code>.</div>`;
  const gh = co.github && !co.github.error ? co.github : null;
  const rows = [
    ["Legal name", esc(co.legal_name || "")], ["Founded", esc(co.founded || "")],
    ["Employees", co.employees ? `${esc(co.employees)} <span class="muted small">(schema.org)</span>`
      : co.employees_hint ? `"${esc(co.employees_hint)}" <span class="muted small">(as the site says)</span>` : ""],
    ["Address", esc(co.address || "")],
    ["Email", (co.emails || []).map((e) => `<a href="mailto:${esc(e)}">${esc(e)}</a>`).join(", ")],
    ["Phone", (co.phones || []).map((t) => `<a href="tel:${esc(t)}">${esc(t)}</a>`).join(", ")],
    ["GitHub", gh ? `${link(gh.html_url || co.social.github, gh.login)} · ${fmtNum(gh.public_repos || 0)} repos · ${fmtNum(gh.followers || 0)} followers${gh.created_at ? ` · since ${esc(gh.created_at.slice(0, 4))}` : ""}` : ""],
  ].filter(([, v]) => v);
  const social = Object.entries(co.social || {});
  return `<h4>Company <small class="muted">from its own site, ${fmtDate(co.checked_at)}</small></h4>
    ${rows.length ? `<div class="kv">${rows.map(([k, v]) => `<div>${k}</div><div>${v}</div>`).join("")}</div>` : ""}
    <h4>People</h4>${(co.people || []).length ? `<div class="people-list">${co.people.map((x) => `<div><b>${esc(x.name)}</b> · ${esc(x.role)}</div>`).join("")}</div>`
      : '<div class="muted">None named on its about/team pages.</div>'}
    ${(co.linkedin_people || []).length ? `<div class="small">LinkedIn pages linked from its team page: ${co.linkedin_people.map((u, i) => link(u, String(i + 1))).join(" · ")}</div>` : ""}
    <h4>Social accounts</h4><div>${social.length ? social.map(([k, u]) => link(u, (SOCIAL[k] || [k, k])[1])).join(" · ") : '<span class="muted">none found</span>'}</div>`;
}
function closeDrawer() { $("drawer").classList.remove("open"); $("drawer-bg").classList.remove("open"); }

$("tech-status").addEventListener("click", (e) => {
  const b = e.target.closest("[data-status]"); if (!b) return;
  tech.status = b.dataset.status;
  document.querySelectorAll("#tech-status .chip").forEach((c) => c.classList.toggle("active", c === b));
  loadTechRows(true);
});
$("tech-days").addEventListener("change", (e) => { tech.days = +e.target.value; loadTech(); });
$("tech-category").addEventListener("change", (e) => { tech.category = e.target.value; loadTechRows(true); });
$("tech-sort").addEventListener("change", (e) => { tech.sort = e.target.value; $("tech-table").scrollTop = 0; loadTechRows(true); });
$("tech-source").addEventListener("change", (e) => { tech.source = e.target.value; loadTechRows(true); });
$("tech-company").addEventListener("change", (e) => { tech.company = e.target.value; loadTechRows(true); });
$("tech-q").addEventListener("input", debounce((e) => { tech.q = e.target.value.trim(); loadTechRows(true); }));
$("tech-more").addEventListener("click", () => loadTechRows(false));
// The site list scrolls inside its own box: load the next PAGE by itself near the bottom, so 780 sites
// don't look like the 14 that fit in the box
let techLoading = false;
$("tech-table").addEventListener("scroll", async (e) => {
  const t = e.target;
  if (techLoading || $("tech-more").hidden || t.scrollTop + t.clientHeight < t.scrollHeight - 300) return;
  techLoading = true;
  try { await loadTechRows(false); } finally { techLoading = false; }
});
$("tech-table").addEventListener("click", (e) => {
  if (e.target.closest("a")) return;                     // website links open in a new tab, not the panel
  const tr = e.target.closest("tr[data-domain]"); if (tr) openDomain(tr.dataset.domain);
});
$("drawer-close").addEventListener("click", closeDrawer);
$("drawer-bg").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// ───────────────────────────── MARKETS ─────────────────────────────
const MK_CLASSES = [
  { key: "index", label: "Indices" }, { key: "stock_us", label: "US stocks" }, { key: "stock_lk", label: "Sri Lanka (CSE)" },
  { key: "crypto", label: "Crypto top 100" }, { key: "metals", label: "Gold, silver & oil" }, { key: "fx", label: "Exchange rates" },
];
const mk = { cls: "index", q: "", rows: [], sort: null, summary: null, mode: "table", perf: [],
  cmp: store.get("cmpItems", ["index:^GSPC", "commodity:GC=F", "crypto:BTC", "index:ASPI"]), cmpDays: 30 };
const CMP_PRESETS = [
  ["World indices", ["index:^GSPC", "index:^IXIC", "index:^DJI", "index:ASPI"]],
  ["Sri Lanka", ["index:ASPI", "index:S&P SL20", "fx:LKR=X", "commodity_lkr:GOLD24K_LKR_PAWN"]],
  ["Gold, silver & oil", ["commodity:GC=F", "commodity:SI=F", "commodity:CL=F", "commodity:BZ=F"]],
  ["Safe havens vs risk", ["commodity:GC=F", "index:^GSPC", "crypto:BTC", "index:^VIX"]],
  ["Crypto", ["crypto:BTC", "crypto:ETH", "crypto:SOL", "crypto:XRP", "crypto:BNB"]],
];
const key = (r) => `${r.asset_class}:${r.symbol}`;
const shortName = (r) => ({ "^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^DJI": "Dow", "^NDX": "Nasdaq 100", "^RUT": "Russell 2000",
  "^VIX": "VIX", "GC=F": "Gold", "SI=F": "Silver", "CL=F": "WTI oil", "BZ=F": "Brent oil", "PL=F": "Platinum",
  "LKR=X": "USD/LKR", "GOLD24K_LKR_PAWN": "Gold pawn (LKR)", "ASPI": "ASPI", "S&P SL20": "S&P SL20",
  "DX-Y.NYB": "Dollar index" })[r.symbol] || (r.asset_class === "crypto" ? r.symbol : (r.name || r.symbol));

// A % cell tinted green or red by its size
function pctCell(v) {
  if (v === null || v === undefined) return `<td class="pc muted">–</td>`;
  const a = Math.min(45, Math.abs(v) * 7);
  return `<td class="pc" style="background:color-mix(in srgb, ${v >= 0 ? "var(--ok)" : "var(--bad)"} ${a}%, transparent)">${v > 0 ? "+" : ""}${v.toFixed(2)}%</td>`;
}

async function loadPerformance() {
  const res = await api("/api/markets/performance");
  mk.perf = res.data || [];
  const CLASS = { index: "Index", commodity: "Commodity", commodity_lkr: "In LKR", fx: "Exchange rate", crypto: "Crypto" };
  $("perf-table").innerHTML = mk.perf.length ? `<table class="dense"><thead><tr><th>Name</th><th>Type</th><th class="num">Price</th>
    <th class="num">1 day</th><th class="num">1 week</th><th class="num">1 month</th><th class="num">3 months</th><th style="width:150px">3 months</th></tr></thead>
    <tbody>${mk.perf.map((r) => `<tr class="click" data-cls="${esc(r.asset_class)}" data-sym="${esc(r.symbol)}">
      <td><b>${esc(shortName(r))}</b> <span class="muted small">${esc(r.symbol)}</span></td><td class="muted small">${esc(CLASS[r.asset_class] || r.asset_class)}</td>
      <td class="num nowrap">${fmtPrice(r.price, r.asset_class)} <span class="muted small">${esc(r.currency === "USD" ? "" : r.currency || "")}</span></td>
      ${pctCell(r["1d"])}${pctCell(r["1w"])}${pctCell(r["1m"])}${pctCell(r["3m"])}<td>${sparkSvg(r.spark)}</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty">No market data yet.</div>`;
  $("perf-note").textContent = "history: saved runs + Yahoo daily closes";
  renderPickers();
  await loadCompare();
}

function renderPickers() {
  const known = new Set(mk.perf.map(key));
  $("cmp-presets").innerHTML = `<span class="muted small" style="align-self:center">Quick picks:</span>` + CMP_PRESETS.map(([name, items], i) =>
    `<button class="chip" data-preset="${i}">${esc(name)}</button>`).join("");
  $("cmp-pick").innerHTML = mk.perf.map((r) => {
    const i = mk.cmp.indexOf(key(r));
    return `<button class="chip ${i >= 0 ? "active" : ""}" data-key="${esc(key(r))}" title="${esc(r.name || r.symbol)}">${i >= 0 ? `<i style="background:${color(i)}"></i>` : ""}${esc(shortName(r))}</button>`;
  }).join("");
  mk.cmp = mk.cmp.filter((k) => known.has(k) || !mk.perf.length);
}

let cmpReq = 0;
async function loadCompare() {
  const ticket = ++cmpReq;
  if (!mk.cmp.length) { $("cmp-chart").innerHTML = `<div class="muted small">Pick one or more instruments above.</div>`; return; }
  const res = await api("/api/markets/compare", { items: mk.cmp.join(","), days: mk.cmpDays });
  if (ticket !== cmpReq) return;
  const byKey = Object.fromEntries((res.data || []).map((s) => [key(s), s]));
  const series = mk.cmp.map((k, i) => {
    const s = byKey[k]; if (!s) return { name: k, color: color(i), points: [] };
    const pts = s.points.filter((p) => p.price > 0);
    const base = pts.length ? pts[0].price : 1;
    return { name: shortName(s), color: color(i), points: pts.map((p) => ({ t: Date.parse(p.t), v: (p.price / base - 1) * 100 })) };
  });
  // A line whose history starts later (CSE and crypto prices only build up from our own runs) says so
  const start = Math.min(...series.filter((s) => s.points.length).map((s) => s.points[0].t));
  series.forEach((s) => { if (s.points.length && s.points[0].t - start > 2 * 864e5) s.name += ` (since ${fmtDate(new Date(s.points[0].t).toISOString())})`; });
  lineChart($("cmp-chart"), series, { pct: true, height: 300, empty: "Not enough history for this period yet." });
}

function renderMap() {
  const rows = mk.rows.filter((r) => r.change_pct !== null && r.change_pct !== undefined);
  if (!rows.length) { $("mk-map").innerHTML = `<div class="muted small">No day changes to show for this market.</div>`; return; }
  const caps = rows.map((r) => r.market_cap || 0), maxCap = Math.max(...caps);
  $("mk-map").innerHTML = rows.slice().sort((a, b) => (b.market_cap || 0) - (a.market_cap || 0)).map((r) => {
    const grow = maxCap ? 1 + 9 * Math.sqrt((r.market_cap || 0) / maxCap) : 1;
    const a = Math.min(1, Math.abs(r.change_pct) / 4) * 0.75 + 0.2;
    const bg = r.change_pct > 0 ? `rgba(19,140,72,${a})` : r.change_pct < 0 ? `rgba(196,40,36,${a})` : "rgba(120,128,140,.5)";
    return `<div class="tile" style="flex:${grow.toFixed(2)} 1 ${Math.round(70 * grow ** 0.5)}px;background:${bg}" data-cls="${esc(r.asset_class)}" data-sym="${esc(r.symbol)}"
      title="${esc(qname(r) || r.symbol)}: ${r.change_pct > 0 ? "+" : ""}${r.change_pct.toFixed(2)}%"><b>${esc(r.asset_class === "stock_lk" ? r.symbol.split(".")[0] : r.symbol)}</b>${r.change_pct > 0 ? "+" : ""}${r.change_pct.toFixed(2)}%</div>`;
  }).join("");
}

function fmtPrice(v, cls) {
  if (v === null || v === undefined) return "–";
  const a = Math.abs(v);
  if (a > 0 && a < 1) return Number(v.toPrecision(4)).toString();            // small coins: 4 significant digits
  const d = cls === "fx" && a < 20 ? 4 : 2;                                  // EUR/USD 1.0832; everything else 2
  return v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtBig(v) {
  if (v === null || v === undefined) return "–";
  const a = Math.abs(v);
  for (const [n, s] of [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]]) if (a >= n) return `${(v / n).toFixed(2)}${s}`;
  return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
}
const fmtPct = (v) => (v === null || v === undefined) ? `<span class="muted">–</span>`
  : `<span class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "+" : ""}${v.toFixed(2)}%</span>`;
function sparkSvg(values) {
  const v = (values || []).filter((x) => x !== null && x !== undefined);
  if (v.length < 2) return "";
  const min = Math.min(...v), max = Math.max(...v), span = max - min || 1;
  const pts = v.map((x, i) => `${(i / (v.length - 1)) * 100},${30 - ((x - min) / span) * 28}`).join(" ");
  const trend = v[v.length - 1] > v[0] ? "up" : v[v.length - 1] < v[0] ? "down" : "flat";
  return `<svg class="spark ${trend}" viewBox="0 0 100 32" preserveAspectRatio="none"><polyline points="${pts}" vector-effect="non-scaling-stroke"/></svg>`;
}
const qname = (r) => r.asset_class === "stock_us" && r.name === r.symbol ? r.symbol : r.name;

async function loadMarkets() {
  const res = await api("/api/markets/summary");
  setBanner([res]);
  const s = res.data;
  mk.summary = s;
  if (!s) {
    $("mk-cards").innerHTML = "";
    $("mk-table").innerHTML = `<div class="empty">No market data yet.<br>Run <code>uv run python -m markets run</code>, then refresh this page.</div>`;
    return;
  }
  $("mk-cards").innerHTML = (s.cards || []).map((c) => `
    <div class="card" data-cls="${esc(c.asset_class)}" data-sym="${esc(c.symbol)}">
      <div class="nm" title="${esc(c.name)}">${esc(c.name)}</div>
      <div class="px">${fmtPrice(c.price, c.asset_class)} <small class="muted">${esc(c.currency === "USD" ? "" : c.currency || "")}</small></div>
      <div class="ch">${fmtPct(c.change_pct)}</div>${sparkSvg(c.spark)}</div>`).join("");
  const counts = Object.fromEntries((s.counts || []).map((r) => [r.asset_class, r.n]));
  counts.metals = (counts.commodity || 0) + (counts.commodity_lkr || 0);
  $("mk-classes").innerHTML = MK_CLASSES.map((c) => `<button class="chip ${mk.cls === c.key ? "active" : ""}" data-cls="${c.key}">${c.label}<small>${counts[c.key] || 0}</small></button>`).join(" ");
  $("mk-asof").textContent = s.last_run && s.last_run.run_at ? `Last update ${fmtTime(s.last_run.run_at)} (${ago(s.last_run.run_at)})` : "";
  const src = s.sources || [];
  const bad = src.filter((x) => !x.ok);
  $("mk-sources").innerHTML = src.length ? `<div class="small muted">${src.length - bad.length}/${src.length} OK</div>` + (bad.length
    ? `<table>${bad.map((x) => `<tr><td>${esc(x.source)}</td><td>${pill("failed")}<div class="muted small">${esc((x.error || "").slice(0, 100))}</div></td></tr>`).join("")}</table>`
    : `<div class="muted small">All sources returned data.</div>`) : "";
  const [mn, cn] = await Promise.all([api("/api/news/items", { group: "MARKETS", days: 3, limit: 10 }), api("/api/news/items", { group: "CRYPTO", days: 3, limit: 10 })]);
  const newsList = (r) => ((r.data || {}).items || []).map((i) => `<div class="news-item"><div>${link(i.url, i.title)}</div>
    <div class="meta">${esc(ago(i.published_at || i.first_seen_at))} · ${esc(i.source)}</div></div>`).join("")
    || `<div class="muted small">No ${r === cn ? "crypto" : "market"} news yet. Run <code>uv run python -m worldnews run --group MARKETS --group CRYPTO</code>.</div>`;
  $("mk-news").innerHTML = newsList(mn);
  $("mk-crypto-news").innerHTML = newsList(cn);
  await Promise.all([loadMarketRows(), loadPerformance()]);
}

let mkReq = 0;
async function loadMarketRows() {
  const ticket = ++mkReq;
  const classes = mk.cls === "metals" ? ["commodity", "commodity_lkr"] : [mk.cls];
  const results = await Promise.all(classes.map((c) => api("/api/markets/quotes", { class: c, q: mk.q })));
  if (ticket !== mkReq) return;
  mk.rows = results.flatMap((r) => r.data || []);
  renderMarketTable();
  $("mk-map").hidden = mk.mode !== "map"; $("mk-table").hidden = mk.mode === "map";
  if (mk.mode === "map") renderMap();
  const movers = mk.rows.filter((r) => r.change_pct !== null && (r.asset_class !== "stock_lk" || (r.volume || 0) > 0));
  const label = (MK_CLASSES.find((c) => c.key === mk.cls) || {}).label || "";
  $("mk-movers-label").textContent = label;
  const moverList = (list) => list.map((r) => `<div class="news-item" data-cls="${esc(r.asset_class)}" data-sym="${esc(r.symbol)}" style="cursor:pointer">
    <b>${esc(r.symbol)}</b> ${fmtPct(r.change_pct)} <span class="muted small">${esc((qname(r) || "").slice(0, 32))} · ${fmtPrice(r.price, r.asset_class)}</span></div>`).join("")
    || `<div class="muted small">No data.</div>`;
  const sorted = movers.slice().sort((a, b) => b.change_pct - a.change_pct);
  $("mk-gainers").innerHTML = moverList(sorted.filter((r) => r.change_pct > 0).slice(0, 6));
  $("mk-losers").innerHTML = moverList(sorted.filter((r) => r.change_pct < 0).reverse().slice(0, 6));
}

const MK_COLS = {
  crypto: [["rank", "#"], ["name", "Coin"], ["price", "Price (USD)"], ["pct_1h", "1h"], ["change_pct", "24h"], ["pct_7d", "7d"], ["market_cap", "Market cap"], ["volume", "Volume 24h"]],
  stock_lk: [["symbol", "Symbol"], ["name", "Company"], ["price", "Price (LKR)"], ["change", "Change"], ["change_pct", "%"], ["volume", "Volume"], ["turnover", "Turnover"], ["market_cap", "Market cap"]],
  stock_us: [["symbol", "Symbol"], ["name", "Name"], ["price", "Price"], ["change", "Change"], ["change_pct", "%"], ["volume", "Volume"], ["as_of", "As of"]],
  default: [["name", "Name"], ["symbol", "Symbol"], ["price", "Price"], ["currency", ""], ["change", "Change"], ["change_pct", "%"], ["as_of", "As of"]],
};
function renderMarketTable() {
  const cols = MK_COLS[mk.cls] || MK_COLS.default;
  let rows = mk.rows.slice();
  if (mk.sort) {
    const { key, dir } = mk.sort;
    rows.sort((a, b) => { const x = a[key], y = b[key];
      if (x === null || x === undefined) return 1; if (y === null || y === undefined) return -1;
      return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir; });
  }
  const label = (MK_CLASSES.find((c) => c.key === mk.cls) || {}).label || "Prices";
  $("mk-title").textContent = label;
  $("mk-count").textContent = `${rows.length} · click a row for its price history · click a column to sort`;
  const cell = (r, k) => {
    if (k === "name" && r.asset_class === "crypto") return `<td class="coin">${r.image ? `<img src="${esc(r.image)}" alt="">` : ""}${esc(r.name)} <span class="muted">${esc(r.symbol)}</span></td>`;
    if (["change_pct", "pct_1h", "pct_7d"].includes(k)) return `<td class="num">${fmtPct(r[k])}</td>`;
    if (k === "change") return `<td class="num ${r.change > 0 ? "up" : r.change < 0 ? "down" : ""}">${r.change === null ? "–" : (r.change > 0 ? "+" : "") + fmtPrice(r.change, r.asset_class)}</td>`;
    if (k === "price") return `<td class="num"><b>${fmtPrice(r.price, r.asset_class)}</b></td>`;
    if (["volume", "market_cap", "turnover"].includes(k)) return `<td class="num">${fmtBig(r[k])}</td>`;
    if (k === "as_of") return `<td class="nowrap muted small">${esc(fmtTime(r.as_of))}</td>`;
    if (k === "rank") return `<td class="num muted">${r.rank ?? ""}</td>`;
    if (k === "symbol") return `<td class="nowrap"><b>${esc(r.symbol)}</b></td>`;
    return `<td>${esc(r[k] ?? "")}</td>`;
  };
  $("mk-table").innerHTML = rows.length ? `<table class="list"><tr>${cols.map(([k, l]) => `<th class="sort ${mk.sort && mk.sort.key === k ? "sorted" : ""} ${["price", "change", "change_pct", "pct_1h", "pct_7d", "volume", "market_cap", "turnover", "rank"].includes(k) ? "num" : ""}" data-key="${k}">${esc(l)}${mk.sort && mk.sort.key === k ? (mk.sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr>
    ${rows.map((r) => `<tr class="click" data-cls="${esc(r.asset_class)}" data-sym="${esc(r.symbol)}">${cols.map(([k]) => cell(r, k)).join("")}</tr>`).join("")}</table>`
    : `<div class="empty">No prices for this market yet.</div>`;
  const notes = [...new Set(mk.rows.map((r) => r.note).filter(Boolean))];
  $("mk-note").textContent = notes.length ? `LKR gold/silver: ${notes[0]}.` : (mk.cls === "metals" ? "Gold and silver in USD per troy ounce (COMEX futures from Yahoo, or spot price from Stooq; click a row to see the source)." : "");
}

async function openSymbol(cls, sym) {
  $("drawer-body").innerHTML = `<div class="muted">Loading ${esc(sym)}…</div>`;
  $("drawer").classList.add("open"); $("drawer-bg").classList.add("open");
  const res = await api("/api/markets/history", { class: cls, symbol: sym, days: 100 });
  const d = res.data || {}, q = d.quote || {};
  const pts = (d.points || []).filter((p) => p.price !== null).map((p) => ({ t: Date.parse(p.t), v: p.price }));
  const chart = `<div class="seg" id="sym-range" style="margin:6px 0">${[[1, "1D"], [7, "1W"], [30, "1M"], [100, "3M"]].map(([n, l]) =>
    `<button data-d="${n}" class="${n === 30 ? "active" : ""}">${l}</button>`).join("")}</div>
    <div id="sym-chart" class="chart-box"></div><div class="muted small" id="sym-stats"></div>`;
  $("drawer-body").innerHTML = `
    <h2>${esc(qname(q) || sym)}</h2><div class="muted">${esc(sym)} · ${esc(q.asset_class || cls)} · ${esc(q.source || "")}</div>
    <p style="font-size:24px;font-weight:700;margin:8px 0">${fmtPrice(q.price, cls)} <small class="muted">${esc(q.currency || "")}</small> ${fmtPct(q.change_pct)}</p>
    ${chart}
    <h4>Details</h4><div class="kv">
      <div>Previous close</div><div>${fmtPrice(q.prev_close, cls)}</div><div>Change</div><div>${q.change === null || q.change === undefined ? "–" : fmtPrice(q.change, cls)}</div>
      ${q.high ? `<div>Day high / low</div><div>${fmtPrice(q.high, cls)} / ${fmtPrice(q.low, cls)}</div>` : ""}
      ${q.volume ? `<div>Volume</div><div>${fmtBig(q.volume)}</div>` : ""}${q.market_cap ? `<div>Market cap</div><div>${fmtBig(q.market_cap)}</div>` : ""}
      ${q.rank ? `<div>Rank</div><div>#${q.rank}</div>` : ""}${q.pct_7d !== null && q.pct_7d !== undefined ? `<div>7 days</div><div>${fmtPct(q.pct_7d)}</div>` : ""}
      <div>Price time</div><div>${esc(fmtTime(q.as_of))}</div><div>Collected</div><div>${esc(fmtTime(q.run_at))}</div>
      ${q.note ? `<div>Note</div><div>${esc(q.note)}</div>` : ""}</div>`;
  const draw = (days) => {
    const since = Date.now() - days * 864e5, p = pts.filter((x) => x.t >= since);
    lineChart($("sym-chart"), [{ name: sym, color: "var(--c1)", points: p }],
      { height: 240, fmt: (v) => fmtPrice(v, cls), empty: "Not enough history for this period yet: each run adds a point." });
    if (p.length >= 2) {
      const vals = p.map((x) => x.v), ch = (vals[vals.length - 1] / vals[0] - 1) * 100;
      $("sym-stats").innerHTML = `${p.length} points · high ${fmtPrice(Math.max(...vals), cls)} · low ${fmtPrice(Math.min(...vals), cls)} · change ${fmtPct(ch)}`;
    } else $("sym-stats").textContent = "";
  };
  draw(30);
  $("sym-range").addEventListener("click", (e) => {
    const b = e.target.closest("[data-d]"); if (!b) return;
    document.querySelectorAll("#sym-range button").forEach((x) => x.classList.toggle("active", x === b));
    draw(+b.dataset.d);
  });
}

$("mk-classes").addEventListener("click", (e) => {
  const b = e.target.closest("[data-cls]"); if (!b) return;
  mk.cls = b.dataset.cls; mk.sort = null;
  document.querySelectorAll("#mk-classes .chip").forEach((c) => c.classList.toggle("active", c === b));
  loadMarketRows();
});
$("mk-q").addEventListener("input", debounce((e) => { mk.q = e.target.value.trim(); loadMarketRows(); }));
$("mk-table").addEventListener("click", (e) => {
  const th = e.target.closest("th.sort");
  if (th) { const k = th.dataset.key; mk.sort = mk.sort && mk.sort.key === k ? { key: k, dir: -mk.sort.dir } : { key: k, dir: ["name", "symbol", "currency"].includes(k) ? 1 : -1 }; renderMarketTable(); return; }
  const tr = e.target.closest("tr[data-sym]"); if (tr) openSymbol(tr.dataset.cls, tr.dataset.sym);
});
$("mk-mode").addEventListener("click", (e) => {
  const b = e.target.closest("[data-m]"); if (!b) return;
  mk.mode = b.dataset.m;
  document.querySelectorAll("#mk-mode button").forEach((x) => x.classList.toggle("active", x === b));
  $("mk-map").hidden = mk.mode !== "map"; $("mk-table").hidden = mk.mode === "map";
  if (mk.mode === "map") renderMap();
});
$("cmp-range").addEventListener("click", (e) => {
  const b = e.target.closest("[data-d]"); if (!b) return;
  mk.cmpDays = +b.dataset.d;
  document.querySelectorAll("#cmp-range button").forEach((x) => x.classList.toggle("active", x === b));
  loadCompare();
});
$("cmp-pick").addEventListener("click", (e) => {
  const b = e.target.closest("[data-key]"); if (!b) return;
  const k = b.dataset.key, i = mk.cmp.indexOf(k);
  if (i >= 0) mk.cmp.splice(i, 1); else if (mk.cmp.length < 8) mk.cmp.push(k);
  store.set("cmpItems", mk.cmp); renderPickers(); loadCompare();
});
$("cmp-presets").addEventListener("click", (e) => {
  const b = e.target.closest("[data-preset]"); if (!b) return;
  const known = new Set(mk.perf.map(key));
  mk.cmp = CMP_PRESETS[+b.dataset.preset][1].filter((k) => known.has(k));
  store.set("cmpItems", mk.cmp); renderPickers(); loadCompare();
});
for (const id of ["mk-cards", "mk-gainers", "mk-losers", "perf-table", "mk-map"]) {
  $(id).addEventListener("click", (e) => { const c = e.target.closest("[data-sym]"); if (c) openSymbol(c.dataset.cls, c.dataset.sym); });
}

// ───────────────────────────── VEHICLES ─────────────────────────────
const vh = { days: 30, site: "", q: "", new: false, offset: 0 };
const lkr = (v) => (v === null || v === undefined) ? "–" : `Rs ${Math.round(v).toLocaleString()}`;
const lkrShort = (v) => v >= 1e6 ? `Rs ${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `Rs ${Math.round(v / 1e3)}K` : `Rs ${v}`;

async function loadVehicles() {
  const [summary, changes] = await Promise.all([api("/api/vehicles/summary"), api("/api/vehicles/changes")]);
  setBanner([summary, changes]);
  const s = summary.data;
  if (!s) {
    $("vh-kpis").innerHTML = "";
    $("vh-list").innerHTML = `<div class="empty">No vehicle ads yet.<br>Run <code>./run_all.sh vehicles</code> (or <code>uv run python -m vehicles run</code>), then refresh.</div>`;
    return;
  }
  const c = s.counts;
  $("vh-kpis").innerHTML = [[fmtNum(c.ads), "Ads tracked"], [fmtNum(c.new_last_run), "New in last run"], [fmtNum(c.new_24h), "New in 24 hours"],
    [fmtNum(c.price_drops), "Price drops (7 days)"], [lkr(c.median_price), "Median price"]]
    .map(([v, l]) => `<div class="kpi"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join("");
  $("vh-sites").innerHTML = [`<button class="chip ${vh.site ? "" : "active"}" data-site="">All<small>${c.ads}</small></button>`]
    .concat((s.sites || []).map((x) => `<button class="chip ${vh.site === x.site ? "active" : ""}" data-site="${esc(x.site)}">${esc(x.site)}<small>${x.ads}</small></button>`)).join(" ");
  $("vh-site-table").innerHTML = `<table><tr><th>Site</th><th class="num">Ads</th><th class="num">New</th><th>Last collected</th></tr>${(s.sites || []).map((x) => `
    <tr><td>${esc(x.site)}</td><td class="num">${x.ads}</td><td class="num">${x.new_last_run}</td><td class="nowrap">${esc(fmtTime(x.last_saved))}<div class="muted small">${esc(ago(x.last_saved))}</div></td></tr>`).join("")}</table>`;
  dayColumns($("vh-days-chart"), s.by_day, "n");
  bars($("vh-prices"), s.price_ranges, "range", "n");
  bars($("vh-makes"), s.makes, "make", "n", (r) => `${r.make}${r.median_price ? ` · ${lkrShort(r.median_price)}` : ""}`);
  $("vh-changes").innerHTML = (changes.data || []).length ? changes.data.slice(0, 15).map((x) => {
    const down = x.price_lkr < x.previous_price_lkr;
    return `<div class="news-item"><div>${link(x.url, x.title)}</div><div class="meta"><span class="${down ? "up" : "down"}">${down ? "▼" : "▲"} ${lkr(x.previous_price_lkr)} → ${lkr(x.price_lkr)}</span> · ${esc(x.site)} · ${esc(ago(x.changed_at))}</div></div>`;
  }).join("") : `<div class="muted small">No price changes seen yet. They appear when an ad is seen again at a different price.</div>`;
  await loadVehicleAds(true);
}

let vhReq = 0;
async function loadVehicleAds(reset) {
  const ticket = ++vhReq;
  const offset = reset ? 0 : vh.offset;
  const res = await api("/api/vehicles/ads", { days: vh.days, site: vh.site, q: vh.q, new: vh.new ? 1 : "", limit: PAGE, offset });
  if (ticket !== vhReq) return;
  const d = res.data || { total: 0, items: [] };
  $("vh-count").textContent = shown(d.total, offset + d.items.length, "newest first");
  const html = d.items.map((a) => {
    const drop = a.previous_price_lkr && a.price_lkr !== null && a.price_lkr !== a.previous_price_lkr;
    const meta = [a.location, a.year, a.mileage].filter(Boolean).join(" · ");
    return `<div class="ad">${a.image ? `<img src="${esc(a.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : `<div class="noimg"></div>`}
      <div><div class="t">${a.is_new ? `<span class="badge b-new">NEW</span>` : ""}${link(a.url, a.title)}</div>
        <div class="m">${esc(meta || a.posted || "")}</div>
        <div class="m">${esc(a.site)} · first seen ${esc(fmtTime(a.first_seen))} (${esc(ago(a.first_seen))})${a.times_seen > 1 ? ` · seen in ${a.times_seen} runs` : ""}</div></div>
      <div class="p">${a.price_lkr ? lkr(a.price_lkr) : esc(a.price || "–")}${drop ? `<div class="small ${a.price_lkr < a.previous_price_lkr ? "up" : "down"}">was ${lkr(a.previous_price_lkr)}</div>` : ""}</div></div>`;
  }).join("");
  if (reset) { vh.offset = 0; $("vh-list").innerHTML = html || `<div class="empty">No ads match these filters.</div>`; }
  else $("vh-list").insertAdjacentHTML("beforeend", html);
  vh.offset = offset + d.items.length;
  $("vh-more").hidden = vh.offset >= d.total;
}

$("vh-sites").addEventListener("click", (e) => {
  const b = e.target.closest("[data-site]"); if (!b) return;
  vh.site = b.dataset.site;
  document.querySelectorAll("#vh-sites .chip").forEach((c) => c.classList.toggle("active", c === b));
  loadVehicleAds(true);
});
$("vh-days").addEventListener("change", (e) => { vh.days = +e.target.value; loadVehicleAds(true); });
$("vh-new").addEventListener("change", (e) => { vh.new = e.target.checked; loadVehicleAds(true); });
$("vh-q").addEventListener("input", debounce((e) => { vh.q = e.target.value.trim(); loadVehicleAds(true); }));
$("vh-more").addEventListener("click", () => loadVehicleAds(false));

// ───────────────────────────── COMPANIES ─────────────────────────────
// python -m companies: collect (names, websites, countries from open data) and update (each website read)
const co = { country: "", source: "", status: "", q: "", sort: "best", offset: 0, rows: [] };
const SITE_STATUS = { ok: "read", "not read": "not read", "no website": "no website", unreachable: "unreachable",
  not_allowed: "robots.txt: no" };
const SOURCE_NAME = { wikidata: "Wikidata", gleif: "GLEIF (LEI)", cse: "CSE (Sri Lanka)", companies_house: "Companies House (UK)",
  asic: "ASIC (Australia)", sec: "SEC EDGAR (US)" };

async function loadCompanies() {
  const summary = await api("/api/companies/summary");
  setBanner([summary]);
  const s = summary.data;
  if (!s || !s.counts.companies) {
    $("co-kpis").innerHTML = "";
    $("co-table").innerHTML = `<div class="empty">No companies yet.<br>Run <code>uv run python -m companies collect --max-minutes 5</code>, then refresh this page.</div>`;
    return;
  }
  const c = s.counts;
  $("co-kpis").innerHTML = [[fmtNum(c.companies), "Companies"], [fmtNum(c.with_website), "With a website"],
    [fmtNum(c.sites_ok), "Websites read"], [fmtNum(c.sites_with_social), "With social links"],
    [fmtNum(c.countries), "Countries"], [fmtNum(c.new_24h), "Added in 24 hours"]]
    .map(([v, l]) => `<div class="kpi"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join("");
  const sel = $("co-country");
  sel.innerHTML = `<option value="">All countries (${fmtNum(c.companies)})</option>` + s.by_country.filter((r) => r.country)
    .map((r) => `<option value="${esc(r.country)}">${esc(r.name)} · ${fmtNum(r.n)}</option>`).join("");
  sel.value = co.country;
  $("co-sources").innerHTML = [`<button class="chip ${co.source ? "" : "active"}" data-source="">All<small>${fmtNum(c.companies)}</small></button>`]
    .concat(s.by_source.map((x) => `<button class="chip ${co.source === x.source ? "active" : ""}" data-source="${esc(x.source)}">${esc(SOURCE_NAME[x.source] || x.source)}<small>${fmtNum(x.n)}</small></button>`)).join(" ");
  dayColumns($("co-days-chart"), s.by_day, "n", "with_website", "with a website");
  const top = s.by_country.slice(0, 12), max = Math.max(1, ...top.map((r) => r.n));
  $("co-by-country").innerHTML = top.map((r) => `<div class="row" data-country="${esc(r.country || "")}" title="${esc(r.name)}: ${fmtNum(r.n)} companies, ${fmtNum(r.with_website)} with a website, ${fmtNum(r.sites_ok)} read">
    <span>${esc(r.name)}</span><i style="width:${Math.max(2, (100 * r.n) / max)}%"></i><span class="n">${fmtNum(r.n)}</span></div>`).join("");
  bars($("co-by-status"), s.by_status, "status", "n", (r) => SITE_STATUS[r.status] || r.status);
  bars($("co-social"), s.social.slice(0, 10), "platform", "n", (r) => (SOCIAL[r.platform] || [0, r.platform])[1]);
  bars($("co-industries"), s.industries, "industry", "n");
  $("co-source-table").innerHTML = `<table><tr><th>Source</th><th class="num">Companies</th><th class="num">Websites</th><th class="num">Countries</th></tr>${s.by_source.map((x) => `
    <tr><td>${esc(SOURCE_NAME[x.source] || x.source)}<div class="muted small">last ${esc(ago(x.last_seen))}</div></td><td class="num">${fmtNum(x.n)}</td>
    <td class="num">${fmtNum(x.with_website)}</td><td class="num">${x.countries}</td></tr>`).join("")}</table>`;
  $("co-progress").innerHTML = s.progress.length ? `<table>${s.progress.map((p) => `<tr><td>${esc(SOURCE_NAME[p.source] || p.source)}</td>
    <td class="num">${p.done} / ${p.parts} done</td></tr>`).join("")}</table><div class="muted small">Each run continues where the last one stopped.</div>`
    : `<div class="muted small">No runs yet.</div>`;
  $("co-runs").innerHTML = s.runs.length ? `<table>${s.runs.map((r) => `<tr><td class="nowrap">${esc(fmtTime(r.started_at))}</td>
    <td>${esc(r.command)}</td><td class="small">${esc(r.text)}${r.problems ? `<div class="down">${esc(r.problems)}</div>` : ""}</td></tr>`).join("")}</table>` : "";
  await loadCompanyRows(true);
}

let coReq = 0;
async function loadCompanyRows(reset) {
  const ticket = ++coReq;
  const offset = reset ? 0 : co.offset;
  const res = await api("/api/companies/list", { country: co.country, source: co.source, status: co.status, q: co.q,
    sort: co.sort, limit: PAGE, offset });
  if (ticket !== coReq) return;
  if (reset) { co.offset = 0; co.rows = []; }
  const d = res.data || { total: 0, items: [] };
  co.rows = co.rows.concat(d.items);
  $("co-count").textContent = shown(d.total, co.rows.length,
    co.rows.length < d.total ? "scroll the list for more · click a row for details" : "click a row for details");
  $("co-table").innerHTML = co.rows.length ? `<table class="dense"><thead><tr><th>#</th><th>Company</th><th>Country</th>
    <th>Industry</th><th>Website</th><th>Social</th><th>Source</th></tr></thead><tbody>${co.rows.map((r, n) => `
    <tr class="click" data-id="${esc(r.id)}"><td class="num muted">${n + 1}</td>
    <td class="site"><div class="nm">${esc(r.name)}</div>${r.website ? `<div>${link(r.website, r.domain || r.website)}</div>` : ""}</td>
    <td class="nowrap" title="${esc(r.country_name || "")}">${esc(r.country || "–")}</td>
    <td class="small"><div class="ds" style="max-width:30ch" title="${esc(r.industry || "")}">${esc(r.industry || "")}</div></td>
    <td>${pill(SITE_STATUS[r.site_status] || r.site_status)}</td>
    <td class="nowrap small">${socialLinks(r.social)}${r.people ? ` <span title="people named on its site">👤${r.people}</span>` : ""}${r.employees
      ? ` <span class="muted" title="employees, as the site says">${esc(String(r.employees).slice(0, 18))}</span>` : ""}</td>
    <td class="small nowrap">${esc(SOURCE_NAME[r.source] || r.source)}</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty">No companies match these filters.</div>`;
  co.offset += d.items.length;
  $("co-more").hidden = co.offset >= d.total;
}

async function openCompany(id) {
  $("drawer-body").innerHTML = `<div class="muted">Loading…</div>`;
  $("drawer").classList.add("open"); $("drawer-bg").classList.add("open");
  const d = (await api(`/api/companies/company/${encodeURIComponent(id)}`)).data || {};
  const c = d.company;
  if (!c) { $("drawer-body").innerHTML = `<div class="muted">Not found.</div>`; return; }
  const ex = c.extra || {};
  const LABEL = { type: "Type (Wikidata)", wikidata: "Wikidata", city: "City", legal_form: "Legal form", registered_as: "Registration no.",
    jurisdiction: "Jurisdiction", created: "Created", symbol: "CSE symbol", established: "Established", board: "Board",
    address: "Registered office", business: "Business", status: "Status", category: "Category", incorporated: "Incorporated",
    town: "Town", postcode: "Postcode", companies_house: "Companies House", abn: "ABN", company_class: "Class",
    registered: "Registered", state: "State", ticker: "Ticker", edgar: "SEC EDGAR" };
  const facts = Object.entries(ex).map(([k, v]) => [LABEL[k] || k, /^https?:\/\//.test(String(v)) ? link(String(v)) : esc(v)]);
  const site = d.site;
  let siteHtml;
  if (!c.website) siteHtml = `<h4>Website</h4><div class="muted">This source gives no website for it.</div>`;
  else if (!site) siteHtml = `<h4>Website</h4><div class="muted">${c.refusal ? `The site refused us (${esc(c.refusal.error)}, ${esc(fmtDate(c.refusal.refused_at))}); it is tried again later.`
    : "Not read yet. Run <code>uv run python -m companies update</code>."}</div>`;
  else if (site.status !== "ok") siteHtml = `<h4>Website</h4><div>${pill(SITE_STATUS[site.status] || site.status)} <span class="muted small">${esc(site.error || "")} · ${esc(fmtDate(site.checked_at))}</span></div>`;
  else siteHtml = companyHtml(site).replace("from its own site,", "from its own website,")
    + `<div class="muted small" style="margin-top:6px">Pages read: ${(site.pages || []).map((u) => link(u, new URL(u).pathname || "/")).join(" · ")}</div>`;
  $("drawer-body").innerHTML = `
    <h2>${esc(c.name)}</h2>
    <div>${c.website ? link(c.website) : ""} ${pill(SITE_STATUS[site ? site.status : c.website ? "not read" : "no website"] || "")}</div>
    <h4>Details</h4><div class="kv">
      <div>Country</div><div>${esc(c.country_name || c.country || "—")}</div>
      ${c.industry ? `<div>Industry</div><div>${esc(c.industry)}</div>` : ""}
      <div>Source</div><div>${esc(SOURCE_NAME[c.source] || c.source)} · ${esc(c.source_id)}</div>
      ${facts.map(([k, v]) => `<div>${esc(k)}</div><div>${v}</div>`).join("")}
      <div>First seen</div><div>${esc(fmtTime(c.first_seen))}</div>
    </div>
    ${siteHtml}
    ${(d.same_website || []).length ? `<h4>Same website in other records</h4>${d.same_website.map((x) => `<div><a href="#" data-co="${esc(x.id)}">${esc(x.name)}</a> <span class="muted small">${esc(SOURCE_NAME[x.source] || x.source)} · ${esc(x.country || "")}</span></div>`).join("")}` : ""}`;
}

$("co-country").addEventListener("change", (e) => { co.country = e.target.value; loadCompanyRows(true); });
$("co-status").addEventListener("change", (e) => { co.status = e.target.value; loadCompanyRows(true); });
$("co-sort").addEventListener("change", (e) => { co.sort = e.target.value; $("co-table").scrollTop = 0; loadCompanyRows(true); });
$("co-q").addEventListener("input", debounce((e) => { co.q = e.target.value.trim(); loadCompanyRows(true); }));
$("co-more").addEventListener("click", () => loadCompanyRows(false));
$("co-sources").addEventListener("click", (e) => {
  const b = e.target.closest("[data-source]"); if (!b) return;
  co.source = b.dataset.source;
  document.querySelectorAll("#co-sources .chip").forEach((x) => x.classList.toggle("active", x === b));
  loadCompanyRows(true);
});
$("co-by-country").addEventListener("click", (e) => {
  const row = e.target.closest("[data-country]"); if (!row) return;
  co.country = co.country === row.dataset.country ? "" : row.dataset.country;
  $("co-country").value = co.country; loadCompanyRows(true);
});
let coLoading = false;
$("co-table").addEventListener("scroll", async (e) => {
  const t = e.target;
  if (coLoading || $("co-more").hidden || t.scrollTop + t.clientHeight < t.scrollHeight - 300) return;
  coLoading = true;
  try { await loadCompanyRows(false); } finally { coLoading = false; }
});
$("co-table").addEventListener("click", (e) => {
  if (e.target.closest("a")) return;
  const tr = e.target.closest("tr[data-id]"); if (tr) openCompany(tr.dataset.id);
});
$("drawer-body").addEventListener("click", (e) => {
  const a = e.target.closest("a[data-co]"); if (!a) return;
  e.preventDefault(); openCompany(a.dataset.co);
});

// ───────────────────────────── shell ─────────────────────────────
let view = "news";
async function loadAutomation() {
  try {
    const runs = (await api("/api/automation")).runs || [];
    const last = runs.find((r) => !r.skipped);
    $("lastrun").innerHTML = last
      ? `Last collection run: ${esc(last.when.slice(5, 16))} ${pill(last.ok ? "ok" : "failed")}`
      : "";
    $("lastrun").title = runs.map((r) => `${r.when}  ${r.text}`).join("\n");
  } catch { /* the dashboard still works without it */ }
}

async function refresh() {
  loadAutomation();
  try {
    await ({ news: loadNews, tech: loadTech, markets: loadMarkets, vehicles: loadVehicles, companies: loadCompanies })[view]();
    $("updated").textContent = `Updated ${fmtTime(new Date().toISOString())}`;
  } catch (err) {
    showLoadError(err);
  }
}
function showLoadError(err) {
  const outdated = / HTTP 404$/.test(err.message);     // page is newer than the running server
  setBanner([{ error: outdated
    ? `Could not load data: ${err.message}. The dashboard server is running older code than this page `
      + "(the project was updated after it started). Restart it: press Ctrl+C in its Terminal window, "
      + "then run: uv run python -m dashboard --open"
    : `Could not load data: ${err.message}. Is the dashboard server still running?` }]);
}
const VIEWS = ["news", "tech", "markets", "vehicles", "companies"];
function show(v) {
  view = VIEWS.includes(v) ? v : "news";
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  VIEWS.forEach((n) => { $(`view-${n}`).hidden = view !== n; });
  if (location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
  refresh();
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => show(t.dataset.view)));
window.addEventListener("hashchange", () => show(location.hash.slice(1)));
$("refresh").addEventListener("click", refresh);
function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen().catch(() => { /* not allowed here: the page is full width anyway */ });
}
$("fs").addEventListener("click", toggleFullscreen);
document.addEventListener("keydown", (e) => {
  if ((e.key === "f" || e.key === "F") && !e.metaKey && !e.ctrlKey && !e.altKey && !/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) toggleFullscreen();
});
document.addEventListener("fullscreenchange", () => { $("fs").textContent = document.fullscreenElement ? "✕" : "⛶"; });

let timer = null;
function setAuto(on) {
  $("auto").checked = on; store.set("autoRefresh", on);
  clearInterval(timer); timer = on ? setInterval(refresh, 60000) : null;
}
$("auto").addEventListener("change", (e) => setAuto(e.target.checked));
setAuto(store.get("autoRefresh", false));
show(location.hash.slice(1) || "news");
