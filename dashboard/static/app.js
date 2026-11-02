// Intelligence Dashboard: World News + Tech Intel, reading from the local DuckDB-backed API.
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(2, "0");
const PAGE = 100;

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

function dayColumns(el, rows, total, part) {
  const days = [];
  const today = new Date();
  for (let i = 13; i >= 0; i--) {
    const d = new Date(today); d.setDate(today.getDate() - i);
    days.push(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`);
  }
  const byDay = Object.fromEntries((rows || []).map((r) => [r.day, r]));
  const max = Math.max(1, ...days.map((d) => byDay[d]?.[total] || 0));
  el.innerHTML = `<div class="cols">${days.map((d) => {
    const r = byDay[d] || {}; const t = r[total] || 0; const p = part ? r[part] || 0 : 0;
    return `<div class="c" title="${d}: ${t}${part ? ` (${p} tech)` : ""}">
      ${part ? `<i class="t" style="height:${(100 * p) / max}%"></i>` : ""}<i style="height:${(100 * (t - p)) / max}%"></i><em>${d.slice(8)}</em></div>`;
  }).join("")}</div>`;
}

function setBanner(results) {
  const busy = results.some((r) => r && r.busy);
  const err = results.map((r) => r && r.error).find(Boolean);
  const b = $("banner");
  b.hidden = !busy && !err;
  b.textContent = err || (busy ? "A collection run is writing to the database right now; showing the last loaded data. It will update when the run finishes." : "");
}

const debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// ───────────────────────────── NEWS ─────────────────────────────
const news = { days: 7, group: "", source: "", tech: false, new: false, q: "", offset: 0, groups: [] };
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
}

let newsReq = 0;
async function loadNewsItems(reset) {
  if (reset) news.offset = 0;
  const ticket = ++newsReq;                                // ignore answers to older requests
  const res = await api("/api/news/items", { days: news.days, group: news.group, source: news.source,
    tech: news.tech ? 1 : "", new: news.new ? 1 : "", q: news.q, limit: PAGE, offset: news.offset });
  if (ticket !== newsReq) return;
  const d = res.data || { total: 0, items: [] };
  $("news-count").textContent = `${fmtNum(d.total)} · newest first`;
  const html = d.items.map((i) => {
    const when = i.published_at || i.first_seen_at;
    return `<div class="news-item">
      <div class="title">${i.is_new ? `<span class="badge b-new">NEW</span>` : ""}${link(i.url, i.title)}${(i.topics || []).includes("tech") && i.group_code !== "TECH" ? ` <span class="badge b-tech">tech</span>` : ""}</div>
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
});
$("news-days").addEventListener("change", (e) => { news.days = +e.target.value; loadNews(); });
$("news-source").addEventListener("change", (e) => { news.source = e.target.value; loadNewsItems(true); });
$("news-tech").addEventListener("change", (e) => { news.tech = e.target.checked; loadNewsItems(true); });
$("news-new").addEventListener("change", (e) => { news.new = e.target.checked; loadNewsItems(true); });
$("news-q").addEventListener("input", debounce((e) => { news.q = e.target.value.trim(); loadNewsItems(true); }));
$("news-more").addEventListener("click", () => loadNewsItems(false));

// ───────────────────────────── TECH ─────────────────────────────
const tech = { days: 30, status: "", category: "", source: "", q: "", offset: 0, rows: [] };

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
    [fmtNum(c.changes), `Changes, ${periodLabel}`], [fmtNum(c.candidates), "Domains (all time)"],
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

  dayColumns($("tech-days-chart"), s.by_day, "n");
  bars($("tech-by-category"), s.by_category, "category", "n");
  bars($("tech-stack"), s.tech_stack, "tech", "n");
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
    source: tech.source, q: tech.q, limit: PAGE, offset });
  if (ticket !== techReq) return;
  if (reset) { tech.offset = 0; tech.rows = []; }
  const d = res.data || { total: 0, items: [] };
  tech.rows = tech.rows.concat(d.items);
  $("tech-count").textContent = `${fmtNum(d.total)} · latest seen first · click a row for details`;
  $("tech-table").innerHTML = tech.rows.length ? `<table><tr><th>#</th><th>Seen</th><th>Website</th><th>Name</th><th>Status</th>
    <th>Category</th><th class="num">Score</th><th>Registered</th><th>Source</th></tr>${tech.rows.map((r, n) => `
    <tr class="click" data-domain="${esc(r.domain)}"><td class="num muted">${n + 1}</td>
    <td class="nowrap">${fmtTime(r.seen_at)}<div class="muted small">${ago(r.seen_at)}</div></td>
    <td>${link(r.url || "https://" + r.domain + "/")}</td>
    <td>${esc(r.name || "")}<div class="muted small">${esc((r.description || r.reason || "").slice(0, 110))}</div></td>
    <td>${pill(r.status)}</td><td>${esc(r.category || "")}</td><td class="num">${r.score ?? ""}</td>
    <td class="nowrap">${r.registered_at ? fmtDate(r.registered_at) : "–"}</td>
    <td class="small">${esc(r.source || "")}${r.source_title ? `<div class="muted">${esc(r.source_title.slice(0, 50))}</div>` : ""}</td></tr>`).join("")}</table>`
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
      ${Object.keys(p.social || {}).length ? `<h4>Social</h4><div>${Object.entries(p.social).map(([k, u]) => link(u, k)).join(" · ")}</div>` : ""}`
      : `<h4>Profile</h4><div class="muted">Not profiled yet${q.status === "qualified" ? " (run the profile stage)" : " (only qualified sites are profiled)"}.</div>`}
    <h4>Changes</h4>${(d.changes || []).length ? d.changes.map((x) => `<div>${fmtTime(x.detected_at)} · ${esc(changeText(x))}</div>`).join("") : '<div class="muted">None detected.</div>'}
    <h4>Check history</h4><table>${(d.qualifications || []).slice().reverse().map((h) => `<tr><td class="nowrap">${fmtTime(h.checked_at)}</td><td>${pill(h.status)}</td><td class="small">${esc(h.reason)}</td></tr>`).join("")}</table>`;
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
$("tech-source").addEventListener("change", (e) => { tech.source = e.target.value; loadTechRows(true); });
$("tech-q").addEventListener("input", debounce((e) => { tech.q = e.target.value.trim(); loadTechRows(true); }));
$("tech-more").addEventListener("click", () => loadTechRows(false));
$("tech-table").addEventListener("click", (e) => {
  if (e.target.closest("a")) return;                     // website links open in a new tab, not the panel
  const tr = e.target.closest("tr[data-domain]"); if (tr) openDomain(tr.dataset.domain);
});
$("drawer-close").addEventListener("click", closeDrawer);
$("drawer-bg").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// ───────────────────────────── shell ─────────────────────────────
let view = "news";
async function refresh() {
  try {
    await (view === "news" ? loadNews() : loadTech());
    $("updated").textContent = `Updated ${fmtTime(new Date().toISOString())}`;
  } catch (err) {
    setBanner([{ error: `Could not load data: ${err.message}. Is the dashboard server still running?` }]);
  }
}
function show(v) {
  view = v === "tech" ? "tech" : "news";
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  $("view-news").hidden = view !== "news"; $("view-tech").hidden = view !== "tech";
  if (location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
  refresh();
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => show(t.dataset.view)));
window.addEventListener("hashchange", () => show(location.hash.slice(1)));
$("refresh").addEventListener("click", refresh);

let timer = null;
function setAuto(on) {
  $("auto").checked = on; store.set("autoRefresh", on);
  clearInterval(timer); timer = on ? setInterval(refresh, 60000) : null;
}
$("auto").addEventListener("change", (e) => setAuto(e.target.checked));
setAuto(store.get("autoRefresh", false));
show(location.hash.slice(1) || "news");
