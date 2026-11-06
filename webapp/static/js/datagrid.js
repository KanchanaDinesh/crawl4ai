/* datagrid.js: the only file that talks to the grid library, Tabulator (Kanchana's choice, 2026-11-06: cell blocks
   with copy and paste are free there; docs/user-platform-plan.md, "The grid"). Pages use this small interface, so
   the library can be changed by changing this file only.

   DataGrid.create(element, options)
     columns     [{field, title, type: text|choice|multi|link|date, editable, choices, allow_new, noun, width}]
     rowsUrl     GET ?start&limit&sort&dir&<filters> -> {total, rows:[{id, ...}], ms}
     editUrl     POST {id, field, value, seen} -> {ok, value, linked} | {error, conflict, current}; seen is the value
                 the person saw, so a value someone else changed meanwhile isn't saved over
     choiceUrl   POST {field, value} -> {ok, value, choices} | {error}   (columns with allow_new)
     filters()   returns extra request values (object)
     headerFilters  true: a filter under each heading (column.filter: text|number|date|choice)
     state       {columns:[{field, visible, width}], sorts:[{field, dir}], filters:[{field, value}]} to start from
     defaultSort {field, dir}, shown on the heading
     index       the row's key field (default "id"); undo: false turns off Ctrl+Z (saved edits are undone from the history)
     onStatus(text), onSelection(count), onError(text), onTotal(total)
   returns { reload(), selectedIds(), applyValue(ids, field, value), state(), params(), chooseColumns(anchor),
             clearFilters(), destroy() }

   DataGrid.pick(anchor, {choices, value, noun, onAdd(text), onDone(list), onCancel()})
     the searchable multiple-choice list, also usable outside the grid (the bulk edit bar uses it). With onAdd
     (async, returns the saved name) people can add a new choice by typing it.
   DataGrid.addChoice(url, column, text): saves a new choice and updates column.choices (shared by every list).
   DataGrid.pickRecords(anchor, {optionsUrl, current, single, onDone(rows), onCancel()}): the same for rows of another
     table, searched on the server. DataGrid.postLinks(url, ids, field, to) saves links.
*/
(function () {
  "use strict";
  const PAGE = 100;

  function csrf() {
    const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  async function getRows(opt, start, limit, sort, extra) {
    const qs = new URLSearchParams({ start, limit });
    if (sort && sort.field) { qs.set("sort", sort.field); qs.set("dir", sort.dir || "asc"); }
    const f = { ...(opt.filters ? opt.filters() : {}), ...(extra || {}) };
    for (const k in f) if (f[k]) qs.set(k, f[k]);
    const t0 = performance.now();
    const res = await fetch(`${opt.rowsUrl}?${qs}`, { headers: { Accept: "application/json" } });
    if (!res.ok) {
      const problem = await res.json().catch(() => ({}));
      throw new Error(problem.error || `Loading rows failed (HTTP ${res.status})`);
    }
    const data = await res.json();
    opt.onTotal && opt.onTotal(data.total);
    const ms = Math.round(performance.now() - t0);
    opt.onStatus && opt.onStatus(`Rows ${data.total ? start + 1 : 0}–${Math.min(start + limit, data.total)} of ` +
      `${data.total.toLocaleString("en-US")} in ${ms} ms (database ${data.ms} ms)`);
    return data;
  }

  async function saveEdit(opt, id, field, value, seen) {
    const res = await fetch(opt.editUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ id, field, value, seen, engine: "tabulator" }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      const problem = new Error(data.error || `Saving failed (HTTP ${res.status})`);
      if (data.conflict) { problem.conflict = true; problem.current = data.current; }
      throw problem;
    }
    return data;
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
    return (Array.isArray(list) ? list : list ? [list] : []).map((t) => `<span class="chip">${esc(t)}</span>`).join("");
  }
  function linkHtml(url) {
    if (!url) return "";
    const safe = /^https?:\/\//i.test(url) ? url : "";
    return safe ? `<a href="${esc(safe)}" target="_blank" rel="noopener">${esc(url.replace(/^https?:\/\//, ""))}</a>` : esc(url);
  }

  function editableLinkHtml(url) {
    if (!url) return "";
    const safe = /^https?:\/\//i.test(url);
    return esc(String(url).replace(/^https?:\/\//, "")) + (safe ? ` <a href="${esc(url)}" target="_blank" rel="noopener" title="Open the site" aria-label="Open the site">↗</a>` : "");
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
    // only when what it's attached to moved: a grid that finishes loading mustn't close the list of a toolbar button
    const scrolled = (e) => { if (e.target === document || (e.target.contains && e.target.contains(anchor))) close(true); };

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

  // ── Choosing rows of another table ───────────────────────────────────────────────────────────────────────
  // The list can be 100,000 rows long, so it's searched on the server rather than filtered here.
  async function getJson(url) {
    const res = await fetch(url, { headers: { Accept: "application/json" } });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `That didn't load (HTTP ${res.status}).`);
    return data;
  }

  function pickRecords(anchor, { optionsUrl, current, single, onDone, onCancel }) {
    const chosen = new Map((current || []).map((r) => [r.id, r.label]));
    const start = JSON.stringify([...chosen.keys()].sort());
    const box = document.createElement("div");
    box.className = "picker";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", "Choose rows");
    box.innerHTML = `<input type="search" class="picker-search" placeholder="Search by name" aria-label="Search by name" autocomplete="off">
      <div class="picker-chosen"></div><div class="picker-list" role="listbox"></div>
      <div class="picker-error" role="alert" hidden></div>
      <div class="picker-foot"><button type="button" class="btn link picker-clear">Clear all</button><button type="button" class="btn primary picker-done">Done</button></div>`;
    document.body.appendChild(box);
    const search = box.querySelector(".picker-search"), listEl = box.querySelector(".picker-list"), chosenEl = box.querySelector(".picker-chosen"),
      errorEl = box.querySelector(".picker-error");
    let shown = [], active = 0, closed = false, ticket = 0, timer = null;

    function drawChosen() {
      chosenEl.innerHTML = "";
      for (const [id, label] of chosen) {
        const b = document.createElement("button");
        b.type = "button"; b.className = "chip chip-x"; b.textContent = label;
        b.setAttribute("aria-label", `Remove ${label}`);
        b.addEventListener("click", () => { chosen.delete(id); drawChosen(); drawList(); search.focus(); });
        chosenEl.appendChild(b);
      }
      chosenEl.hidden = !chosen.size;
    }
    function drawList() {
      listEl.innerHTML = "";
      if (!shown.length) {
        const none = document.createElement("div");
        none.className = "picker-none"; none.textContent = "No rows match.";
        listEl.appendChild(none);
      }
      shown.forEach((r, i) => {
        const row = document.createElement("label");
        row.className = "picker-row" + (i === active ? " active" : "");
        row.setAttribute("role", "option");
        row.setAttribute("aria-selected", String(chosen.has(r.id)));
        const tick = document.createElement("input");
        tick.type = single ? "radio" : "checkbox"; tick.checked = chosen.has(r.id); tick.tabIndex = -1;
        tick.addEventListener("change", () => { toggle(r); search.focus(); });
        const text = document.createElement("span");
        text.textContent = r.label;
        row.append(tick, text);
        listEl.appendChild(row);
      });
    }
    function toggle(r) {
      if (chosen.has(r.id)) chosen.delete(r.id);
      else { if (single) chosen.clear(); chosen.set(r.id, r.label); }
      drawChosen(); drawList();
    }
    async function load() {
      const mine = ++ticket;
      try {
        const data = await getJson(`${optionsUrl}&q=${encodeURIComponent(search.value.trim())}`);
        if (mine !== ticket || closed) return;
        shown = data.options; active = 0; errorEl.hidden = true;
        drawList();
      } catch (err) { if (!closed) { errorEl.textContent = err.message; errorEl.hidden = false; } }
    }
    function place() {
      const r = anchor.getBoundingClientRect();
      box.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - box.offsetWidth - 8))}px`;
      const below = r.bottom + 330 <= window.innerHeight;
      box.style.top = below ? `${r.bottom + 4}px` : "auto";
      box.style.bottom = below ? "auto" : `${Math.max(8, window.innerHeight - r.top + 4)}px`;
    }
    function close(save) {
      if (closed) return;
      closed = true;
      document.removeEventListener("mousedown", outside, true);
      box.remove();
      const result = [...chosen].map(([id, label]) => ({ id, label }));
      if (save && JSON.stringify([...chosen.keys()].sort()) !== start) onDone && onDone(result);
      else onCancel && onCancel();
    }
    const outside = (e) => { if (!box.contains(e.target)) { e.stopPropagation(); close(true); } };
    search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 200); });
    search.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, shown.length - 1); drawList(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); drawList(); }
      else if (e.key === "Enter") { e.preventDefault(); if (shown[active]) toggle(shown[active]); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(false); }
      else if (e.key === "Tab") { e.preventDefault(); close(true); }
    });
    box.querySelector(".picker-done").addEventListener("click", () => close(true));
    box.querySelector(".picker-clear").addEventListener("click", () => { chosen.clear(); drawChosen(); drawList(); search.focus(); });
    drawChosen(); place(); load();
    search.focus();
    setTimeout(() => { if (!closed) document.addEventListener("mousedown", outside, true); }, 150);
    return { close };
  }

  function recordsEditor(cell, onRendered, success, cancel, params) {
    const { column: c, opt } = params;
    const el = document.createElement("div");
    el.className = "picker-cell";
    el.innerHTML = chipsHtml(cell.getValue());
    onRendered(async () => {
      const id = cell.getRow().getData()[opt.index || "id"];
      const qs = new URLSearchParams({ field: c.field });
      try {
        const now = await getJson(`${opt.links.values}?${qs}&id=${encodeURIComponent(id)}`);
        pickRecords(cell.getElement(), { optionsUrl: `${opt.links.options}?${qs}`, current: now.rows, single: c.single,
          onCancel: () => cancel(),
          onDone: async (rows) => {
            try {
              const done = await postLinks(opt.links.save, [id], c.field, rows.map((r) => r.id));
              success(done.labels && done.labels.length ? done.labels : null);
              opt.onLinked && opt.onLinked(id);
            } catch (err) { opt.onError && opt.onError(err.message); cancel(); }
          } });
      } catch (err) { opt.onError && opt.onError(err.message); cancel(); }
    });
    return el;
  }

  async function postLinks(url, ids, field, to) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ ids, field, to }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || `Saving failed (HTTP ${res.status})`);
    return data;
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
  const WIDTHS = { number: 110, datetime: 140, bool: 80, link: 220, list: 200, json: 220, text: 190 };

  // A tick-list filter under a heading: the searchable list, for fields with few different values
  function choiceFilter(cell, onRendered, success, cancel, params) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "filter-btn";
    let current = Array.isArray(cell.getValue()) ? cell.getValue() : [];
    const show = () => {
      button.textContent = !current.length ? "Any" : current.length === 1 ? current[0] : `${current.length} chosen`;
      button.title = current.join(", ");
      button.classList.toggle("on", current.length > 0);
    };
    show();
    button.addEventListener("click", (e) => {
      e.stopPropagation();
      pick(button, { choices: params.choices, value: current, noun: "value",
        onDone: (list) => { current = list; show(); success(list); button.focus(); },
        onCancel: () => button.focus() });
    });
    // Tabulator treats a key released on the filter's element as typing and reads its .value, which would clear the
    // filter (focus comes back here just before the key is released). So the element it gets is a wrapper.
    const wrapper = document.createElement("div");
    wrapper.appendChild(button);
    button.addEventListener("keyup", (e) => e.stopPropagation());
    return wrapper;
  }

  function headerFilter(col, c) {
    if (c.filter === "choice") {
      col.headerFilter = choiceFilter;
      col.headerFilterParams = { choices: c.filter_choices || c.choices || [] };
      col.headerFilterEmptyCheck = (v) => !v || !v.length;
    } else if (c.filter) {
      col.headerFilter = "input";
      col.headerFilterPlaceholder = { number: ">10, 5..20", date: "7d, 2026-11-01", text: "contains" }[c.filter] || "";
    }
  }

  const NUMBER = new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 });
  const MONEY = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function column(c, opt) {
    const col = { field: c.field, title: c.title, width: c.width || WIDTHS[c.type] || 150, headerSort: c.type !== "json", resizable: true };
    if (c.description) col.headerTooltip = c.description;
    if (opt.headerFilters) headerFilter(col, c);
    if (c.visible === false) col.visible = false;
    if (c.type === "number" || c.type === "money") {
      const shape = c.type === "money" ? MONEY : NUMBER;
      col.hozAlign = "right";
      col.formatter = (cell) => (cell.getValue() === null || cell.getValue() === undefined ? "" : shape.format(cell.getValue()));
    }
    if (c.type === "email") col.formatter = (cell) => (cell.getValue() ? `<a href="mailto:${esc(cell.getValue())}">${esc(cell.getValue())}</a>` : "");
    if (c.type === "choice") col.formatter = (cell) => (cell.getValue() ? chipsHtml([cell.getValue()]) : "");
    if (c.type === "records" || c.type === "list") col.formatter = (cell) => chipsHtml(cell.getValue());
    if (c.type === "bool") col.formatter = (cell) => (cell.getValue() === true ? "yes" : cell.getValue() === false ? "no" : "");
    if (c.editable) {
      col.editor = c.type === "choice" ? "list" : "input";
      if (col.editor === "input") col.editorParams = { selectContents: true };   // typing replaces the value, as in Excel
      if (opt.headerFilters) col.cssClass = "can-edit";
      if (c.edit_choices) {          // suggestions from what's in the data; other values can be typed
        col.editor = "list";
        col.editorParams = { values: ["", ...c.edit_choices], autocomplete: true, freetext: true, clearable: true, listOnEmpty: true };
      }
    }
    if (c.type === "choice" && c.editable) {
      col.editor = "list";
      col.editorParams = { values: ["", ...(c.choices || [])], autocomplete: true, freetext: !!c.allow_new, clearable: true, listOnEmpty: true };
    }
    if (c.type === "bool" && c.editable) { col.editor = "tickCross"; col.editorParams = { tristate: false }; }
    if (c.type === "date" && c.editable) col.editor = "date";
    if (c.type === "multi") {
      col.formatter = (cell) => chipsHtml(cell.getValue());
      if (c.editable) {
        col.editor = multiEditor;
        c.choices = c.choices || [];
        col.editorParams = { choices: c.choices, noun: c.noun || "item",
          onAdd: c.allow_new && opt.choiceUrl ? (text) => addChoice(opt.choiceUrl, c, text) : null };
      }
    }
    if (c.type === "records" && c.can_link && opt.links) {
      col.editor = recordsEditor;
      col.cssClass = "can-edit";
      col.editorParams = { column: c, opt };
    }
    // In a cell that can be edited, a double-click must edit it, not open the site twice: the link is a small arrow beside the text
    if (c.type === "link") col.formatter = (cell) => (c.editable ? editableLinkHtml(cell.getValue()) : linkHtml(cell.getValue()));
    return col;
  }

  function create(el, opt) {
    const original = new Map();          // id -> the row as loaded (and as saved since)
    const editable = !!opt.editUrl;
    const key = opt.index || "id";
    const idOf = (d) => d[key];
    const state = opt.state || {};
    // a saved view: its columns first, in its order and widths; anything else after them, hidden
    let columns = opt.columns.map((c) => ({ ...c, visible: c.shown !== false }));
    if (state.columns && state.columns.length) {
      const byField = new Map(columns.map((c) => [c.field, c]));
      const inView = state.columns.filter((c) => byField.has(c.field)).map((c) => ({
        ...byField.get(c.field), visible: c.visible !== false, width: c.width || byField.get(c.field).width }));
      const named = new Set(inView.map((c) => c.field));
      columns = [...inView, ...columns.filter((c) => !named.has(c.field)).map((c) => ({ ...c, visible: false }))];
    }
    const shownFields = () => table.getColumns().filter((c) => c.getField() && c.isVisible()).map((c) => c.getField());
    let lastParams = {};
    // Tabulator writes its height onto the element itself; "100%" of a box without a fixed height made the grid
    // grow to the full data and keep loading pages, so pass the box's height in pixels
    const height = el.getBoundingClientRect().height || 560;
    const table = new window.Tabulator(el, {
      height: `${Math.round(height)}px`,
      layout: "fitData",
      columns: columns.map((c) => column(c, opt)),
      index: key,
      nestedFieldSeparator: false,       // a field "extra.city" is one field, not a key inside "extra"
      movableColumns: !!opt.headerFilters,
      headerSortClickElement: "header",
      columnHeaderSortMulti: true,
      filterMode: "remote",
      headerFilterLiveFilterDelay: 400,
      initialHeaderFilter: (state.filters || []).map((f) => ({ field: f.field, value: f.value })),
      initialSort: (state.sorts && state.sorts.length ? state.sorts
        : opt.defaultSort ? [opt.defaultSort] : []).filter((s) => columns.some((c) => c.field === s.field && c.visible)).map((s) => ({ column: s.field, dir: s.dir })),
      // Spreadsheet-style cell blocks, with copy and paste (also from Excel)
      selectableRange: 1,
      selectableRangeColumns: true,
      selectableRangeRows: true,
      selectableRangeClearCells: false,
      editTriggerEvent: "dblclick",
      clipboard: editable ? true : "copy",
      clipboardCopyStyled: false,
      clipboardCopyConfig: { rowHeaders: false, columnHeaders: false },
      clipboardCopyRowRange: "range",
      clipboardPasteParser: "range",
      clipboardPasteAction: "range",
      rowHeader: { resizable: false, frozen: true, width: 46, hozAlign: "center", formatter: "rownum", headerSort: false },
      history: editable && opt.undo !== false,
      progressiveLoad: "scroll",
      progressiveLoadScrollMargin: 400,
      paginationSize: PAGE,
      sortMode: "remote",
      ajaxURL: opt.rowsUrl,
      ajaxRequestFunc: async (url, config, params) => {
        const page = params.page || 1, size = params.size || PAGE;
        const s = (params.sort || [])[0];
        const sorts = (params.sort || []).map((x) => ({ field: x.field, dir: x.dir }));
        const filters = (params.filter || []).map((x) => ({ field: x.field, value: x.value }));
        // what a generic dataset's endpoint needs; the same values make the export links
        lastParams = opt.headerFilters ? { cols: JSON.stringify(shownFields()),
          filters: filters.length ? JSON.stringify(filters) : "", sorts: sorts.length ? JSON.stringify(sorts) : "" } : {};
        try {
          const data = await getRows(opt, (page - 1) * size, size, s ? { field: s.field, dir: s.dir } : null, lastParams);
          for (const r of data.rows) original.set(idOf(r), { ...r });   // to undo pastes into read-only cells
          return { last_page: Math.max(1, Math.ceil(data.total / size)), data: data.rows };
        } catch (e) { opt.onError && opt.onError(e.message); throw e; }
      },
      rowFormatter: (row) => {
        const d = row.getData();
        for (const f of d._edited || []) { const cell = row.getCell(f); cell && cell.getElement().classList.add("edited"); }
        for (const f of d._pending || []) { const cell = row.getCell(f); cell && cell.getElement().classList.add("pending"); }
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
          const before = (original.get(idOf(data)) || {})[field];
          if (!col || !col.editable) { putBack[field] = before; refused++; continue; }
          const value = col.type === "multi" && typeof raw === "string" ? raw.split(",").map((v) => v.trim()).filter(Boolean) : raw;
          try {
            const done = await saveEdit(opt, idOf(data), field, value, before);
            (original.get(idOf(data)) || {})[field] = done.value;
            if (!col.mine) data._edited = [...(data._edited || []), field];
            putBack[field] = done.value;
            Object.assign(putBack, done.linked || {});
            saved++;
          } catch (err) { putBack[field] = err.conflict ? err.current : before; refused++; opt.onError && opt.onError(err.message); }
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
      const value = cell.getValue(), row = cell.getRow(), d = row.getData();
      const seen = (original.get(idOf(d)) || {})[field];
      // row.update changes what's shown without counting as another edit, which cell.setValue would
      try {
        const done = await saveEdit(opt, idOf(d), field, value, seen);
        (original.get(idOf(d)) || {})[field] = done.value;
        if (!col.mine) {       // underlined: a collected value someone corrected. People's own fields are all theirs.
          d._edited = [...(d._edited || []), field];
          cell.getElement().classList.add("edited");
        }
        if (JSON.stringify(done.value) !== JSON.stringify(value) || done.linked) row.update({ [field]: done.value, ...(done.linked || {}) });
        opt.onStatus && opt.onStatus(`Saved ${col.title}${d.name ? ` of ${d.name}` : ""}`);
      } catch (err) {
        opt.onError && opt.onError(err.message);
        const back = err.conflict ? err.current : seen;
        if (err.conflict) (original.get(idOf(d)) || {})[field] = back;
        row.update({ [field]: back });
      }
    });
    return {
      state() {
        return {
          columns: table.getColumns().filter((c) => c.getField()).map((c) => ({ field: c.getField(), visible: c.isVisible(), width: c.getWidth() })),
          sorts: table.getSorters().map((s) => ({ field: s.field, dir: s.dir })),
          filters: table.getHeaderFilters().map((f) => ({ field: f.field, value: f.value })),
        };
      },
      params() { return { ...lastParams, cols: JSON.stringify(shownFields()) }; },
      chooseColumns(anchor) {
        const byTitle = new Map(columns.map((c) => [c.title, c.field]));
        pick(anchor, { choices: columns.map((c) => c.title), noun: "column",
          value: columns.filter((c) => table.getColumn(c.field).isVisible()).map((c) => c.title),
          onDone: (titles) => {
            if (!titles.length) { opt.onError && opt.onError("Keep at least one column."); return; }
            const keep = new Set(titles.map((t) => byTitle.get(t)));
            for (const c of table.getColumns()) {
              const f = c.getField();
              if (!f) continue;
              if (keep.has(f)) c.show(); else c.hide();
            }
            table.setData();
          },
        });
      },
      clearFilters() { table.clearHeaderFilter(); },
      reload() { table.setData(); },
      addRow(data) {
        original.set(idOf(data), { ...data });
        table.addRow(data, true).then((row) => {
          table.scrollToRow(row, "top", false).catch(() => {});
          const first = columns.find((c) => c.editable && c.visible !== false);
          if (first) { const cell = row.getCell(first.field); cell && cell.edit(); }
        });
      },
      selectedIds() { return selectedRows().map((r) => idOf(r.getData())); },
      applyChanges(id, changes, edited) {     // edited: the fields of this row that are corrected now
        const row = table.getRow(id);
        if (!row) return;
        for (const [f, v] of Object.entries(changes)) (original.get(id) || {})[f] = v;
        row.update(changes);
        if (edited) {
          const d = row.getData();
          d._edited = edited;
          d._pending = (d._pending || []).filter((f) => edited.includes(f) && !(f in changes));
          for (const cell of row.getCells()) cell.getElement().classList.remove("edited", "pending");
        }
        row.reformat();
      },
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

  window.DataGrid = { create, pick, pickRecords, addChoice, postLinks };
})();
