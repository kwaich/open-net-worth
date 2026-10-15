"""Charts inside an artifact, drawn by code: a model supplies the DATA, never the geometry.

A page embeds ``<script type="application/x-onw-chart">{...json...}</script>`` and
``expand_charts`` replaces each block with a static inline SVG before the page is stored, so the
same chart shows in the chat, in the PDF and in a file opened from Telegram (no JavaScript).
A model computing SVG coordinates by hand gets them subtly wrong; this way the only numbers it
handles are the ones a tool gave it.

Each chart is emitted TWICE inside its ``<div class="onw-chart">``: this fixed 340-wide SVG
(``class="onw-print"``), which the PDF path stamps as a vector graphic, and a screen form from
``artifact_charts_screen`` whose text a browser never scales. The page shows the screen form;
the PDF removes it.

Chart types (``type``):
- ``line``: ``points`` = ``[{"date": "...", "<key>": value, ...}]`` and ``series`` =
  ``{"<key>": "Label"}`` (≤ 3) — e.g. get_returns' ``series`` pasted verbatim. ``format``
  ``percent`` reads values as fractions (0.024 = 2.4%).
- ``stack``: one 100% bar of ``items`` = ``[{"label", "value", "target"?}]`` (shares in percent).
- ``diverging``: gains right, losses left — ``items`` = ``[{"label", "value", "note"?}]`` with
  ``format`` ``money`` + ``currency``.

The JSON is untrusted: sizes are capped, numbers must be finite, every label is escaped.
"""

from __future__ import annotations

import html
import json
import math

from backend.core.errors import ValidationError
from backend.services import artifact_charts_screen as screen
from backend.services.artifact_chart_interaction import with_interaction

_OPEN = '<script type="application/x-onw-chart">'
_CLOSE = "</script>"
MAX_CHARTS = 12
MAX_POINTS = 400
_FONT = "Plus Jakarta Sans, -apple-system, system-ui, Helvetica, Arial, sans-serif"
# The app's light tokens (frontend/src/design/tokens.source.mjs); a page's dark-mode CSS
# re-colours the same classes (c-ink, c-muted, s1, k1, …).
_INK, _MUTED, _RULE, _PAPER = "#202124", "#656970", "#e2e2e2", "#ffffff"
_GOOD, _OVER, _BENCH = "#177a5c", "#bc3a34", "#969aa1"
_LINE_COLORS = ("#2666b8", _BENCH, "#96690a")  # the portfolio, its benchmark, a third series
_STACK_COLORS = ("#2666b8", "#96690a", "#177a5c", "#6f56b0", "#b23560", "#15756e")
_W = 340  # drawn at phone width: text is true size on a phone, scales up in the PDF


def expand_charts(page: str) -> str:
    """Replace every chart block with its SVG. Linear scan; raises ``ValidationError`` naming
    the chart that is wrong, so the model can fix its data."""
    out, i, n = [], 0, 0
    lower = page.lower()
    while True:
        s = lower.find(_OPEN, i)
        if s < 0:
            out.append(page[i:])
            page_out = "".join(out)
            return with_interaction(page_out) if n else page_out
        e = lower.find(_CLOSE, s + len(_OPEN))
        if e < 0:
            raise ValidationError("a chart block is missing its closing </script>")
        n += 1
        if n > MAX_CHARTS:
            raise ValidationError(f"at most {MAX_CHARTS} charts per page")
        out.append(page[i:s])
        out.append(render_chart(page[s + len(_OPEN) : e], n))
        i = e + len(_CLOSE)


def render_chart(raw: str, n: int = 1) -> str:
    try:
        spec = json.loads(raw)
    except ValueError as exc:
        raise ValidationError(f"chart {n}: the block must be JSON ({exc})") from exc
    kind = spec.get("type") if isinstance(spec, dict) else None
    draw = {"line": _line, "stack": _stack, "diverging": _diverging}.get(kind)
    if draw is None:
        raise ValidationError(f"chart {n}: type must be line, stack or diverging")
    try:
        svg, on_screen = draw(spec)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(f"chart {n} ({kind}): {exc}") from exc
    return f'<div class="onw-chart">{svg}{on_screen}</div>'


