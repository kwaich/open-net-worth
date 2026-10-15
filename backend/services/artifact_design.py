"""The artifact design system: ONE stylesheet for every report and page Lucius makes.

Why it exists: the weekly report used about 14 font sizes (9 to 28px) between its template, the
server-drawn charts and a dozen one-off patches, so it read as unbalanced (owner, 2026-10-05).
A page now declares ``<!-- onw:design -->`` in its ``<head>`` and :func:`apply_design` swaps that
marker for this stylesheet when the artifact is saved — the template carries markup only, and a
page Lucius writes freely can use the same components and look like the app.

The rules a change here must keep:

- **Five type sizes, no others**: ``--fs-xs`` 11 (meta), ``--fs-sm`` 12 (secondary),
  ``--fs-md`` 14 (body — the app's own body size, owner 2026-10-05), ``--fs-lg`` 20 (section
  titles, key figures), ``--fs-xl`` 28 (the page title). ``tests/test_artifact_design.py`` fails on any other ``font-size``.
- **One spacing scale**: 4, 8, 12, 16, 24, 32, 48.
- **The app's tokens** (``frontend/src/design/tokens.source.mjs``) for colour, light and dark.
- **Tables, not flex/grid**, for anything side by side — the PDF engine (PyMuPDF) lays out only
  blocks and tables. Screen-only decoration (a bar's comparison mark) is absolutely positioned
  and simply absent from a PDF.
- **No requests**: no web font, no ``url()``, no ``@import`` — a private report fetches nothing.
"""

from __future__ import annotations

import re

from backend.core.errors import ValidationError

