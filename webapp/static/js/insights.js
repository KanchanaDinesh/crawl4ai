// The dashboard tabs: the few things a page can't do by itself (side panel, clicks that set a filter, refresh, full
// screen). plots.js draws the charts; everything else is Django templates + HTMX.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);

  // ── the side panel with one row's details ──
  const drawer = $("drawer"), shade = $("drawer-bg");
  let cameFrom = null;
  function openDrawer() {
    if (!drawer.classList.contains("open")) cameFrom = document.activeElement;
    drawer.classList.add("open"); shade.classList.add("open");
    $("drawer-body").innerHTML = '<div class="muted">Loading…</div>';
    $("drawer-close").focus();
  }
  function closeDrawer() {
    if (!drawer.classList.contains("open")) return;
    drawer.classList.remove("open"); shade.classList.remove("open");
    if (cameFrom && cameFrom.isConnected) cameFrom.focus();
  }
  document.body.addEventListener("htmx:beforeRequest", (e) => {
    if (e.detail.target && e.detail.target.id === "drawer-body" && !drawer.contains(e.detail.elt)) openDrawer();
  });
  $("drawer-close").addEventListener("click", closeDrawer);
  shade.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

  // ── lists: the count above a list comes with its rows; a word or a country clicked anywhere filters the list ──
  document.body.addEventListener("listCount", (e) => {
    const count = document.querySelector("[data-count]");
    if (count) count.textContent = e.detail.value;
  });
  function searchFor(text) {
    const box = document.querySelector("#filters input[type=search]");
    if (!box) return;
    box.value = text;
    box.dispatchEvent(new Event("input", { bubbles: true }));
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    $("list").scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" });
  }
  function setFilter(name, value) {
    const control = document.querySelector(`#filters [name="${name}"]`);
    if (!control) return;
    control.value = control.value === value ? "" : value;      // clicking the chosen one again clears it
    control.dispatchEvent(new Event("change", { bubbles: true }));
  }
  function activate(target) {
    const word = target.closest("[data-search]");
    if (word) { searchFor(word.dataset.search); return true; }
    const row = target.closest("[data-set-field]");
    if (row) { setFilter(row.dataset.setField, row.dataset.setValue); return true; }
    return false;
  }
  document.addEventListener("click", (e) => activate(e.target));
  document.addEventListener("keydown", (e) => { if (e.key === "Enter" && activate(e.target)) e.preventDefault(); });
  // A long list scrolls inside its own box: fetch the next rows by itself near the bottom, so 3,000 sites don't look
  // like the 14 that fit in the box. (Its Load more button does the same by keyboard.)
  document.addEventListener("scroll", (e) => {
    const box = e.target;
    if (!box.classList || !box.classList.contains("tablewrap") || box.scrollTop + box.clientHeight < box.scrollHeight - 300) return;
    const next = box.querySelector(".more-row");
    if (next) window.htmx.trigger(next, "loadmore");
  }, true);
  // The period changes the whole page (counts and charts too), so it reloads with all the other filters kept
  document.addEventListener("change", (e) => { if (e.target.dataset && e.target.dataset.reload) e.target.form.submit(); });

  // ── when a request doesn't work: say so, and send a timed-out session to the sign-in page ──
  const banner = $("banner");
  function complain(text) { banner.textContent = text; banner.hidden = false; }
  document.body.addEventListener("htmx:beforeSwap", (e) => {
    if ((e.detail.xhr.responseURL || "").includes("/login")) { e.detail.shouldSwap = false; window.location.reload(); }
  });
  document.body.addEventListener("htmx:responseError", (e) => {
    complain(e.detail.xhr.status === 403 ? "You don't have the right to see that. An administrator can grant it."
      : `That didn't load (HTTP ${e.detail.xhr.status}). Press Refresh to try again.`);
  });
  document.body.addEventListener("htmx:sendError", () => complain("Can't reach the web app. Is it still running? Press Refresh to try again."));
  document.body.addEventListener("htmx:afterOnLoad", (e) => {
    if (e.detail.successful && !banner.dataset.fromServer) banner.hidden = true;
  });
  banner.dataset.fromServer = banner.textContent.trim() ? "1" : "";

  // ── refresh and full screen ──
  $("refresh").addEventListener("click", () => window.location.reload());
  const store = {
    get() { try { return localStorage.getItem("dashboardAuto") === "1"; } catch { return false; } },
    set(on) { try { localStorage.setItem("dashboardAuto", on ? "1" : "0"); } catch { /* private window: just for this page */ } },
  };
  const auto = $("auto");
  let timer = null;
  function setAuto(on) {
    auto.checked = on; store.set(on);
    clearInterval(timer);
    // Not while someone is reading a side panel or typing a search: a reload would take that away
    timer = on ? setInterval(() => {
      if (!drawer.classList.contains("open") && document.activeElement?.type !== "search") window.location.reload();
    }, 60000) : null;
  }
  auto.addEventListener("change", () => setAuto(auto.checked));
  setAuto(store.get());
  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen().catch(() => { /* not allowed here: the page is full width anyway */ });
  }
  $("fs").addEventListener("click", toggleFullscreen);
  document.addEventListener("keydown", (e) => {
    if ((e.key === "f" || e.key === "F") && !e.metaKey && !e.ctrlKey && !e.altKey && !/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) toggleFullscreen();
  });
  document.addEventListener("fullscreenchange", () => { $("fs").textContent = document.fullscreenElement ? "✕" : "⛶"; });
})();
