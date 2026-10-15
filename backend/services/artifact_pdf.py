"""Render an HTML page as an A4 PDF with PyMuPDF — safely, for model-written markup.

Split out of ``artifact_formats``. Everything here is linear in the input and bounded (see
the section comment below), because the page is untrusted.
"""

from __future__ import annotations

import io
import re

from backend.core.errors import ValidationError

# ── PDF ────────────────────────────────────────────────────────────────────────
#
# The HTML is untrusted (a model wrote it, possibly after reading hostile text), so every step
# here is LINEAR in the input and bounded: no backtracking regex over the page, no unbounded
# CSS expansion, a hard page ceiling and a wall-clock budget. The PDF engine must also never
# read a file: @font-face/@import and every url() are removed before it sees the CSS.

MAX_PDF_PAGES = 200
_PDF_TIME_BUDGET_S = 20.0
_MAX_MARKUP_TAGS = 400
_MAX_CSS_CHARS = 512_000
_MAX_VAR_VALUE = 200
_DROPPED_AT_RULES = ("media", "font-face", "import", "supports", "keyframes", "namespace", "page")
_VAR_DEF_RE = re.compile(r"(--[\w-]{1,80})\s*:\s*([^;}]{1,201})")
_VAR_USE_RE = re.compile(r"var\(\s*(--[\w-]{1,80})\s*(?:,\s*([^)]{0,200}))?\)", re.I)
_POSITION_RELATIVE_RE = re.compile(r"position\s{0,20}:\s{0,20}relative\s{0,20};?", re.I)


def _too_much_markup(lower: str) -> None:
    for needle in ("<style", "<script", "<link", "<body", "data-screen-only", "url(", "var("):
        if lower.count(needle) > _MAX_MARKUP_TAGS:
            raise ValidationError(
                f"the page has more than {_MAX_MARKUP_TAGS} {needle!r} — simplify it for a PDF"
            )


def _tag_at(lower: str, start: int, tag: str) -> bool:
    nxt = lower[start + len(tag) + 1 : start + len(tag) + 2]
    return nxt in ("", " ", "\t", "\r", "\n", ">", "/")


def _take_elements(html: str, tag: str) -> tuple[str, list[str]]:
    """Remove every ``<tag …>…</tag>`` element; return (rest, inner texts). An unterminated
    element drops everything after it. Linear: the cursor only moves forward."""
    lower = html.lower()
    out, inner, i = [], [], 0
    while True:
        s = lower.find(f"<{tag}", i)
        if s < 0:
            out.append(html[i:])
            break
        if not _tag_at(lower, s, tag):
            out.append(html[i : s + 1])
            i = s + 1
            continue
        out.append(html[i:s])
        gt = lower.find(">", s)
        close = lower.find(f"</{tag}", gt) if gt >= 0 else -1
        if close < 0:
            break
        inner.append(html[gt + 1 : close])
        end = lower.find(">", close)
        i = len(html) if end < 0 else end + 1
    return "".join(out), inner


def _drop_void(html: str, tag: str) -> str:
    """Remove every ``<tag …>`` (a void element such as <link>)."""
    lower = html.lower()
    out, i = [], 0
    while True:
        s = lower.find(f"<{tag}", i)
        if s < 0 or not _tag_at(lower, s, tag):
            if s < 0:
                out.append(html[i:])
                return "".join(out)
            out.append(html[i : s + 1])
            i = s + 1
            continue
        out.append(html[i:s])
        gt = lower.find(">", s)
        if gt < 0:
            return "".join(out)
        i = gt + 1


def _drop_screen_only(html: str) -> str:
    """Remove each EMPTY element carrying ``data-screen-only`` (screen decoration, e.g. an
    absolutely-positioned tick, that the PDF engine would misplace)."""
    lower = html.lower()
    out, i = [], 0
    while True:
        p = lower.find("data-screen-only", i)
        if p < 0:
            out.append(html[i:])
            return "".join(out)
        s = lower.rfind("<", i, p)
        gt = lower.find(">", p)
        name = re.match(r"<([a-z][a-z0-9]{0,15})", lower[s : s + 17]) if s >= 0 else None
        close = f"</{name.group(1)}" if name else ""
        after = lower[gt + 1 :].lstrip()[: len(close)] if gt >= 0 and close else ""
        if not name or gt < 0 or after != close:
            out.append(html[i : p + 1])
            i = p + 1
            continue
        close_at = lower.find(close, gt)
        end = lower.find(">", close_at)
        out.append(html[i:s])
        i = len(html) if end < 0 else end + 1