MARKER = "<!-- onw:design -->"
LOGO_MARKER = "<!-- onw:logo -->"
# The app's mark (frontend/public/favicon.svg, the tiled app icon), inline so a private report
# fetches nothing. The server places it; a template carries only LOGO_MARKER, so a model never
# retypes ~6 KB of path data (owner, 2026-10-05) and a rebrand changes this one string.
LOGO_SVG = '<svg class="onw-logo" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="20" height="20" aria-hidden="true"><defs><mask id="onw-logo-cuts" maskUnits="userSpaceOnUse" x="-320" y="-320" width="640" height="640"><circle r="320" fill="#fff"/><path fill="#000" stroke="#000" stroke-width="12" stroke-linejoin="round" d="M -2.3 -186.46 L 133.47 -130.22 L 138.06 -141.31 L 2.3 -197.54 Z M 130.22 -133.47 L 186.46 2.3 L 197.54 -2.3 L 141.31 -138.06 Z M 186.46 -2.3 L 130.22 133.47 L 141.31 138.06 L 197.54 2.3 Z M 133.47 130.22 L -2.3 186.46 L 2.3 197.54 L 138.06 141.31 Z M 2.3 186.46 L -133.47 130.22 L -138.06 141.31 L -2.3 197.54 Z M -130.22 133.47 L -186.46 -2.3 L -197.54 2.3 L -141.31 138.06 Z M -186.46 2.3 L -130.22 -133.47 L -141.31 -138.06 L -197.54 -2.3 Z M -133.47 -130.22 L 2.3 -186.46 L -2.3 -197.54 L -138.06 -141.31 Z M 90 -227.44 L -1.85 -197.71 L 1.85 -186.29 L 93.69 -216.02 Z M 86.5 -219 L 130.42 -133.03 L 141.11 -138.49 L 97.19 -224.46 Z M 96.23 -225.83 L -20.2 -350.4 L -28.97 -342.2 L 87.46 -217.63 Z M 97.39 -219.43 L 141.78 -326.61 L 130.69 -331.2 L 86.3 -224.03 Z M 92.05 -215.73 L 262.46 -221.49 L 262.05 -233.49 L 91.64 -227.73 Z M 224.46 -97.19 L 138.49 -141.11 L 133.03 -130.42 L 219 -86.5 Z M 216.02 -93.69 L 186.29 -1.85 L 197.71 1.85 L 227.44 -90 Z M 227.73 -91.64 L 233.49 -262.05 L 221.49 -262.46 L 215.73 -92.05 Z M 224.03 -86.3 L 331.2 -130.69 L 326.61 -141.78 L 219.43 -97.39 Z M 217.63 -87.46 L 342.2 28.97 L 350.4 20.2 L 225.83 -96.23 Z M 227.44 90 L 197.71 -1.85 L 186.29 1.85 L 216.02 93.69 Z M 219 86.5 L 133.03 130.42 L 138.49 141.11 L 224.46 97.19 Z M 225.83 96.23 L 350.4 -20.2 L 342.2 -28.97 L 217.63 87.46 Z M 219.43 97.39 L 326.61 141.78 L 331.2 130.69 L 224.03 86.3 Z M 215.73 92.05 L 221.49 262.46 L 233.49 262.05 L 227.73 91.64 Z M 97.19 224.46 L 141.11 138.49 L 130.42 133.03 L 86.5 219 Z M 93.69 216.02 L 1.85 186.29 L -1.85 197.71 L 90 227.44 Z M 91.64 227.73 L 262.05 233.49 L 262.46 221.49 L 92.05 215.73 Z M 86.3 224.03 L 130.69 331.2 L 141.78 326.61 L 97.39 219.43 Z M 87.46 217.63 L -28.97 342.2 L -20.2 350.4 L 96.23 225.83 Z M -90 227.44 L 1.85 197.71 L -1.85 186.29 L -93.69 216.02 Z M -86.5 219 L -130.42 133.03 L -141.11 138.49 L -97.19 224.46 Z M -96.23 225.83 L 20.2 350.4 L 28.97 342.2 L -87.46 217.63 Z M -97.39 219.43 L -141.78 326.61 L -130.69 331.2 L -86.3 224.03 Z M -92.05 215.73 L -262.46 221.49 L -262.05 233.49 L -91.64 227.73 Z M -224.46 97.19 L -138.49 141.11 L -133.03 130.42 L -219 86.5 Z M -216.02 93.69 L -186.29 1.85 L -197.71 -1.85 L -227.44 90 Z M -227.73 91.64 L -233.49 262.05 L -221.49 262.46 L -215.73 92.05 Z M -224.03 86.3 L -331.2 130.69 L -326.61 141.78 L -219.43 97.39 Z M -217.63 87.46 L -342.2 -28.97 L -350.4 -20.2 L -225.83 96.23 Z M -227.44 -90 L -197.71 1.85 L -186.29 -1.85 L -216.02 -93.69 Z M -219 -86.5 L -133.03 -130.42 L -138.49 -141.11 L -224.46 -97.19 Z M -225.83 -96.23 L -350.4 20.2 L -342.2 28.97 L -217.63 -87.46 Z M -219.43 -97.39 L -326.61 -141.78 L -331.2 -130.69 L -224.03 -86.3 Z M -215.73 -92.05 L -221.49 -262.46 L -233.49 -262.05 L -227.73 -91.64 Z M -97.19 -224.46 L -141.11 -138.49 L -130.42 -133.03 L -86.5 -219 Z M -93.69 -216.02 L -1.85 -186.29 L 1.85 -197.71 L -90 -227.44 Z M -91.64 -227.73 L -262.05 -233.49 L -262.46 -221.49 L -92.05 -215.73 Z M -86.3 -224.03 L -130.69 -331.2 L -141.78 -326.61 L -97.39 -219.43 Z M -87.46 -217.63 L 28.97 -342.2 L 20.2 -350.4 L -96.23 -225.83 Z M 6 -192 A 6 6 0 1 0 -6 -192 A 6 6 0 1 0 6 -192 Z M 141.76 -135.76 A 6 6 0 1 0 129.76 -135.76 A 6 6 0 1 0 141.76 -135.76 Z M 198 0 A 6 6 0 1 0 186 0 A 6 6 0 1 0 198 0 Z M 141.76 135.76 A 6 6 0 1 0 129.76 135.76 A 6 6 0 1 0 141.76 135.76 Z M 6 192 A 6 6 0 1 0 -6 192 A 6 6 0 1 0 6 192 Z M -129.76 135.76 A 6 6 0 1 0 -141.76 135.76 A 6 6 0 1 0 -129.76 135.76 Z M -186 0 A 6 6 0 1 0 -198 0 A 6 6 0 1 0 -186 0 Z M -129.76 -135.76 A 6 6 0 1 0 -141.76 -135.76 A 6 6 0 1 0 -129.76 -135.76 Z M 97.84 -221.73 A 6 6 0 1 0 85.84 -221.73 A 6 6 0 1 0 97.84 -221.73 Z M -18.58 -346.3 A 6 6 0 1 0 -30.58 -346.3 A 6 6 0 1 0 -18.58 -346.3 Z M 142.24 -328.9 A 6 6 0 1 0 130.24 -328.9 A 6 6 0 1 0 142.24 -328.9 Z M 268.25 -227.49 A 6 6 0 1 0 256.25 -227.49 A 6 6 0 1 0 268.25 -227.49 Z M 227.73 -91.84 A 6 6 0 1 0 215.73 -91.84 A 6 6 0 1 0 227.73 -91.84 Z M 233.49 -262.25 A 6 6 0 1 0 221.49 -262.25 A 6 6 0 1 0 233.49 -262.25 Z M 334.9 -136.24 A 6 6 0 1 0 322.9 -136.24 A 6 6 0 1 0 334.9 -136.24 Z M 352.3 24.58 A 6 6 0 1 0 340.3 24.58 A 6 6 0 1 0 352.3 24.58 Z M 227.73 91.84 A 6 6 0 1 0 215.73 91.84 A 6 6 0 1 0 227.73 91.84 Z M 352.3 -24.58 A 6 6 0 1 0 340.3 -24.58 A 6 6 0 1 0 352.3 -24.58 Z M 334.9 136.24 A 6 6 0 1 0 322.9 136.24 A 6 6 0 1 0 334.9 136.24 Z M 233.49 262.25 A 6 6 0 1 0 221.49 262.25 A 6 6 0 1 0 233.49 262.25 Z M 97.84 221.73 A 6 6 0 1 0 85.84 221.73 A 6 6 0 1 0 97.84 221.73 Z M 268.25 227.49 A 6 6 0 1 0 256.25 227.49 A 6 6 0 1 0 268.25 227.49 Z M 142.24 328.9 A 6 6 0 1 0 130.24 328.9 A 6 6 0 1 0 142.24 328.9 Z M -18.58 346.3 A 6 6 0 1 0 -30.58 346.3 A 6 6 0 1 0 -18.58 346.3 Z M -85.84 221.73 A 6 6 0 1 0 -97.84 221.73 A 6 6 0 1 0 -85.84 221.73 Z M 30.58 346.3 A 6 6 0 1 0 18.58 346.3 A 6 6 0 1 0 30.58 346.3 Z M -130.24 328.9 A 6 6 0 1 0 -142.24 328.9 A 6 6 0 1 0 -130.24 328.9 Z M -256.25 227.49 A 6 6 0 1 0 -268.25 227.49 A 6 6 0 1 0 -256.25 227.49 Z M -215.73 91.84 A 6 6 0 1 0 -227.73 91.84 A 6 6 0 1 0 -215.73 91.84 Z M -221.49 262.25 A 6 6 0 1 0 -233.49 262.25 A 6 6 0 1 0 -221.49 262.25 Z M -322.9 136.24 A 6 6 0 1 0 -334.9 136.24 A 6 6 0 1 0 -322.9 136.24 Z M -340.3 -24.58 A 6 6 0 1 0 -352.3 -24.58 A 6 6 0 1 0 -340.3 -24.58 Z M -215.73 -91.84 A 6 6 0 1 0 -227.73 -91.84 A 6 6 0 1 0 -215.73 -91.84 Z M -340.3 24.58 A 6 6 0 1 0 -352.3 24.58 A 6 6 0 1 0 -340.3 24.58 Z M -322.9 -136.24 A 6 6 0 1 0 -334.9 -136.24 A 6 6 0 1 0 -322.9 -136.24 Z M -221.49 -262.25 A 6 6 0 1 0 -233.49 -262.25 A 6 6 0 1 0 -221.49 -262.25 Z M -85.84 -221.73 A 6 6 0 1 0 -97.84 -221.73 A 6 6 0 1 0 -85.84 -221.73 Z M -256.25 -227.49 A 6 6 0 1 0 -268.25 -227.49 A 6 6 0 1 0 -256.25 -227.49 Z M -130.24 -328.9 A 6 6 0 1 0 -142.24 -328.9 A 6 6 0 1 0 -130.24 -328.9 Z M 30.58 -346.3 A 6 6 0 1 0 18.58 -346.3 A 6 6 0 1 0 30.58 -346.3 Z"/></mask></defs><rect width="1024" height="1024" rx="184" fill="#000"/><g transform="translate(512 512) scale(1.2)"><circle r="320" fill="#fff" mask="url(#onw-logo-cuts)"/></g></svg>'
_LEFTOVER_SLOT = re.compile(r"\{\{([A-Z][A-Z0-9_]{0,40})\}\}")
_LEFTOVER_MARK = re.compile(r"<!--\s*/?(?:repeat|optional)\b[^>]{0,60}-->")
_APPLIED = "data-onw-design"

