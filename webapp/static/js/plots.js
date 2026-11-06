// Draws the Plotly figures Python built, wherever a page has them, also when HTMX brings them in later.
// A colour written "token:name" is the app's CSS variable --name, so charts follow the theme.
(function () {
  "use strict";
  const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
  function withColours(value) {
    if (typeof value === "string") return value.startsWith("token:") ? token(value.slice(6)) : value;
    if (Array.isArray(value)) return value.map(withColours);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, withColours(v)]));
    return value;
  }
  function draw(plot) {
    const source = plot.querySelector("script[type='application/json']");
    if (!source || !window.Plotly) return;
    const figure = withColours(JSON.parse(source.textContent));
    let area = plot.querySelector(".plot-area");
    if (!area) { area = document.createElement("div"); area.className = "plot-area"; plot.appendChild(area); }
    window.Plotly.react(area, figure.data, figure.layout, { displayModeBar: false, responsive: true });
    plot.dataset.drawn = "1";
  }
  const drawNew = () => document.querySelectorAll(".plot:not([data-drawn])").forEach(draw);
  const drawAll = () => document.querySelectorAll(".plot").forEach(draw);
  drawNew();
  document.body.addEventListener("htmx:afterSettle", drawNew);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", drawAll);
  new MutationObserver(drawAll).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
})();