def _strip_at_blocks(css: str, keyword: str) -> str:
    """Remove every ``@<keyword>`` rule, any letter case — a block with nested braces, or a
    one-line statement such as ``@import url(x);``."""
    lower = css.lower()
    out, i = [], 0
    needle = f"@{keyword}"
    while True:
        start = lower.find(needle, i)
        if start < 0:
            out.append(css[i:])
            return "".join(out)
        out.append(css[i:start])
        brace, semi = lower.find("{", start), lower.find(";", start)
        if semi >= 0 and (brace < 0 or semi < brace):
            i = semi + 1
            continue
        if brace < 0:
            return "".join(out)
        depth, j = 0, brace
        while j < len(css):
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
            if depth == 0:
                break
        i = j


def _drop_urls(css: str) -> str:
    """Replace every ``url(…)`` with ``none`` so nothing in the CSS can name a file or URL."""
    lower = css.lower()
    out, i = [], 0
    while True:
        s = lower.find("url(", i)
        if s < 0:
            out.append(css[i:])
            return "".join(out)
        out.append(css[i:s] + "none")
        e = lower.find(")", s)
        i = len(css) if e < 0 else e + 1


def _flatten_css(css: str) -> str:
    """Plain, safe CSS for the PDF engine: drop at-rules (the dark palette, fonts, imports),
    neutralise url(), resolve var() in ONE bounded pass, drop position:relative."""
    if len(css) > _MAX_CSS_CHARS:
        raise ValidationError("the page's CSS is too large for a PDF")
    for keyword in _DROPPED_AT_RULES:
        css = _strip_at_blocks(css, keyword)
    css = _drop_urls(css)
    values: dict[str, str] = {}
    for name, value in _VAR_DEF_RE.findall(css):
        value = value.strip()
        # A variable built from another variable is not expanded (no compounding growth).
        if len(value) <= _MAX_VAR_VALUE and "var(" not in value.lower():
            values.setdefault(name, value)
    css = _VAR_USE_RE.sub(lambda m: values.get(m.group(1), m.group(2) or ""), css)
    if len(css) > _MAX_CSS_CHARS:
        raise ValidationError("the page's CSS is too large for a PDF")
    # The engine skips painting a positioned box's background, and the only reason a page
    # positions anything is screen-only decoration (dropped from the PDF anyway).
    return _POSITION_RELATIVE_RE.sub("", css)


# An SVG goes through MuPDF's own parser, which can resolve links; anything that could name a
# file or URL makes the SVG unsafe, and it never reaches the engine.
_SVG_UNSAFE = ("href", "<image", "<use", "url(", "<style", "<foreignobject", "<script", "@import")
_CHART_OPEN = '<div class="onw-chart"><svg '
_PDF_CONTENT_WIDTH = 499.0  # A4 minus the 48pt margins, in points


def _svg_is_safe(svg: str) -> bool:
    lower = svg.lower()
    return not any(token in lower for token in _SVG_UNSAFE)


def _lift_charts(body: str) -> tuple[str, list[str]]:
    """Swap each server-drawn chart for an empty placeholder of the height it needs, and drop
    any other inline SVG that isn't safe. The engine sizes inline SVG unpredictably (it shrank
    a chart to a third of the page), so charts are stamped in afterwards at an exact size."""
    lower = body.lower()
    out, charts, i = [], [], 0
    while True:
        s = lower.find("<svg", i)
        if s < 0:
            out.append(body[i:])
            return "".join(out), charts
        e = lower.find("</svg>", s)
        if e < 0:
            out.append(body[i:s])
            return "".join(out), charts
        svg = body[s : e + len("</svg>")]
        is_chart = lower.startswith(_CHART_OPEN, s - len('<div class="onw-chart">'))
        out.append(body[i:s])
        if is_chart and _svg_is_safe(svg) and len(charts) < _MAX_MARKUP_TAGS:
            box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg[:400])
            ratio = float(box.group(2)) / float(box.group(1)) if box else 0.5
            # A fixed-height <div> made the engine drop the content after it; one line of the
            # chart's height reserves the space, flows to the next page whole, and reports
            # its full-width box.
            out.append(
                f'<p id="onwchart{len(charts)}" style="margin:0;'
                f'line-height:{_PDF_CONTENT_WIDTH * ratio:.1f}pt">&#160;</p>'
            )
            charts.append(svg)
        elif not is_chart and _svg_is_safe(svg):
            out.append(svg)
        i = e + len("</svg>")