STYLESHEET = """
:root {
  --fs-xs: 11px; --fs-sm: 12px; --fs-md: 14px; --fs-lg: 20px; --fs-xl: 28px;
  --sp-1: 4px; --sp-2: 8px; --sp-3: 12px; --sp-4: 16px; --sp-5: 24px; --sp-6: 32px; --sp-7: 48px;
  --paper: #ffffff; --wash: #f5f5f4; --ink: #202124; --ink-2: #656970; --ink-3: #969aa1;
  --rule: #e2e2e2; --track: #ececea; --accent: #2666b8; --high: #96690a;
  --good: #177a5c; --bad: #bc3a34; --mark: #fdf0c4;
  --sans: "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper: #0e1014; --wash: #171a21; --ink: #f3f5f9; --ink-2: #9aa3b2; --ink-3: #646c7c;
    --rule: #1f242d; --track: #232833; --accent: #3b86dd; --high: #e0a032;
    --good: #34b08a; --bad: #e2554f; --mark: #4a3d12;
  }
  .onw-chart .c-ink { fill: #f3f5f9; } .onw-chart .c-muted { fill: #9aa3b2; }
  .onw-chart .c-rule { stroke: #1f242d; }
  .onw-chart .c-good { fill: #34b08a; } .onw-chart .c-over { fill: #e2554f; }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--paper); color: var(--ink); font-family: var(--sans);
  font-size: var(--fs-md); line-height: 1.5; font-variant-numeric: tabular-nums;
  -webkit-font-smoothing: antialiased;
}
.onw { max-width: 44rem; margin: 0 auto; padding: var(--sp-5) var(--sp-4) var(--sp-7); }
/* The reset is ELEMENT-only (specificity 0,0,1) so any component class beats it — scoped as
   `.onw p` it outranked `.onw-summary { margin-top }` and zeroed every paragraph's spacing. */
h1, h2, h3, p { margin: 0; }
.onw table { width: 100%; border-collapse: collapse; }
.onw td { padding: 0; vertical-align: top; }
.onw .r { text-align: right; white-space: nowrap; }
/* Flow: whatever a skill stacks inside a section — paragraph, rows, callout, chart — is spaced
   apart. Declared BEFORE the components so a component's own margin (same specificity) wins.
   Without it a free paragraph sat flush against the rows above and the callout below. */
.onw-section > * + * { margin-top: var(--sp-3); }

/* type roles — the only sizes on a page */
.onw-title { font-size: var(--fs-xl); line-height: 1.15; font-weight: 700; letter-spacing: -0.02em; }
.onw h2, .onw-h2 { font-size: var(--fs-lg); line-height: 1.25; font-weight: 700; letter-spacing: -0.01em; }
.onw h3, .onw-h3 { font-size: var(--fs-md); line-height: 1.35; font-weight: 700; }
.onw-sub, .onw-lede { display: block; color: var(--ink-2); font-size: var(--fs-sm); font-weight: 400; }
.onw-label { display: block; color: var(--ink-2); font-size: var(--fs-xs); font-weight: 400; }
.onw-meta { color: var(--ink-3); font-size: var(--fs-xs); }
/* rich text a skill may put in a prose slot: strong, em, mark, br; in the summary also p and lists */
.onw-strong, .onw strong { font-weight: 600; }
.onw em { font-style: italic; }
.onw mark { background: var(--mark); color: inherit; padding: 0 2px; border-radius: 3px; }
.onw-summary p + p { margin-top: var(--sp-2); }
.onw-summary ul, .onw-summary ol { margin: var(--sp-2) 0 0; padding-left: var(--sp-5); }
.onw-summary li { margin: var(--sp-1) 0; }
.onw-summary li::marker { color: var(--ink-3); }

/* masthead and title block */
.onw-masthead td { padding-bottom: var(--sp-4); font-size: var(--fs-sm); vertical-align: middle; }
.onw-brand { font-weight: 600; color: var(--ink); }
.onw-logo { width: 20px; height: 20px; vertical-align: -5px; margin-right: var(--sp-2); border-radius: 5px; }
.onw-for { color: var(--ink-2); }
.onw-period { color: var(--ink-2); font-size: var(--fs-sm); margin-top: var(--sp-2); }

/* the summary: what the period was about, in a few sentences */
.onw-summary { margin-top: var(--sp-5); padding: var(--sp-4) var(--sp-5); background: var(--wash);
  border-radius: 12px; font-size: var(--fs-md); line-height: 1.6; }

/* key figures: a 2-column table of label / value / one line of context */
.onw-figures { margin-top: var(--sp-5); }
.onw-figures td { width: 50%; padding: 0 0 var(--sp-4); }
.onw-figures td + td { padding-left: var(--sp-4); }
.onw-figures td.wide { width: 100%; }
.onw-value { display: block; font-size: var(--fs-lg); font-weight: 700; letter-spacing: -0.01em;
  line-height: 1.3; margin: 2px 0; white-space: nowrap; }
.onw-total { color: var(--ink-2); font-size: var(--fs-sm); margin-top: var(--sp-1); }

/* sections */
.onw-section { margin-top: var(--sp-6); }
.onw-section .onw-lede { margin: var(--sp-1) 0 var(--sp-3); }
.onw-section h3 { margin: var(--sp-5) 0 var(--sp-1); }

/* list rows: name + one line of context on the left, the amount on the right. No dividers:
   space separates rows. */
.onw-rows td { padding: var(--sp-2) 0; }
.onw-rows td.r { padding-left: var(--sp-3); font-weight: 600; }
.onw-rows tr.onw-bar-row td { padding: 0 0 var(--sp-3); }
.onw-rows tr.onw-has-bar td { padding-bottom: var(--sp-1); }

/* a horizontal bar with an optional comparison mark (screen only) */
.onw-track { position: relative; height: 6px; background: var(--track); border-radius: 3px; }
.onw-fill { height: 6px; background: var(--accent); border-radius: 3px; }
.onw-fill.onw-high { background: var(--high); }
.onw-mark { position: absolute; top: -4px; width: 2px; height: 14px; margin-left: -1px;
  background: var(--ink); border-radius: 1px; }
.onw-key { margin-top: var(--sp-2); color: var(--ink-3); font-size: var(--fs-xs); }
.onw-key i { display: inline-block; width: 2px; height: 10px; margin-right: var(--sp-2);
  background: var(--ink); vertical-align: -1px; }

/* value colours — never the only signal: signs carry it too */
.onw-gain { color: var(--good); }
.onw-loss { color: var(--bad); }

/* a check that came back clean, and a flagged item */
.onw-clear { padding: var(--sp-3) var(--sp-4); background: var(--wash); border-radius: 10px;
  color: var(--good); font-weight: 600; }
.onw-flag { display: block; color: var(--bad); font-size: var(--fs-sm); font-weight: 600; }

/* data notes and footer */
.onw-notes { margin-top: var(--sp-6); }
.onw-notes summary { cursor: pointer; font-weight: 600; }
.onw-notes ul { margin: var(--sp-2) 0 0; padding-left: var(--sp-5); color: var(--ink-2); font-size: var(--fs-sm); }
.onw-notes li { margin: var(--sp-1) 0; }
.onw-footer { margin-top: var(--sp-6); color: var(--ink-3); font-size: var(--fs-xs); }
.onw-footer p { margin: var(--sp-2) 0 0; }
.onw-footer a { color: var(--ink-2); font-weight: 600; text-decoration: none; }

/* charts drawn by the server (artifact_charts) */
.onw-chart { margin: var(--sp-2) 0 var(--sp-1); }
.onw-chart > svg { display: block; width: 100%; height: auto; }
"""


