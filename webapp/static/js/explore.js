/* A dataset's page: the grid, search, column choice, saved views and exports. Works for every dataset from the catalog's
   description of it (the "dataset-config" block on the page). */
(function () {
  "use strict";
  const config = JSON.parse(document.getElementById("dataset-config").textContent);
  const $ = (id) => document.getElementById(id);
  const csrf = () => (document.cookie.match(/(?:^|; )csrftoken=([^;]+)/) || [])[1] || "";
  const status = (t, bad) => { $("status").textContent = t; $("status").style.color = bad ? "var(--bad)" : ""; };
  const state = config.state || {};
  let total = 0, timer = null;

  $("q").value = state.q || "";
  const grid = DataGrid.create($("grid"), {
    columns: config.columns, rowsUrl: config.urls.rows, headerFilters: true, index: "_row",
    state, defaultSort: config.defaultSort,
    filters: () => ({ q: $("q").value.trim() }),
    onStatus: (t) => status(t), onError: (t) => status(t, true), onTotal: (n) => (total = n),
  });

  $("q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => grid.reload(), 350); });
  $("columns").addEventListener("click", () => grid.chooseColumns($("columns")));
  $("clearfilters").addEventListener("click", () => { $("q").value = ""; grid.clearFilters(); grid.reload(); });

  // exports: the same columns, filters and sorting as on screen
  function exportUrl(fmt) {
    const qs = new URLSearchParams();
    const p = { ...grid.params(), q: $("q").value.trim() };
    for (const k in p) if (p[k]) qs.set(k, p[k]);
    return `${config.urls.export.replace("FMT", fmt)}?${qs}`;
  }
  for (const fmt of ["csv", "xlsx"]) {
    const link = $(fmt);
    link.hidden = !config.canExport;
    link.addEventListener("click", (e) => {
      if (total > config.exportLimit) {
        e.preventDefault();
        status(`That's ${total.toLocaleString("en-US")} rows; an export takes at most ${config.exportLimit.toLocaleString("en-US")}. Filter it down first.`, true);
        return;
      }
      link.href = exportUrl(fmt);
      status(`Your ${fmt === "csv" ? "CSV" : "Excel"} file of ${total.toLocaleString("en-US")} rows downloads in a moment.`);
    });
  }

  // saved views
  const select = $("views");
  for (const v of config.views) {
    const o = document.createElement("option");
    o.value = v.id;
    o.textContent = v.name + (v.mine ? (v.shared ? " (shared)" : "") : ` (from ${v.owner})`);
    select.appendChild(o);
  }
  select.value = config.currentView || "";
  const current = () => config.views.find((v) => String(v.id) === select.value);
  $("deleteview").hidden = !(current() && current().mine);
  select.addEventListener("change", () => {
    location.href = select.value ? `${config.urls.page}?view=${select.value}` : config.urls.page;
  });
  const dialog = $("savedialog");
  $("saveview").addEventListener("click", () => {
    const c = current();
    $("viewname").value = c && c.mine ? c.name : "";
    $("viewshared").checked = !!(c && c.mine && c.shared);
    $("saveerror").hidden = true;
    dialog.showModal();
    $("viewname").select();
  });
  $("savecancel").addEventListener("click", () => dialog.close());
  $("saveform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const res = await fetch(config.urls.save, {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ name: $("viewname").value, shared: $("viewshared").checked,
        state: { ...grid.state(), q: $("q").value.trim() } }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { $("saveerror").textContent = data.error || "Saving failed."; $("saveerror").hidden = false; return; }
    location.href = data.url;
  });
  $("deleteview").addEventListener("click", async () => {
    const c = current();
    if (!c) return;
    if ($("deleteview").dataset.armed !== "1") {
      $("deleteview").dataset.armed = "1";
      $("deleteview").textContent = `Really delete “${c.name}”?`;
      return;
    }
    const res = await fetch(config.urls.delete.replace("/0/delete", `/${c.id}/delete`), { method: "POST", headers: { "X-CSRFToken": csrf() } });
    if (res.ok) location.href = config.urls.page;
    else status("Couldn't delete that view.", true);
  });
})();
