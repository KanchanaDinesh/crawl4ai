/* datagrid.js: the only file that talks to the grid library, Tabulator (Kanchana's choice, 2026-11-06: cell blocks
   with copy and paste are free there; docs/user-platform-plan.md, "The grid"). Pages use this small interface, so
   the library can be changed by changing this file only.

   DataGrid.create(element, options)
     columns     [{field, title, type: text|choice|multi|link|date, editable, choices, allow_new, noun, width}]
     rowsUrl     GET ?start&limit&sort&dir&<filters> -> {total, rows:[{id, ...}], ms}
     editUrl     POST {id, field, value} -> {ok, value} | {error}
     choiceUrl   POST {field, value} -> {ok, value, choices} | {error}   (columns with allow_new)
     filters()   returns the current filter values (object)
     onStatus(text), onSelection(count), onError(text)
   returns { reload(), selectedIds(), applyValue(ids, field, value), destroy() }

   DataGrid.pick(anchor, {choices, value, noun, onAdd(text), onDone(list), onCancel()})
     the searchable multiple-choice list, also usable outside the grid (the bulk edit bar uses it). With onAdd
     (async, returns the saved name) people can add a new choice by typing it.
   DataGrid.addChoice(url, column, text): saves a new choice and updates column.choices (shared by every list).
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
      body: JSON.stringify({ id, field, value, engine: "tabulator" }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || `Saving failed (HTTP ${res.status})`);
    return data.value;
  }

  async function addChoice(url, column, text) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ field: column.field, value: text }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || `Adding failed (HTTP ${res.status})`);
    column.choices.splice(0, column.choices.length, ...data.choices);   // the same array every list reads
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

  // ── The searchable multiple-choice list ──────────────────────────────────────────────────────────────────
  // Tabulator's own list editor can't search and select several at once (multiselect is off with autocomplete),
  // and long lists need both. Lives on <body>, so the grid's cell doesn't clip it.
  function pick(anchor, { choices, value, noun = "item", onAdd, onDone, onCancel }) {
    const selected = new Set(value || []);
    const start = JSON.stringify([...selected].sort());
    const box = document.createElement("div");
    box.className = "picker";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", `Choose ${noun}s`);
    box.innerHTML = `<input type="search" class="picker-search" placeholder="Search ${esc(noun)}s" aria-label="Search ${esc(noun)}s" autocomplete="off">
      <div class="picker-chosen"></div>
      <div class="picker-list" role="listbox" aria-multiselectable="true"></div>
      <div class="picker-error" role="alert" hidden></div>
      <div class="picker-foot"><button type="button" class="btn link picker-clear">Clear all</button><button type="button" class="btn primary picker-done">Done</button></div>`;
    document.body.appendChild(box);
    const search = box.querySelector(".picker-search"), listEl = box.querySelector(".picker-list"),
      chosenEl = box.querySelector(".picker-chosen"), clearBtn = box.querySelector(".picker-clear"),
      errorEl = box.querySelector(".picker-error");
    let shown = [], active = 0, closed = false, adding = false, addText = "";
    const ordered = () => (choices || []).filter((c) => selected.has(c));

    function drawChosen() {
      chosenEl.innerHTML = "";
      for (const c of ordered()) {
        const b = document.createElement("button");
        b.type = "button"; b.className = "chip chip-x"; b.textContent = c;
        b.title = `Remove ${c}`; b.setAttribute("aria-label", `Remove ${c}`);
        b.addEventListener("click", () => { selected.delete(c); drawChosen(); drawList(); search.focus(); });
        chosenEl.appendChild(b);
      }
      chosenEl.hidden = !selected.size;
      clearBtn.disabled = !selected.size;
    }
    function drawList() {
      const q = search.value.trim().toLowerCase();
      shown = (choices || []).filter((c) => c.toLowerCase().includes(q));
      // a new choice can be added when what's typed isn't in the list yet (the same words in other capitals are)
      addText = onAdd && q && !(choices || []).some((c) => c.toLowerCase() === q) ? search.value.trim().replace(/\s+/g, " ") : "";
      active = Math.min(active, Math.max(shown.length + (addText ? 1 : 0) - 1, 0));
      listEl.innerHTML = "";
      if (!shown.length && !addText) {
        const none = document.createElement("div");
        none.className = "picker-none"; none.textContent = `No ${noun} matches "${search.value.trim()}"`;
        listEl.appendChild(none);
      }
      shown.forEach((c, i) => {
        const row = document.createElement("label");
        row.className = "picker-row" + (i === active ? " active" : "");
        row.setAttribute("role", "option");
        row.setAttribute("aria-selected", String(selected.has(c)));
        const box2 = document.createElement("input");
        box2.type = "checkbox"; box2.checked = selected.has(c); box2.tabIndex = -1;
        box2.addEventListener("change", () => { toggle(c); search.focus(); });
        const text = document.createElement("span");
        text.textContent = c;
        row.append(box2, text);
        row.addEventListener("mouseenter", () => { active = i; mark(); });
        listEl.appendChild(row);
      });
      if (addText) {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "picker-row picker-add" + (active === shown.length ? " active" : "");
        row.textContent = `Add new ${noun}: ${addText}`;
        row.addEventListener("click", addNew);
        row.addEventListener("mouseenter", () => { active = shown.length; mark(); });
        listEl.appendChild(row);
      }
      errorEl.hidden = true;
    }
    async function addNew() {
      if (adding || !addText) return;
      adding = true;
      try {
        const saved = await onAdd(addText);
        if (closed) return;
        selected.add(saved);
        search.value = "";
        active = 0;
        drawChosen(); drawList();
        search.focus();
      } catch (err) {
        if (closed) return;
        errorEl.textContent = err.message;
        errorEl.hidden = false;
        search.focus();
      } finally { adding = false; }
    }
    function mark() {
      listEl.querySelectorAll(".picker-row").forEach((r, i) => {
        r.classList.toggle("active", i === active);
        if (i === active) r.scrollIntoView({ block: "nearest" });
      });
    }
    function toggle(c) {
      selected.has(c) ? selected.delete(c) : selected.add(c);
      drawChosen(); drawList(); mark();
    }
    function place() {
      const r = anchor.getBoundingClientRect(), w = box.offsetWidth, h = box.offsetHeight;
      const left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
      const below = r.bottom + 4 + h <= window.innerHeight;
      box.style.left = `${left}px`;
      // opening upwards it hangs from its bottom edge, so it stays next to the cell when a search shortens it
      box.style.top = below ? `${r.bottom + 4}px` : "auto";
      box.style.bottom = below ? "auto" : `${Math.max(8, window.innerHeight - r.top + 4)}px`;
    }
    function close(save) {
      if (closed) return;
      closed = true;
      document.removeEventListener("mousedown", outside, true);
      window.removeEventListener("scroll", scrolled, true);
      window.removeEventListener("resize", done);
      box.remove();
      const result = ordered();
      if (save && JSON.stringify([...result].sort()) !== start) onDone && onDone(result);
      else onCancel && onCancel();
    }
    const done = () => close(true);
    const outside = (e) => { if (!box.contains(e.target)) { e.stopPropagation(); close(true); } };
    const scrolled = (e) => { if (!box.contains(e.target)) close(true); };

    search.addEventListener("input", () => { active = 0; drawList(); });
    search.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, shown.length + (addText ? 1 : 0) - 1); mark(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); mark(); }
      else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); close(true); }
      else if (e.key === "Enter") {
        e.preventDefault();
        if (shown[active]) { toggle(shown[active]); search.select(); }
        else if (addText) addNew();
      }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(false); }
      else if (e.key === "Tab") { e.preventDefault(); close(true); }
    });
    box.querySelector(".picker-done").addEventListener("click", done);
    clearBtn.addEventListener("click", () => { selected.clear(); drawChosen(); drawList(); search.focus(); });
    drawChosen(); drawList(); place();
    search.focus();
    // from the next moment on: opening can itself scroll the grid, or still be inside the click that opened it
    setTimeout(() => {
      if (closed) return;
      document.addEventListener("mousedown", outside, true);
      window.addEventListener("scroll", scrolled, true);
      window.addEventListener("resize", done);
    }, 150);
    return { close };
  }

  function multiEditor(cell, onRendered, success, cancel, params) {
    const el = document.createElement("div");
    el.className = "picker-cell";
    el.innerHTML = chipsHtml(cell.getValue());
    onRendered(() => pick(cell.getElement(), {
      choices: params.choices, value: cell.getValue(), noun: params.noun || "item", onAdd: params.onAdd,
      onDone: (list) => success(list), onCancel: () => cancel(),
    }));
    return el;
  }

  // ── The grid ─────────────────────────────────────────────────────────────────────────────────────────────
  function column(c, opt) {
    const col = { field: c.field, title: c.title, width: c.width || 150, headerSort: true, resizable: true };
    if (c.editable) col.editor = c.type === "choice" ? "list" : "input";
    if (c.type === "choice") col.editorParams = { values: ["", ...(c.choices || [])], autocomplete: true, clearable: true, listOnEmpty: true };
    if (c.type === "multi") {
      col.editor = multiEditor;
      c.choices = c.choices || [];
      col.editorParams = { choices: c.choices, noun: c.noun || "item",
        onAdd: c.allow_new && opt.choiceUrl ? (text) => addChoice(opt.choiceUrl, c, text) : null };
      col.formatter = (cell) => chipsHtml(cell.getValue());
    }
    if (c.type === "link") col.formatter = (cell) => linkHtml(cell.getValue());
    return col;
  }

  function create(el, opt) {
    const original = new Map();          // id -> the row as loaded (and as saved since)
    // Tabulator writes its height onto the element itself; "100%" of a box without a fixed height made the grid
    // grow to the full data and keep loading pages, so pass the box's height in pixels
    const height = el.getBoundingClientRect().height || 560;
    const table = new window.Tabulator(el, {
      height: `${Math.round(height)}px`,
      layout: "fitData",
      columns: opt.columns.map((c) => column(c, opt)),
      index: "id",
      // Spreadsheet-style cell blocks, with copy and paste (also from Excel)
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
      const value = cell.getValue();
      try {
        const saved = await saveEdit(opt, cell.getRow().getData().id, field, value);
        const d = cell.getRow().getData();
        (original.get(d.id) || {})[field] = saved;
        d._edited = [...(d._edited || []), field];
        cell.getElement().classList.add("edited");
        if (JSON.stringify(saved) !== JSON.stringify(value)) cell.setValue(saved, true);
        opt.onStatus && opt.onStatus(`Saved ${col.title} of ${d.name}`);
      } catch (err) {
        opt.onError && opt.onError(err.message);
        cell.restoreOldValue();
      }
    });
    return {
      reload() { table.setData(); },
      selectedIds() { return selectedRows().map((r) => r.getData().id); },
      applyValue(ids, field, value) {
        for (const id of ids) {
          const row = table.getRow(id);
          if (!row) continue;
          const d = row.getData();
          (original.get(id) || {})[field] = value;
          d._edited = [...(d._edited || []), field];
          row.update({ [field]: value });
          row.reformat();
        }
      },
      destroy() { table.destroy(); },
    };
  }

  window.DataGrid = { create, pick, addChoice };
})();