def _pdf_parts(html: str) -> tuple[str, str]:
    """(body markup, css) from a page — every <style> anywhere becomes the CSS, and no
    <script>, <link> or <style> reaches the engine."""
    _too_much_markup(html.lower())
    html, styles = _take_elements(html, "style")
    html, _scripts = _take_elements(html, "script")
    # A chart's screen form (artifact_charts_screen); the PDF stamps its print SVG instead.
    html, _screen = _take_elements(html, "onw-screen")
    html = _drop_screen_only(_drop_void(html, "link"))
    lower = html.lower()
    s = lower.find("<body")
    gt = lower.find(">", s) if s >= 0 else -1
    e = lower.rfind("</body")
    body = html[gt + 1 : e] if 0 <= gt < e else html
    return body, _flatten_css("\n".join(styles))


def render_pdf(html: str, *, title: str) -> bytes:
    """Lay an HTML page out on A4 with PyMuPDF. The page should use tables, not flex/grid —
    the engine supports a print-oriented subset of CSS."""
    import time

    import pymupdf

    body, css = _pdf_parts(html)
    body, charts = _lift_charts(body)
    story = pymupdf.Story(html=body, user_css=css)
    placed: dict[str, tuple[int, pymupdf.Rect]] = {}

    def _record(position) -> None:
        pid = getattr(position, "id", "") or ""
        if pid.startswith("onwchart") and pid not in placed:
            placed[pid] = (pages - 1, pymupdf.Rect(position.rect))

    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    page = pymupdf.paper_rect("a4")
    frame = page + (48, 52, -48, -60)
    started, pages, more = time.monotonic(), 0, 1
    while more:
        pages += 1
        if pages > MAX_PDF_PAGES:
            raise ValidationError(f"the PDF would run past {MAX_PDF_PAGES} pages — shorten it")
        if time.monotonic() - started > _PDF_TIME_BUDGET_S:
            raise ValidationError("laying the PDF out took too long — simplify the page")
        device = writer.begin_page(page)
        more, filled = story.place(frame)
        filled = pymupdf.Rect(filled)
        story.element_positions(_record)
        story.draw(device)
        writer.end_page()
        if more and (filled.is_empty or filled.height <= 0):
            raise ValidationError(
                "one block is taller than a page — split long table cells or paragraphs"
            )
    writer.close()
    doc = pymupdf.open(stream=buf.getvalue(), filetype="pdf")
    _stamp_charts(doc, charts, placed)
    for number, pdf_page in enumerate(doc, start=1):
        muted = (0.45, 0.53, 0.5)
        pdf_page.insert_text(
            (48, page.height - 32), title[:90], fontsize=8, fontname="helv", color=muted
        )
        label = f"Page {number} of {doc.page_count}"
        width = pymupdf.get_text_length(label, fontname="helv", fontsize=8)
        pdf_page.insert_text(
            (page.width - 48 - width, page.height - 32),
            label,
            fontsize=8,
            fontname="helv",
            color=muted,
        )
    doc.set_metadata({"title": title, "creator": "Open Net Worth", "producer": "Open Net Worth"})
    return doc.tobytes(garbage=3, deflate=True)


def _stamp_charts(doc, charts: list[str], placed: dict) -> None:
    """Draw each lifted chart, as vector PDF, at the full width of its placeholder."""
    import pymupdf

    for index, svg in enumerate(charts):
        where = placed.get(f"onwchart{index}")
        if where is None:
            continue
        page_no, rect = where
        chart = pymupdf.open(
            "pdf", pymupdf.open(stream=svg.encode(), filetype="svg").convert_to_pdf()
        )
        width = rect.width or _PDF_CONTENT_WIDTH
        box = chart[0].rect
        height = width * box.height / box.width if box.width else rect.height
        doc[page_no].show_pdf_page(
            pymupdf.Rect(rect.x0, rect.y0, rect.x0 + width, rect.y0 + height), chart, 0
        )
