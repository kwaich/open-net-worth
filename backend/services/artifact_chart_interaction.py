"""The interaction layer added to a page that has server-drawn charts (``artifact_charts``).

Fixed code (never model-written), added once per page. The charts are complete without it —
the screen forms are plain HTML/SVG with native tooltips, and the PDF stamps the print SVG;
where scripts run it adds a crosshair to line charts and a tap/hover tooltip to every bar.
Tooltips are built with textContent, never innerHTML, so a hostile label can't become markup.
The styles include ``artifact_charts_screen.SCREEN_CSS``, which shows the screen form and hides
the print SVG.
"""

from __future__ import annotations

from backend.services.artifact_charts_screen import SCREEN_CSS

_INTERACTION = (
    """
<style>"""
    + SCREEN_CSS
    + """
.onw-chart { position: relative; }
.onw-chart svg { touch-action: pan-y; }
.onw-tip { position: absolute; z-index: 2; pointer-events: none; white-space: pre;
  transform: translate(-50%, -100%); margin-top: -8px; padding: 6px 9px; border-radius: 7px;
  background: #202124; color: #ffffff; font: 12px/1.45 system-ui, -apple-system, sans-serif;
  font-variant-numeric: tabular-nums; box-shadow: 0 2px 8px rgba(17, 24, 39, .22); }
.onw-guide { stroke: #969aa1; stroke-width: 1; vector-effect: non-scaling-stroke; }
.onw-hot { opacity: .78; }
@media (prefers-color-scheme: dark) { .onw-tip { background: #f3f5f9; color: #0e1014; } }
</style>
<script>
(function () {
  if (window.__onwCharts) return;
  window.__onwCharts = true;
  var NS = "http://www.w3.org/2000/svg";
  var hiders = [];
  document.addEventListener("pointerdown", function (e) {
    hiders.forEach(function (h) {
      if (!h.wrap.contains(e.target)) h.hide();  // a tap outside the chart closes its tip
    });
  });
  function toSvg(svg, e) {
    var p = svg.createSVGPoint(); p.x = e.clientX; p.y = e.clientY;
    return p.matrixTransform(svg.getScreenCTM().inverse());
  }
  function at(wrap, tip, px, py) {
    var w = wrap.getBoundingClientRect();
    tip.style.left = Math.max(70, Math.min(w.width - 70, px - w.left)) + "px";
    tip.style.top = (py - w.top) + "px";
    tip.hidden = false;
  }
  function place(wrap, tip, svg, x, y) {
    // x and y scale separately: a screen-form plot stretches horizontally only.
    var r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
    at(wrap, tip, r.left + x * r.width / vb.width, r.top + y * r.height / vb.height);
  }
  function line(wrap, svg, tip, d) {
    var guide = document.createElementNS(NS, "line");
    guide.setAttribute("class", "onw-guide");
    guide.setAttribute("y1", d.top); guide.setAttribute("y2", d.bottom);
    guide.style.display = "none"; svg.appendChild(guide);
    var dots = d.series.map(function (s) {
      // A zero-length round-capped line stays a circle when the plot is stretched.
      var c = document.createElementNS(NS, "line");
      c.setAttribute("stroke", s.color); c.setAttribute("stroke-width", "8");
      c.setAttribute("stroke-linecap", "round");
      c.setAttribute("vector-effect", "non-scaling-stroke"); c.style.display = "none";
      svg.appendChild(c); return c;
    });
    function show(e) {
      var n = d.dates.length, p = toSvg(svg, e);
      var i = Math.max(0, Math.min(n - 1, Math.round((p.x - d.left) / (d.right - d.left) * (n - 1))));
      var x = d.left + (d.right - d.left) * i / (n - 1), rows = [d.dates[i]];
      guide.setAttribute("x1", x); guide.setAttribute("x2", x); guide.style.display = "";
      d.series.forEach(function (s, k) {
        dots[k].setAttribute("x1", x); dots[k].setAttribute("x2", x);
        dots[k].setAttribute("y1", s.ys[i]); dots[k].setAttribute("y2", s.ys[i]);
        dots[k].style.display = ""; rows.push(s.name + "  " + s.labels[i]);
      });
      tip.textContent = rows.join("\\n"); place(wrap, tip, svg, x, d.top);
    }
    function hide() {
      guide.style.display = "none"; tip.hidden = true;
      dots.forEach(function (c) { c.style.display = "none"; });
    }
    svg.addEventListener("pointermove", show); svg.addEventListener("pointerdown", show);
    svg.addEventListener("pointerleave", function (e) { if (e.pointerType === "mouse") hide(); });
    hiders.push({ wrap: wrap, hide: hide });
  }
  function bars(wrap, svg, tip) {
    svg.querySelectorAll("[data-tip]").forEach(function (el) {
      function on() {
        svg.querySelectorAll(".onw-hot").forEach(function (o) { o.classList.remove("onw-hot"); });
        var b = el.getBBox(); tip.textContent = el.getAttribute("data-tip");
        el.classList.add("onw-hot"); place(wrap, tip, svg, b.x + b.width / 2, b.y);
      }
      function off() { tip.hidden = true; el.classList.remove("onw-hot"); }
      el.addEventListener("pointerenter", on); el.addEventListener("pointerdown", on);
      // A finger lifting fires pointerleave at once; on touch the tip stays until the next
      // tap elsewhere instead of vanishing as it appears.
      el.addEventListener("pointerleave", function (e) { if (e.pointerType === "mouse") off(); });
      hiders.push({ wrap: wrap, hide: off });
    });
  }
  function htmlBars(wrap, root, tip) {
    root.querySelectorAll("[data-tip]").forEach(function (el) {
      function on() {
        root.querySelectorAll(".onw-hot").forEach(function (o) { o.classList.remove("onw-hot"); });
        var b = el.getBoundingClientRect(); tip.textContent = el.getAttribute("data-tip");
        el.classList.add("onw-hot"); at(wrap, tip, b.left + b.width / 2, b.top);
      }
      function off() { tip.hidden = true; el.classList.remove("onw-hot"); }
      el.removeAttribute("title");  // the script's tip replaces the native one
      el.addEventListener("pointerenter", on); el.addEventListener("pointerdown", on);
      el.addEventListener("pointerleave", function (e) { if (e.pointerType === "mouse") off(); });
      hiders.push({ wrap: wrap, hide: off });
    });
  }
  document.querySelectorAll(".onw-chart").forEach(function (wrap) {
    var tip = document.createElement("div");
    tip.className = "onw-tip"; tip.hidden = true; tip.setAttribute("role", "status");
    var scr = wrap.querySelector("onw-screen");
    if (scr) {
      wrap.appendChild(tip);
      var plot = scr.querySelector("svg[data-onw-line]");
      if (plot) { try { line(wrap, plot, tip, JSON.parse(plot.getAttribute("data-onw-line"))); } catch (e) {} }
      else { htmlBars(wrap, scr, tip); }
      return;
    }
    var svg = wrap.querySelector("svg"); if (!svg) return;  // a page drawn before 2026-10-05
    wrap.appendChild(tip);
    var raw = svg.getAttribute("data-onw-line");
    if (raw) { try { line(wrap, svg, tip, JSON.parse(raw)); } catch (e) {} }
    else { bars(wrap, svg, tip); }
  });
})();
</script>
"""
)


def with_interaction(page: str) -> str:
    """Add the interaction layer once, just before </body> (or at the end)."""
    if "window.__onwCharts" in page:
        return page
    at = page.lower().rfind("</body>")
    return page + _INTERACTION if at < 0 else page[:at] + _INTERACTION + page[at:]
