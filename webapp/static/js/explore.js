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
    columns: config.columns, rowsUrl: config.urls.rows, headerFilters: true, index: config.hasRecords ? "_id" : "_row",
    editUrl: config.canEdit ? config.urls.edit : null, undo: false, choiceUrl: config.urls.choice,
    links: config.canEdit ? { options: config.urls.linkOptions, values: config.urls.linkValues, save: config.urls.links } : null,
    // a lookup or rollup in the same row depends on the link just changed
    onLinked: () => { if (config.columns.some((c) => c.mine && (c.type === "number" || c.type === "list"))) grid.reload(); },
    state, defaultSort: config.defaultSort,
    onSelection: showSelection,
    filters: () => ({ q: $("q").value.trim() }),
    onStatus: (t) => status(t), onError: (t) => status(t, true), onTotal: (n) => (total = n),
  });

  // ── rows selected: their history, one value for all of them, hide ──
  function showSelection() {
    const n = config.hasRecords ? grid.selectedIds().length : 0;
    $("selection").hidden = !n;
    $("selcount").textContent = `${n.toLocaleString("en-US")} row${n === 1 ? "" : "s"} selected`;
    $("showhistory").hidden = n !== 1;
    $("bulkbar").hidden = !config.canEdit;
    $("hiderows").hidden = !config.canHide;
    $("hiderows").textContent = `${config.isOwn ? "Remove" : "Hide"} ${n === 1 ? "this row" : `these ${n.toLocaleString("en-US")} rows`}`;
    delete $("hiderows").dataset.armed;
  }
  async function post(url, body) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": decodeURIComponent(csrf()) }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `That didn't work (HTTP ${res.status}).`);
    return data;
  }
  const editableColumns = config.columns.filter((c) => c.editable || c.can_link);
  for (const c of editableColumns) {
    const o = document.createElement("option");
    o.value = c.field; o.textContent = c.title;
    $("bulkfield").appendChild(o);
  }
  function fillBulkChoices() {
    const c = editableColumns.find((x) => x.field === $("bulkfield").value);
    const picksRows = !!(c && c.type === "records");
    $("bulkvalue").hidden = $("bulkto").hidden = picksRows;       // rows of another table are chosen from a list
    $("bulkapply").textContent = picksRows ? "Choose rows" : "Apply";
    $("bulkchoices").innerHTML = "";
    for (const v of (c && (c.edit_choices || c.choices)) || []) {
      const o = document.createElement("option");
      o.value = v;
      $("bulkchoices").appendChild(o);
    }
  }
  $("bulkfield").addEventListener("change", fillBulkChoices);
  fillBulkChoices();
  $("bulkapply").addEventListener("click", async () => {
    const ids = grid.selectedIds(), field = $("bulkfield").value;
    const c = editableColumns.find((x) => x.field === field);
    if (!ids.length || !c) return;
    if (c.type === "records") {
      DataGrid.pickRecords($("bulkapply"), { optionsUrl: `${config.urls.linkOptions}?${new URLSearchParams({ field })}`, current: [], single: c.single,
        onDone: async (rows) => {
          try {
            const done = await DataGrid.postLinks(config.urls.links, ids, field, rows.map((r) => r.id));
            status(`${c.title} set on ${done.changed.toLocaleString("en-US")} row${done.changed === 1 ? "" : "s"}.`);
            grid.reload();
          } catch (err) { status(err.message, true); }
        } });
      return;
    }
    try {
      const done = await post(config.urls.bulk, { ids, field, value: $("bulkvalue").value });
      grid.applyValue(ids, field, done.value);
      for (const [id, linked] of Object.entries(done.linked || {})) grid.applyChanges(id, linked, [field, ...Object.keys(linked)]);
      status(`${c.title} set on ${done.changed.toLocaleString("en-US")} row${done.changed === 1 ? "" : "s"}.`);
      $("bulkvalue").value = "";
    } catch (err) { status(err.message, true); }
  });
  $("hiderows").addEventListener("click", async () => {
    const ids = grid.selectedIds(), b = $("hiderows");
    if (!ids.length) return;
    if (b.dataset.armed !== "1") {            // asked here rather than in a dialog: one more click, right where they are
      b.dataset.armed = "1";
      b.textContent = `Really ${config.isOwn ? "remove" : "hide"} ${ids.length.toLocaleString("en-US")}? They can come back from Changes`;
      return;
    }
    try {
      const done = await post(config.urls.hide, { ids });
      status(`${done.hidden.toLocaleString("en-US")} row${done.hidden === 1 ? "" : "s"} ${config.isOwn ? "removed" : "hidden"}. Changes lists them, to bring them back.`);
      $("selection").hidden = true;
      grid.reload();
    } catch (err) { status(err.message, true); }
  });

  // ── a chart of what's on screen: the same filters come along ──
  $("chartit").hidden = !config.canChart;
  $("chartit").addEventListener("click", (e) => {
    const qs = new URLSearchParams({ dataset: config.ref });
    const filters = grid.params().filters;
    if (filters) qs.set("filters", filters);
    e.currentTarget.href = `${config.chartUrl}?${qs}`;
  });

  // ── own tables: a new row; any rows: a copy as a table of one's own ──
  $("addrow").hidden = !(config.isOwn && config.canEdit);
  $("addrow").addEventListener("click", async () => {
    try {
      const done = await post(config.urls.addRow, {});
      grid.addRow(done.row);
      status("Row added.");
    } catch (err) { status(err.message, true); }
  });
  $("savetable").hidden = !(config.canMakeTables && config.canExport);
  $("savetable").addEventListener("click", () => {
    $("tablename").value = ""; $("tableerror").hidden = true;
    $("tabledialog").showModal();
  });
  $("tablecancel").addEventListener("click", () => $("tabledialog").close());
  $("tableform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const qs = new URLSearchParams();
    const p = { ...grid.params(), q: $("q").value.trim() };
    for (const k in p) if (p[k]) qs.set(k, p[k]);
    try {
      const done = await post(`${config.urls.saveTable}?${qs}`, { name: $("tablename").value });
      location.href = done.url;
    } catch (err) { $("tableerror").textContent = err.message; $("tableerror").hidden = false; }
  });

  // ── a row's history, in the side panel ──
  const drawer = $("drawer"), shade = $("drawer-bg");
  function closeDrawer() { drawer.classList.remove("open"); shade.classList.remove("open"); }
  $("drawer-close").addEventListener("click", closeDrawer);
  shade.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  $("showhistory").addEventListener("click", async () => {
    const id = grid.selectedIds()[0];
    if (id === undefined) return;
    drawer.classList.add("open"); shade.classList.add("open");
    $("drawer-close").focus();
    $("drawer-body").innerHTML = '<div class="muted">Loading…</div>';
    const res = await fetch(`${config.urls.history}?${new URLSearchParams({ id })}`);
    $("drawer-body").innerHTML = res.ok ? await res.text() : '<div class="muted">That didn\'t load. Try again.</div>';
    window.htmx.process($("drawer-body"));
  });
  document.body.addEventListener("cellsChanged", (e) => grid.applyChanges(e.detail.id, e.detail.changes, e.detail.edited));
  document.body.addEventListener("htmx:responseError", (e) => {
    const box = document.querySelector("#drawer-body .panel-error");
    if (box) { box.textContent = e.detail.xhr.responseText.slice(0, 300) || "That didn't work."; box.hidden = false; }
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
