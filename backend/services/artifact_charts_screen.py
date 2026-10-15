"""The SCREEN form of each server-drawn chart: text that is never scaled, edges on the page's.

``artifact_charts`` draws a chart as one fixed 340-wide SVG — exact on a phone and as a PDF
graphic, but a browser can only scale a picture: in a 576px column its 12px labels became 20px
and its right-aligned values stopped short of every other amount on the page (owner,
2026-10-05). So every chart also gets a screen form, wrapped in ``<onw-screen>`` (the PDF path
removes that element and keeps stamping the SVG):

- bars (``diverging``, ``stack``) are plain HTML — the page's own type, values right-aligned
  with the page's other figures;
- a ``line`` is three SVGs side by side: fixed-size tick labels, a plot that stretches
  horizontally (``preserveAspectRatio="none"``, non-scaling strokes, round zero-length lines
  for dots so they stay round), and fixed-size end labels.

Colours come from the page's tokens with the app's light values as fallbacks, so a report's
dark mode re-colours them with no extra CSS. Labels are escaped; numbers are the renderer's.
"""

from __future__ import annotations

import html
import json

SCREEN_CSS = """
onw-screen { display: block; }
.onw-chart > svg.onw-print { display: none; }
.onw-hbar { width: 100%; border-collapse: collapse; }
.onw-hbar td { padding: var(--sp-2, 8px) 0; vertical-align: middle; }
.onw-hbar .onw-name { display: block; color: var(--ink, #202124); }
.onw-hbar .onw-note { display: block; color: var(--ink-2, #656970); font-size: var(--fs-sm, 12px);
  white-space: nowrap; }
.onw-hbar td.onw-bars { width: 30%; padding: 0 var(--sp-3, 12px); }
.onw-halves { width: 100%; border-collapse: collapse; }
.onw-halves td { width: 50%; padding: 0; }
.onw-halves td.onw-neg { text-align: right; }
.onw-halves td.onw-pos { border-left: 1px solid var(--rule, #e2e2e2); }
.onw-bar { display: inline-block; height: 12px; vertical-align: middle; min-width: 2px; }
.onw-bar.onw-gain { background: var(--good, #177a5c); border-radius: 0 3px 3px 0; }
.onw-bar.onw-loss { background: var(--bad, #bc3a34); border-radius: 3px 0 0 3px; }
.onw-hbar td.onw-val { text-align: right; white-space: nowrap; font-weight: 650; }
.onw-val.onw-gain { color: var(--good, #177a5c); }
.onw-val.onw-loss { color: var(--bad, #bc3a34); }
.onw-stackbar { display: flex; height: 14px; border-radius: 4px; overflow: hidden; gap: 2px; }
.onw-stackbar span { display: block; height: 14px; }
.onw-legend { width: 100%; border-collapse: collapse; margin-top: 8px; }
.onw-legend td { padding: var(--sp-1, 4px) 0; }
.onw-legend td.onw-val { text-align: right; color: var(--ink-2, #656970); white-space: nowrap;
  font-size: var(--fs-sm, 12px); }
.onw-sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px;
          margin-right: 8px; vertical-align: -1px; }
.onw-k1 { background: #2666b8; } .onw-k2 { background: #96690a; }
.onw-k3 { background: #177a5c; } .onw-k4 { background: #6f56b0; }
.onw-k5 { background: #b23560; } .onw-k6 { background: #15756e; }
.onw-linebox { width: 100%; border-collapse: collapse; }
.onw-linebox td { padding: 0; vertical-align: top; }
.onw-linebox td.onw-plot { width: 100%; }
.onw-chart .onw-linebox svg { display: block; width: auto; height: auto; }  /* beats a page's `.onw-chart svg {width:100%}` */
.onw-dates { color: var(--ink-3, #969aa1); font-size: var(--fs-xs, 11px); }
.onw-dates span + span { float: right; }
.onw-callout { margin: var(--sp-1, 4px) 0 0; color: var(--ink-3, #969aa1); font-size: var(--fs-xs, 11px); }
.onw-t-ink { fill: var(--ink, #202124); }
.onw-t-muted { fill: var(--ink-2, #656970); }
.onw-grid { stroke: var(--rule, #e2e2e2); }
@media (prefers-color-scheme: dark) {
  .onw-k1 { background: #3b86dd; } .onw-k2 { background: #e0a032; }
  .onw-k3 { background: #34b08a; } .onw-k4 { background: #9a7fd0; }
  .onw-k5 { background: #d4537e; } .onw-k6 { background: #2ba39a; }
}
"""

_FONT = "Plus Jakarta Sans, -apple-system, system-ui, Helvetica, Arial, sans-serif"


def _esc(text: object) -> str:
    return html.escape(str(text))


def _tip(text: str) -> str:
    safe = _esc(text)
    return f' data-tip="{safe}" title="{safe}"'


def wrap(label: str, body: str) -> str:
    return f'<onw-screen role="img" aria-label="{_esc(label)}">{body}</onw-screen>'


