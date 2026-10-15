"""The artifact design system holds its rules: five type sizes, no requests, opt-in by marker.

The weekly report drifted to ~14 font sizes across its template, the chart drawer and patches
(owner, 2026-10-05: "our font size is pretty unbalanced"). These tests fail on a sixth size.
"""

from __future__ import annotations

import re

import pytest

from backend.core.errors import ValidationError
from backend.db.skill_store import STARTER_SKILLS
from backend.services.artifact_charts_screen import SCREEN_CSS
from backend.services.artifact_design import (
    LOGO_MARKER,
    LOGO_SVG,
    MARKER,
    STYLESHEET,
    apply_design,
    check_filled,
)

SCALE = {"11px", "12px", "14px", "20px", "28px"}
TOKENS = {"var(--fs-xs)", "var(--fs-sm)", "var(--fs-md)", "var(--fs-lg)", "var(--fs-xl)"}
_FONT_SIZE = re.compile(r"font-size\s*:\s*([^;}]+)")


def _sizes(css: str) -> set[str]:
    return {m.strip() for m in _FONT_SIZE.findall(css)}


def test_the_stylesheet_uses_only_the_type_scale():
    sizes = _sizes(STYLESHEET)
    token_defs = set(re.findall(r"--fs-\w+:\s*(\d+px)", STYLESHEET))
    assert token_defs == SCALE
    assert sizes <= TOKENS, sizes - TOKENS


def test_chart_screen_css_uses_only_the_type_scale():
    for size in _sizes(SCREEN_CSS):
        token = re.match(r"var\((--fs-\w+), (\d+px)\)", size)
        assert token and f"var({token.group(1)})" in TOKENS and token.group(2) in SCALE, size


def test_the_line_charts_svg_labels_use_scale_sizes():
    import json

    from backend.services.artifact_charts import expand_charts

    spec = {
        "type": "line",
        "format": "percent",
        "points": [{"date": "2026-09-01", "p": 0.0}, {"date": "2026-09-30", "p": 0.02}],
        "series": {"p": "Your portfolio"},
    }
    out = expand_charts(f'<script type="application/x-onw-chart">{json.dumps(spec)}</script>')
    screen = out[out.index("<onw-screen") : out.index("</onw-screen>")]
    assert {f"{s}px" for s in re.findall(r'font-size="(\d+)"', screen)} <= SCALE


def test_the_stylesheet_fetches_nothing():
    lowered = STYLESHEET.lower()
    for fetch in ("url(", "@import", "@font-face", "http"):
        assert fetch not in lowered


@pytest.mark.parametrize("sid", ("weekly-report", "monthly-report"))
def test_report_templates_carry_markup_only(sid):
    template = next(s for s in STARTER_SKILLS if s["id"] == sid)["files"]["assets/report.html"]
    assert template.count(MARKER) == 1
    assert template.index(MARKER) < template.index("</head>")
    assert "<style" not in template and "font-size" not in template
    # the logo is placed by the server, never copied by the model
    assert "<svg" not in template and template.count(LOGO_MARKER) == 1


def test_apply_design_swaps_the_marker_once_and_is_idempotent():
    page = f"<html><head>{MARKER}</head><body><main class='onw'>x</main></body></html>"
    once = apply_design(page)
    assert MARKER not in once and once.count("<style data-onw-design>") == 1
    assert apply_design(once) == once


def test_a_page_without_the_marker_is_untouched():
    page = "<html><head><style>p{color:red}</style></head><body>x</body></html>"
    assert apply_design(page) == page


def test_an_unfinished_report_page_is_refused_with_what_is_left():
    page = f"<head>{MARKER}</head><p>{{{{SUMMARY}}}}</p><!-- optional: notes --><p>x</p>"
    with pytest.raises(ValidationError) as err:
        check_filled(page)
    assert "SUMMARY" in str(err.value) and "<!-- optional: notes -->" in str(err.value)


def test_a_page_outside_the_design_system_may_hold_braces():
    check_filled("<p>Use {{NAME}} in your template.</p>")  # no marker: not a report page


def test_the_server_places_the_logo():
    page = f"<html><head>{MARKER}</head><body>{LOGO_MARKER} Open Net Worth</body></html>"
    out = apply_design(page)
    assert LOGO_MARKER not in out and out.count(LOGO_SVG) == 1
    assert LOGO_SVG.startswith('<svg class="onw-logo"')