def _num(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{what} must be a number")
    return float(value)


def _fmt(value: float, spec: dict, *, signed: bool = False) -> str:
    kind = spec.get("format") or "number"
    sign = (
        ("+" if value > 0 else "−" if value < 0 else "") if signed else ("−" if value < 0 else "")
    )
    if kind == "percent":
        return f"{sign}{abs(value):.1f}%"
    if kind == "money":
        unit = html.escape(str(spec.get("currency") or ""))[:5]
        return f"{sign}{unit} {abs(value):,.2f}".replace(f"{sign} ", sign)
    return f"{sign}{abs(value):,.2f}"


def _text(
    x: float,
    y: float,
    body: str,
    *,
    cls: str,
    size: int = 12,
    anchor: str = "start",
    weight: str = "normal",
    fill: str = _MUTED,
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" font-family="{_FONT}" font-size="{size}" '
        f'font-weight="{weight}" text-anchor="{anchor}" fill="{fill}">{html.escape(body)}</text>'
    )


def _svg(height: float, body: str, label: str, extra: str = "") -> str:
    return (
        f'<svg class="onw-print" xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {_W} {height:.0f}" '
        f'width="{_W}" height="{height:.0f}" '
        f'role="img" aria-label="{html.escape(label)}"{extra}>{body}</svg>'
    )


def _bar(attrs: str, tip: str) -> str:
    """A bar with its exact figure: a native tooltip (works without scripts) and ``data-tip``
    for the tap tooltip the page's interaction layer adds."""
    safe = html.escape(tip)
    return f'<rect {attrs} data-tip="{safe}"><title>{safe}</title></rect>'


def _line(spec: dict) -> tuple[str, str]:
    points, series = spec["points"], spec["series"]
    if not isinstance(points, list) or not 2 <= len(points) <= MAX_POINTS:
        raise ValueError(f"points must be a list of 2–{MAX_POINTS} entries")
    if not isinstance(series, dict) or not 1 <= len(series) <= len(_LINE_COLORS):
        raise ValueError(f"series must name 1–{len(_LINE_COLORS)} keys")
    scale = 100.0 if spec.get("format") == "percent" else 1.0
    lines = {
        key: [_num(p[key], f"points[{i}].{key}") * scale for i, p in enumerate(points)]
        for key in series
    }
    lo = min(0.0, *(min(v) for v in lines.values()))
    hi = max(0.0, *(max(v) for v in lines.values()))
    pad = (hi - lo) * 0.08 or 1.0
    lo, hi = lo - pad, hi + pad
    left, right, top, bottom = 34.0, 236.0, 16.0, 130.0
    x = lambda i: left + (right - left) * i / (len(points) - 1)  # noqa: E731
    y = lambda v: bottom - (bottom - top) * (v - lo) / (hi - lo)  # noqa: E731
    unit = "%" if scale == 100 else ""
    body, tick_labels, grid = [], [], []
    for tick in _ticks(lo, hi):  # a readable scale: the chart needs no tapping
        ty = y(tick)
        body.append(
            f'<line x1="{left}" y1="{ty:.1f}" x2="{right}" y2="{ty:.1f}" class="c-rule" '
            f'stroke="{_RULE}" stroke-width="{1 if tick == 0 else 0.5}"/>'
        )
        label = f"{'+' if tick > 0 else '−' if tick < 0 else ''}{abs(tick):g}{unit}"
        body.append(_text(left - 4, ty + 3, label, cls="c-muted", size=9, anchor="end"))
        tick_labels.append((ty, label))
        grid.append((ty, tick == 0))
    ends = []
    for (key, label), color in zip(series.items(), _LINE_COLORS):
        values = lines[key]
        path = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
        cls = f"s{len(ends) + 1}"
        body.append(
            f'<polyline points="{path}" class="{cls}" fill="none" stroke="{color}" '
            f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        body.append(
            f'<circle cx="{right:.1f}" cy="{y(values[-1]):.1f}" r="3.5" class="{cls}-dot" '
            f'fill="{color}"/>'
        )
        ends.append([y(values[-1]) + 4, str(label), _fmt(values[-1], spec, signed=True)])
    ends.sort(key=lambda e: e[0])
    for k in range(1, len(ends)):  # keep the end labels from overlapping
        ends[k][0] = max(ends[k][0], ends[k - 1][0] + 30)
    marks, callouts = _extremes(
        points, lines[next(iter(series))], spec, x, y, left, right, top, bottom
    )
    body.extend(marks)
    for ly, label, value in ends:
        body.append(_text(right + 10, ly - 6, _fit(label, 15), cls="c-muted", size=11))
        body.append(
            _text(right + 10, ly + 9, value, cls="c-ink", size=13, weight="bold", fill=_INK)
        )
    first, last = _short_date(points[0].get("date")), _short_date(points[-1].get("date"))
    body.append(_text(left, 150, first, cls="c-muted", size=10))
    body.append(_text(right, 150, last, cls="c-muted", size=10, anchor="end"))
    height = max(158.0, ends[-1][0] + 16)
    data = {
        "left": left,
        "right": right,
        "top": top,
        "bottom": bottom,
        "dates": [_short_date(p.get("date")) for p in points],
        "series": [
            {
                "name": _fit(label, 20),
                "color": color,
                "ys": [round(y(v), 1) for v in lines[key]],
                "labels": [_fmt(v, spec, signed=True) for v in lines[key]],
            }
            for (key, label), color in zip(series.items(), _LINE_COLORS)
        ],
    }
    title = spec.get("title") or "Line chart"
    geometry = {
        "height": height,
        "left": left,
        "right": right,
        "ticks": tick_labels,
        "grid": grid,
        "series": [
            {
                "cls": f"s{k + 1}",
                "color": color,
                "xy": [(x(i), y(v)) for i, v in enumerate(lines[key])],
            }
            for k, (key, color) in enumerate(zip(series, _LINE_COLORS))
        ],
        "ends": [(ly, _fit(label, 15), value) for ly, label, value in ends],
        "first": first,
        "last": last,
        "callouts": callouts,
        "interaction": data,
    }
    return _svg(height, "".join(body), title), screen.line(title, geometry)


def _ticks(lo: float, hi: float) -> list[float]:
    """Round gridline values across [lo, hi] — 3 to 5 of them, always including 0."""
    span = hi - lo
    for step in (0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if span / step <= 5:
            break
    first = math.ceil(lo / step)
    return [round(k * step, 6) for k in range(first, math.floor(hi / step) + 1)]


def _extremes(points, values, spec, x, y, left, right, top, bottom) -> tuple[list[str], list[str]]:
    """Label the first series' high and low (value and date) so the picture reads on its own —
    skipped where it would sit on the start, the end label, or the other callout. Returns the
    SVG marks and the same callouts as plain text (the screen form prints them below)."""
    hi_i = max(range(len(values)), key=values.__getitem__)
    lo_i = min(range(len(values)), key=values.__getitem__)
    out, texts, last = [], [], len(values) - 1
    for i, word, dy in ((hi_i, "High", -8), (lo_i, "Low", 14)):
        if i in (0, last) or (word == "Low" and lo_i == hi_i):
            continue
        cx = min(max(x(i), left + 46), right - 46)
        cy = min(max(y(values[i]) + dy, top + 2), bottom + 12)
        text = f"{word} {_fmt(values[i], spec, signed=True)}, {_short_date(points[i].get('date'))}"
        out.append(
            f'<circle cx="{x(i):.1f}" cy="{y(values[i]):.1f}" r="2.5" class="c-ink" fill="{_INK}"/>'
        )
        out.append(_text(cx, cy, text, cls="c-ink", size=10, anchor="middle", fill=_INK))
        texts.append(text)
    return out, texts


def _fit(text: object, limit: int) -> str:
    """Shorten to ``limit`` characters at a word boundary, with an ellipsis — never mid-word."""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip(" ,.;:-")
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space].rstrip(" ,.;:-")
    return cut + "…"


def _short_date(value: object) -> str:
    """'2026-09-01' → '1 Sep'; anything else is shown as given."""
    from datetime import date

    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value or "")[:12]
    return f"{d.day} {d:%b}"


def _percent_shares(items: list, spec: dict) -> list:
    """A stack's percent shares as percent NUMBERS, whichever way they were given.

    Line charts take percent as FRACTIONS (get_returns' series: 0.024 = 2.4%), so a model reused
    that for a stack and passed 0.464 for 46.4% — the report printed "0.5%, target 0.5%" (live
    2026-10-06). The shares of one whole add up to ~1 as fractions or ~100 as percents, so the
    two can't be confused: fractions are scaled, targets with them."""
    if spec.get("format") != "percent":
        return items
    values = [_num(it.get("value"), f"items[{i}].value") for i, it in enumerate(items)]
    if not 0 < sum(values) <= 1.5:
        return items
    scaled = []
    for it in items:
        row = dict(it, value=float(it["value"]) * 100)
        if it.get("target") is not None:
            row["target"] = _num(it["target"], "target") * 100
        scaled.append(row)
    return scaled


def _stack(spec: dict) -> tuple[str, str]:
    items = spec["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= len(_STACK_COLORS):
        raise ValueError(f"items must be 1–{len(_STACK_COLORS)} entries")
    items = _percent_shares(items, spec)
    values = [max(_num(it["value"], f"items[{i}].value"), 0.0) for i, it in enumerate(items)]
    total = sum(values) or 1.0
    body, x = [], 0.0
    for i, value in enumerate(values):
        width = _W * value / total
        if width > 0:
            target = items[i].get("target")
            tip = f"{items[i]['label']}: {_fmt(value, spec)}"
            if target is not None:
                tip += f", target {_fmt(_num(target, f'items[{i}].target'), spec)}"
            body.append(
                _bar(
                    f'x="{x:.1f}" y="0" width="{max(width - 2, 0.5):.1f}" height="16" rx="3" '
                    f'class="k{i + 1}" fill="{_STACK_COLORS[i]}"',
                    tip,
                )
            )
        x += width
    for i, (it, value) in enumerate(zip(items, values)):
        row = 40 + i * 24
        target = it.get("target")
        detail = _fmt(value, spec)
        if target is not None:
            detail += f", target {_fmt(_num(target, f'items[{i}].target'), spec)}"
        body.append(
            f'<rect x="0" y="{row - 10}" width="10" height="10" rx="2" class="k{i + 1}" '
            f'fill="{_STACK_COLORS[i]}"/>'
        )
        body.append(_text(18, row, _fit(it["label"], 22), cls="c-ink", size=13, fill=_INK))
        body.append(_text(_W, row, detail, cls="c-muted", size=12, anchor="end"))
    title = spec.get("title") or "Stacked bar"
    rows = []
    for i, (it, value) in enumerate(zip(items, values)):
        target = it.get("target")
        detail = _fmt(value, spec)
        if target is not None:
            detail += f", target {_fmt(_num(target, f'items[{i}].target'), spec)}"
        rows.append(
            {
                "label": _fit(it["label"], 40),
                "detail": detail,
                "share": value / total,
                "tip": f"{it['label']}: {detail}",
            }
        )
    return _svg(40 + len(items) * 24 - 6, "".join(body), title), screen.stack(title, rows)


def _diverging(spec: dict) -> tuple[str, str]:
    """Names (and a note line) on the left, bars from a centre axis, values in a right column —
    so a loss's label never collides with the name beside it."""
    items = spec["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= 16:
        raise ValueError("items must be 1–16 entries")
    values = [_num(it["value"], f"items[{i}].value") for i, it in enumerate(items)]
    biggest = max(abs(v) for v in values) or 1.0
    mid, half, row_h = 178.0, 52.0, 38
    body = [
        f'<line x1="{mid}" y1="0" x2="{mid}" y2="{len(items) * row_h}" class="c-rule" '
        f'stroke="{_RULE}" stroke-width="1"/>'
    ]
    for i, (it, value) in enumerate(zip(items, values)):
        top = i * row_h + 6
        width = max(half * abs(value) / biggest, 1.5)
        gain = value >= 0
        color, cls = (_GOOD, "c-good") if gain else (_OVER, "c-over")
        bx = mid if gain else mid - width
        tip = f"{it['label']}: {_fmt(value, spec, signed=True)}"
        if it.get("note"):
            tip += f" ({it['note']})"
        body.append(
            _bar(
                f'x="{bx:.1f}" y="{top + 2}" width="{width:.1f}" height="14" rx="3" '
                f'class="{cls}" fill="{color}"',
                tip,
            )
        )
        body.append(_text(0, top + 13, _fit(it["label"], 18), cls="c-ink", size=12, fill=_INK))
        note = _fit(it.get("note"), 24)
        if note:
            body.append(_text(0, top + 28, note, cls="c-muted", size=10))
        body.append(
            _text(
                _W,
                top + 13,
                _fmt(value, spec, signed=True),
                cls=cls,
                size=12,
                anchor="end",
                weight="bold",
                fill=color,
            )
        )
    title = spec.get("title") or "Gains and losses"
    rows = [
        {
            "label": _fit(it["label"], 30),
            "note": _fit(it.get("note"), 40),
            "value_text": _fmt(value, spec, signed=True),
            "share": abs(value) / biggest,
            "gain": value >= 0,
            "tip": f"{it['label']}: {_fmt(value, spec, signed=True)}"
            + (f" ({it['note']})" if it.get("note") else ""),
        }
        for it, value in zip(items, values)
    ]
    return _svg(len(items) * row_h + 2, "".join(body), title), screen.diverging(title, rows)