def apply_design(page: str) -> str:
    """Swap the page's ``<!-- onw:design -->`` marker for the stylesheet, and every
    ``<!-- onw:logo -->`` for the app's mark. Only the FIRST marker
    is replaced and a page already carrying the stylesheet is left alone, so applying twice is a
    no-op. A page without the marker is returned unchanged — the design system is opt-in."""
    if _APPLIED in page:
        return page
    at = page.find(MARKER)
    if at < 0:
        return page
    style = f"<style {_APPLIED}>{STYLESHEET}</style>"
    page = page[:at] + style + page[at + len(MARKER) :]
    return page.replace(LOGO_MARKER, LOGO_SVG)


def check_filled(page: str) -> None:
    """Refuse a design-system page (one carrying the marker) that still holds a template slot or
    a repeat/optional marker, naming them — the skill fixes the page and saves again, instead of
    remembering a page of copy-the-template rules. Other pages are never checked: ``{{X}}`` may be
    legitimate text there."""
    if MARKER not in page:
        return
    slots = sorted(set(_LEFTOVER_SLOT.findall(page)))
    marks = sorted(set(m.group(0) for m in _LEFTOVER_MARK.finditer(page)))
    if slots or marks:
        parts = []
        if slots:
            parts.append("unfilled slots: " + ", ".join(slots[:20]))
        if marks:
            parts.append("template markers left in: " + ", ".join(marks[:10]))
        raise ValidationError(
            "the report page is not finished — "
            + "; ".join(parts)
            + ". Fill or remove them (a repeat/optional block keeps its content, not its "
            "markers) and save again."
        )