def diverging(label: str, rows: list[dict]) -> str:
    """rows: {label, note, value_text, share (0–1 of the biggest), gain, tip}."""
    out = ['<table class="onw-hbar">']
    for r in rows:
        width = f"{max(r['share'] * 100, 2):.1f}%"
        bar = f'<span class="onw-bar onw-{"gain" if r["gain"] else "loss"}" style="width:{width}"'
        bar += _tip(r["tip"]) + "></span>"
        neg, pos = ("", bar) if r["gain"] else (bar, "")
        note = f'<span class="onw-note">{_esc(r["note"])}</span>' if r["note"] else ""
        out.append(
            f'<tr><td><span class="onw-name">{_esc(r["label"])}</span>{note}</td>'
            f'<td class="onw-bars"><table class="onw-halves"><tr>'
            f'<td class="onw-neg">{neg}</td><td class="onw-pos">{pos}</td></tr></table></td>'
            f'<td class="onw-val onw-{"gain" if r["gain"] else "loss"}">{_esc(r["value_text"])}</td>'
            "</tr>"
        )
    out.append("</table>")
    return wrap(label, "".join(out))


def stack(label: str, rows: list[dict]) -> str:
    """rows: {label, detail, share (0–1 of the total), tip} in colour order."""
    bar = "".join(
        f'<span class="onw-k{i + 1}" style="flex:{r["share"]:.4f} 0 0"{_tip(r["tip"])}></span>'
        for i, r in enumerate(rows)
        if r["share"] > 0
    )
    legend = "".join(
        f'<tr><td><i class="onw-sw onw-k{i + 1}"></i>{_esc(r["label"])}</td>'
        f'<td class="onw-val">{_esc(r["detail"])}</td></tr>'
        for i, r in enumerate(rows)
    )
    return wrap(
        label, f'<div class="onw-stackbar">{bar}</div><table class="onw-legend">{legend}</table>'
    )


def _text(x: float, y: float, body: str, cls: str, size: int, anchor="start", weight="normal"):
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" font-family="{_FONT}" font-size="{size}" '
        f'font-weight="{weight}" text-anchor="{anchor}">{_esc(body)}</text>'
    )


def _dot(x: float, y: float, color: str, size: int = 7, cls: str = "") -> str:
    """A round dot that stays round in a horizontally stretched SVG: a zero-length line with a
    round cap and a stroke that does not scale."""
    klass = f' class="{cls}"' if cls else ""
    return (
        f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="{color}" '
        f'stroke-width="{size}" stroke-linecap="round" vector-effect="non-scaling-stroke"{klass}/>'
    )


def line(label: str, g: dict) -> str:
    """g: geometry from ``artifact_charts._line`` — plot box, ticks, series, ends, dates."""
    h, w = g["height"], g["right"] - g["left"]
    ticks = "".join(
        _text(36, ty + 4, text, "onw-t-muted", 11, anchor="end") for ty, text in g["ticks"]
    )
    grid = "".join(
        f'<line x1="0" y1="{ty:.1f}" x2="{w:.1f}" y2="{ty:.1f}" class="onw-grid" '
        f'stroke="#e2e2e2" stroke-width="{1 if zero else 0.5}" vector-effect="non-scaling-stroke"/>'
        for ty, zero in g["grid"]
    )
    lines = "".join(
        f'<polyline points="{" ".join(f"{x - g['left']:.1f},{y:.1f}" for x, y in s["xy"])}" '
        f'class="{s["cls"]}" fill="none" stroke="{s["color"]}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>'
        + _dot(w, s["xy"][-1][1], s["color"], cls=f"{s['cls']}-dot")
        for s in g["series"]
    )
    ends = "".join(
        _text(8, ly - 6, name, "onw-t-muted", 11)
        + _text(8, ly + 10, value, "onw-t-ink", 14, weight="bold")
        for ly, name, value in g["ends"]
    )
    data = json.dumps({**g["interaction"], "left": 0, "right": w}, ensure_ascii=False)
    plot = (
        f'<svg class="onw-plotsvg" viewBox="0 0 {w:.0f} {h:.0f}" preserveAspectRatio="none" '
        f'width="100%" height="{h:.0f}" style="width:100%;height:{h:.0f}px" '
        f'data-onw-line="{_esc(data)}">{grid}{lines}</svg>'
    )
    body = (
        '<table class="onw-linebox"><tr>'
        f'<td><svg width="40" height="{h:.0f}" viewBox="0 0 40 {h:.0f}">{ticks}</svg></td>'
        f'<td class="onw-plot">{plot}'
        f'<div class="onw-dates"><span>{_esc(g["first"])}</span><span>{_esc(g["last"])}</span></div></td>'
        f'<td><svg width="112" height="{h:.0f}" viewBox="0 0 112 {h:.0f}">{ends}</svg></td>'
        "</tr></table>"
    )
    if g["callouts"]:
        body += f'<p class="onw-callout">{_esc(". ".join(g["callouts"]))}.</p>'
    return wrap(label, body)
