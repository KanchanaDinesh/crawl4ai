/* datagrid.js: the only file that talks to a grid library (docs/user-platform-plan.md, "The grid").

   Pages call DataGrid.create(element, options) and get back the same small interface whichever engine runs it,
   so the grid library can be changed (or AG Grid Enterprise bought) by changing this file only.

   options:
     engine      "aggrid" | "tabulator"
     columns     [{field, title, type: text|choice|multi|link|date, editable, choices, width}]
     rowsUrl     GET ?start&limit&sort&dir&<filters> -> {total, rows:[{id, ...}], ms}
     editUrl     POST {id, field, value, engine} -> {ok, value} | {error}
     filters()   returns the current filter values (object)
     onStatus(text), onSelection(count), onError(text)
   returns { engine, reload(), selectedIds(), applyValue(ids, field, value), destroy() }
*/
(function () {
  "use strict";
  const PAGE = 100;

  function csrf() {
    const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  async function getRows(opt, start, limit, sort) {
    const qs = new URLSearchParams({ start, limit });
    if (sort && sort.field) { qs.set("sort", sort.field); qs.set("dir", sort.dir || "asc"); }
    const f = opt.filters ? opt.filters() : {};
    for (const k in f) if (f[k]) qs.set(k, f[k]);
    const t0 = performance.now();
    const res = await fetch(`${opt.rowsUrl}?${qs}`, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(`Loading rows failed (HTTP ${res.status})`);
    const data = await res.json();
    const ms = Math.round(performance.now() - t0);
    opt.onStatus && opt.onStatus(`Rows ${data.total ? start + 1 : 0}–${Math.min(start + limit, data.total)} of ` +
      `${data.total.toLocaleString("en-US")} in ${ms} ms (database ${data.ms} ms)`);
    return data;
  }

  async function saveEdit(opt, id, field, value) {
    const res = await fetch(opt.editUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ id, field, value, engine: opt.engine }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || `Saving failed (HTTP ${res.status})`);
    return data.value;
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function chipsHtml(list) {
    return (list || []).map((t) => `<span class="chip">${esc(t)}</span>`).join("");
  }
  function linkHtml(url) {
    if (!url) return "";
    const safe = /^https?:\/\//i.test(url) ? url : "";
    return safe ? `<a href="${esc(safe)}" target="_blank" rel="noopener">${esc(url.replace(/^https?:\/\//, ""))}</a>` : esc(url);
  }

  // ── AG Grid Community ────────────────────────────────────────────────────────────────────────────────────
  // A small multiple-choice editor: AG Grid Community has no multi-select editor (its Rich Select is Enterprise).
  class TagEditor {
    init(params) {
      this.value = new Set(params.value || []);
      this.el = document.createElement("div");
      this.el.className = "tag-editor";
      for (const choice of params.choices || []) {
        const id = `tag-${Math.random().toString(36).slice(2)}`;
        const row = document.createElement("label");
        row.innerHTML = `<input type="checkbox" id="${id}"> ${esc(choice)}`;
        const box = row.querySelector("input");
        box.checked = this.value.has(choice);
        box.addEventListener("change", () => (box.checked ? this.value.add(choice) : this.value.delete(choice)));
        this.el.appendChild(row);
      }
      this.choices = params.choices || [];
    }
    getGui() { return this.el; }
    afterGuiAttached() { const b = this.el.querySelector("input"); b && b.focus(); }
    getValue() { return this.choices.filter((c) => this.value.has(c)); }
    isPopup() { return true; }
  }

  function agColumn(c) {
    const col = { field: c.field, headerName: c.title, width: c.width || 150, sortable: true, editable: !!c.editable, resizable: true };
    if (c.type === "choice") Object.assign(col, { cellEditor: "agSelectCellEditor", cellEditorParams: { values: ["", ...(c.choices || [])] } });
    if (c.type === "multi") Object.assign(col, { cellEditor: TagEditor, cellEditorParams: { choices: c.choices }, cellEditorPopup: true,
      cellRenderer: (p) => chipsHtml(p.value) });
    if (c.type === "link") col.cellRenderer = (p) => linkHtml(p.value);
    col.cellClassRules = { edited: (p) => !!(p.data && p.data._edited && p.data._edited.includes(c.field)) };
    return col;
  }

  function createAgGrid(el, opt) {
    const ag = window.agGrid;
    if (ag.ModuleRegistry && ag.AllCommunityModule) ag.ModuleRegistry.registerModules([ag.AllCommunityModule]);
    const dark = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches;
    const theme = ag.themeQuartz && dark && ag.colorSchemeDark ? ag.themeQuartz.withPart(ag.colorSchemeDark) : ag.themeQuartz;
    let api;
    const datasource = {
      getRows: async (p) => {
        const s = (p.sortModel || [])[0];
        try {
          const data = await getRows(opt, p.startRow, p.endRow - p.startRow, s ? { field: s.colId, dir: s.sort } : null);
          p.successCallback(data.rows, data.total);
        } catch (e) { opt.onError && opt.onError(e.message); p.failCallback(); }
      },
    };
    const options = {
      theme,
      columnDefs: opt.columns.map(agColumn),
      rowModelType: "infinite",
      cacheBlockSize: PAGE,
      maxBlocksInCache: 50,
      getRowId: (p) => String(p.data.id),
      rowSelection: { mode: "multiRow", checkboxes: true, headerCheckbox: false, enableClickSelection: false },
      singleClickEdit: false,
      stopEditingWhenCellsLoseFocus: true,
      undoRedoCellEditing: true,
      datasource,
      onSelectionChanged: () => opt.onSelection && opt.onSelection(api.getSelectedNodes().length),
      onCellValueChanged: async (e) => {
        if (e.oldValue === e.newValue) return;
        try {
          const saved = await saveEdit(opt, e.data.id, e.colDef.field, e.newValue);
          e.data._edited = [...(e.data._edited || []), e.colDef.field];
          e.node.setDataValue(e.colDef.field, saved);
          opt.onStatus && opt.onStatus(`Saved ${e.colDef.headerName} of ${e.data.name}`);
        } catch (err) {
          opt.onError && opt.onError(err.message);
          e.node.setDataValue(e.colDef.field, e.oldValue);
        }
      },
    };
    api = ag.createGrid(el, options);
    return {
      engine: "aggrid",
      reload() { api.setGridOption("datasource", datasource); },
      selectedIds() { return api.getSelectedNodes().map((n) => n.data && n.data.id).filter(Boolean); },
      applyValue(ids, field, value) {
        for (const id of ids) {
          const node = api.getRowNode(String(id));
          if (node && node.data) { node.data[field] = value; node.data._edited = [...(node.data._edited || []), field]; }
        }
        api.refreshCells({ force: true });
      },
      destroy() { api.destroy(); },
    };
  }

  // ── Tabulator ────────────────────────────────────────────────────────────────────────────────────────────
  function tabColumn(c) {
    const col = { field: c.field, title: c.title, width: c.width || 150, headerSort: true, resizable: true };
    if (c.editable) col.editor = c.type === "choice" || c.type === "multi" ? "list" : "input";
    if (c.type === "choice") col.editorParams = { values: ["", ...(c.choices || [])], autocomplete: true, clearable: true };
    if (c.type === "multi") {
      col.editorParams = { values: c.choices || [], multiselect: true };
      col.formatter = (cell) => chipsHtml(cell.getValue());
    }
    if (c.type === "link") col.formatter = (cell) => linkHtml(cell.getValue());
    return col;
  }

  function createTabulator(el, opt) {
    let busy = 0;
    const original = new Map();          // id -> the row as loaded (and as saved since)
    // Tabulator writes its height onto the element itself; "100%" of a box without a fixed height made the grid
    // grow to the full data and keep loading pages, so pass the box's height in pixels
    const height = el.getBoundingClientRect().height || 560;
    const table = new window.Tabulator(el, {
      height: `${Math.round(height)}px`,
      layout: "fitData",
      columns: opt.columns.map(tabColumn),
      index: "id",
      // Spreadsheet-style cell ranges, with copy and paste (also from Excel): free in Tabulator
      selectableRange: 1,
      selectableRangeColumns: true,
      selectableRangeRows: true,
      selectableRangeClearCells: false,
      editTriggerEvent: "dblclick",
      clipboard: true,
      clipboardCopyStyled: false,
      clipboardCopyConfig: { rowHeaders: false, columnHeaders: false },
      clipboardCopyRowRange: "range",
      clipboardPasteParser: "range",
      clipboardPasteAction: "range",
      rowHeader: { resizable: false, frozen: true, width: 46, hozAlign: "center", formatter: "rownum", headerSort: false },
      history: true,
      progressiveLoad: "scroll",
      progressiveLoadScrollMargin: 400,
      paginationSize: PAGE,
      sortMode: "remote",
      ajaxURL: opt.rowsUrl,
      ajaxRequestFunc: async (url, config, params) => {
        const page = params.page || 1, size = params.size || PAGE;
        const s = (params.sort || [])[0];
        try {
          const data = await getRows(opt, (page - 1) * size, size, s ? { field: s.field, dir: s.dir } : null);
          for (const r of data.rows) original.set(r.id, { ...r });      // to undo pastes into read-only cells
          return { last_page: Math.max(1, Math.ceil(data.total / size)), data: data.rows };
        } catch (e) { opt.onError && opt.onError(e.message); throw e; }
      },
      rowFormatter: (row) => {
        const d = row.getData();
        for (const f of d._edited || []) { const cell = row.getCell(f); cell && cell.getElement().classList.add("edited"); }
      },
    });
    const selectedRows = () => {
      const seen = new Map();
      for (const r of table.getRanges ? table.getRanges() : []) for (const row of r.getRows()) seen.set(row.getIndex(), row);
      return [...seen.values()];
    };
    // A paste changes many cells at once and doesn't fire cellEdited: save each pasted cell of an editable column,
    // and put back what was pasted into a read-only one (e.g. Name)
    table.on("clipboardPasted", async (clipboard, rowData, rows) => {
      let saved = 0, refused = 0;
      for (let i = 0; i < rows.length; i++) {
        // a range paste hands over Tabulator's internal rows; the public row object comes from getComponent()
        const row = rows[i].getComponent ? rows[i].getComponent() : rows[i];
        const data = row.getData(), pasted = rowData[i] || {}, putBack = {};
        for (const [field, raw] of Object.entries(pasted)) {
          const col = opt.columns.find((c) => c.field === field);
          const before = (original.get(data.id) || {})[field];
          if (!col || !col.editable) { putBack[field] = before; refused++; continue; }
          const value = col.type === "multi" && typeof raw === "string" ? raw.split(",").map((v) => v.trim()).filter(Boolean) : raw;
          try {
            const value2 = await saveEdit(opt, data.id, field, value);
            (original.get(data.id) || {})[field] = value2;
            data._edited = [...(data._edited || []), field];
            putBack[field] = value2;
            saved++;
          } catch (err) { putBack[field] = before; refused++; opt.onError && opt.onError(err.message); }
        }
        if (Object.keys(putBack).length) { row.update(putBack); row.reformat(); }
      }
      opt.onStatus && opt.onStatus(`Pasted: ${saved} cell${saved === 1 ? "" : "s"} saved` +
        (refused ? `, ${refused} not changed (read-only column or not allowed)` : ""));
    });
    table.on("rangeChanged", () => opt.onSelection && opt.onSelection(selectedRows().length));
    table.on("rangeAdded", () => opt.onSelection && opt.onSelection(selectedRows().length));
    table.on("cellEdited", async (cell) => {
      const field = cell.getField(), col = opt.columns.find((c) => c.field === field);
      if (!col || !col.editable) return;
      let value = cell.getValue();
      if (col.type === "multi" && typeof value === "string") value = value.split(",").map((v) => v.trim()).filter(Boolean);
      busy++;
      try {
        const saved = await saveEdit(opt, cell.getRow().getData().id, field, value);
        const d = cell.getRow().getData();
        d._edited = [...(d._edited || []), field];
        cell.getElement().classList.add("edited");
        if (JSON.stringify(saved) !== JSON.stringify(value)) cell.setValue(saved, true);
        opt.onStatus && opt.onStatus(`Saved ${col.title} of ${d.name}`);
      } catch (err) {
        opt.onError && opt.onError(err.message);
        cell.restoreOldValue();
      } finally { busy--; }
    });
    return {
      engine: "tabulator",
      reload() { table.setData(); },
      selectedIds() { return selectedRows().map((r) => r.getData().id); },
      applyValue(ids, field, value) {
        for (const id of ids) {
          const row = table.getRow(id);
          if (row) { const d = row.getData(); d._edited = [...(d._edited || []), field]; row.update({ [field]: value }); row.reformat(); }
        }
      },
      destroy() { table.destroy(); },
    };
  }

  window.DataGrid = {
    create(el, opt) {
      if (opt.engine === "tabulator") return createTabulator(el, opt);
      return createAgGrid(el, opt);
    },
  };
})();
