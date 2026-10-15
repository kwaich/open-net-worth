"""Charts drawn by code from untrusted JSON (synthetic data only)."""

from __future__ import annotations

import json

import pytest

from backend.core.errors import ValidationError
from backend.services.artifact_charts import MAX_CHARTS, expand_charts


def _block(spec) -> str:
    return f'<script type="application/x-onw-chart">{json.dumps(spec)}</script>'


LINE = {
    "type": "line",
    "format": "percent",
    "points": [
        {"date": "2026-09-01", "p": 0.0, "b": 0.0},
        {"date": "2026-09-15", "p": 0.012, "b": -0.004},
        {"date": "2026-09-30", "p": -0.032, "b": -0.007},
    ],
    "series": {"p": "Your portfolio", "b": "S&P 500"},
}


def test_a_chart_block_becomes_static_svg_with_its_labels():
    out = expand_charts(f"<p>before</p>{_block(LINE)}<p>after</p>")
    assert "application/x-onw-chart" not in out
    # one print SVG (stamped into the PDF) + the screen form: three SVGs for a line chart
    assert out.count('<svg class="onw-print"') == 1 and out.count("<onw-screen") == 1
    assert out.count("window.__onwCharts = true") == 1  # the interaction layer, once
    assert "Your portfolio" in out and "−3.2%" in out and "−0.7%" in out
    assert "1 Sep" in out and "30 Sep" in out
    assert out.startswith("<p>before</p>") and "<p>after</p>" in out


def test_stack_and_diverging_render_with_values_from_the_data():
    stack = {
        "type": "stack",
        "format": "percent",
        "items": [{"label": "Needs", "value": 10.4, "target": 15}],
    }
    div = {
        "type": "diverging",
        "format": "money",
        "currency": "EUR",
        "items": [
            {"label": "Alpha", "value": 120.5, "note": "+4.0%"},
            {"label": "Beta", "value": -80.0},
        ],
    }
    out = expand_charts(_block(stack) + _block(div))
    assert "10.4%, target 15.0%" in out
    assert "+EUR 120.50" in out and "−EUR 80.00" in out


def test_labels_are_escaped_never_markup():
    spec = dict(LINE, series={"p": '<img src=x onerror="alert(1)">', "b": "B"})
    out = expand_charts(_block(spec))
    assert "<img" not in out and "&lt;img" in out


@pytest.mark.parametrize(
    "spec, message",
    [
        ("not json", "must be JSON"),
        ({"type": "pie"}, "line, stack or diverging"),
        (dict(LINE, points=LINE["points"][:1]), "points"),
        (dict(LINE, points=[{"date": "d", "p": "1", "b": 0}] * 3), "must be a number"),
        (dict(LINE, points=[{"date": "d", "p": float("inf"), "b": 0}] * 3), "must be a number"),
        ({"type": "stack", "items": []}, "items"),
    ],
)
def test_bad_chart_data_is_refused_with_the_reason(spec, message):
    raw = spec if isinstance(spec, str) else json.dumps(spec, allow_nan=True)
    page = f'<script type="application/x-onw-chart">{raw}</script>'
    with pytest.raises(ValidationError, match=message):
        expand_charts(page)


def test_chart_count_and_unterminated_blocks_are_bounded():
    with pytest.raises(ValidationError, match="at most"):
        expand_charts(_block(LINE) * (MAX_CHARTS + 1))
    with pytest.raises(ValidationError, match="closing"):
        expand_charts('<script type="application/x-onw-chart">{"type":"line"')


def test_create_artifact_draws_charts_for_html_and_pdf(tmp_path, monkeypatch, db_isolated):
    import pymupdf

    import backend.db.artifact_store as artifact_store_mod
    import backend.services.artifacts as artifacts_service
    from backend.db.artifact_store import ArtifactStore

    (tmp_path / "artifacts").mkdir()
    monkeypatch.setattr(artifact_store_mod, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(artifact_store_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(artifacts_service, "_store", ArtifactStore())
    artifacts_service._turn_ctx.set(None)
    page = f"<html><body><h1>Report</h1>{_block(LINE)}</body></html>"

    html = artifacts_service.create_artifact(title="r", content=page)
    assert "<svg" in artifacts_service.read_text(html["id"])
    pdf = artifacts_service.create_artifact(
        title="r", content=None, artifact_type="pdf", source_artifact_id=html["id"]
    )
    # The engine embeds the SVG as a graphic (its text isn't extractable), so look at the
    # rendered page: the portfolio line's blue (#2666b8) must be on it.
    doc = pymupdf.open(stream=artifacts_service.read_bytes(pdf["id"]), filetype="pdf")
    pix = doc[0].get_pixmap(dpi=50)
    px = pix.samples
    blue = sum(
        1
        for i in range(0, len(px), pix.n)
        if abs(px[i] - 0x26) < 30 and abs(px[i + 1] - 0x66) < 30 and abs(px[i + 2] - 0xB8) < 30
    )
    assert blue > 20


def test_long_names_are_shortened_at_a_word_with_an_ellipsis():
    from backend.services.artifact_charts import _fit

    assert _fit("Meta Platforms, Inc.", 18) == "Meta Platforms…"
    assert _fit("Gold (spot)", 18) == "Gold (spot)"


def test_every_chart_is_stamped_full_width_and_nothing_after_it_is_lost(monkeypatch):
    """Live 2026-10-01: the engine shrank an inline chart to a third of the page; a fixed-height
    placeholder then made it drop every section after the chart."""
    import pymupdf

    from backend.services import artifact_pdf

    stamped: list[float] = []
    real_stamp = artifact_pdf._stamp_charts

    def recording(doc, charts, placed):
        stamped.extend(rect.width for _page, rect in placed.values())
        return real_stamp(doc, charts, placed)

    monkeypatch.setattr(artifact_pdf, "_stamp_charts", recording)
    stack = {
        "type": "stack",
        "format": "percent",
        "items": [{"label": f"Group {i}", "value": 10.0 + i, "target": 20} for i in range(5)],
    }
    sections = "".join(
        f"<h2>Section {n}</h2>{_block(stack)}<p>{'words ' * 60}</p>" for n in range(6)
    )
    page = expand_charts(f"<html><body><h1>Report</h1>{sections}<h2>The end</h2></body></html>")
    doc = pymupdf.open(stream=artifact_pdf.render_pdf(page, title="t"), filetype="pdf")

    text = " ".join(p.get_text() for p in doc)
    assert all(f"Section {n}" in text for n in range(6)) and "The end" in text
    # Six charts, every one at the same full content width, wherever it fell on a page.
    assert len(stamped) == 6
    assert max(stamped) - min(stamped) < 1 and min(stamped) > 450


def test_an_unsafe_inline_svg_never_reaches_the_pdf_engine():
    from backend.services.artifact_pdf import _lift_charts

    body, charts = _lift_charts(
        '<p>a</p><svg><image href="file:///etc/passwd"/></svg><p>b</p>'
        '<svg viewBox="0 0 10 10"><rect width="5" height="5"/></svg>'
    )
    assert "passwd" not in body and charts == []
    assert '<rect width="5"' in body  # a harmless inline SVG stays


def test_bars_carry_their_exact_figure_for_tap_and_hover():
    div = {
        "type": "diverging",
        "format": "money",
        "currency": "EUR",
        "items": [{"label": 'Tom & "Jerry" <b>', "value": -80.0, "note": "-4.0%"}],
    }
    out = expand_charts(_block(div))
    assert 'data-tip="Tom &amp; &quot;Jerry&quot; &lt;b&gt;: −EUR 80.00 (-4.0%)"' in out
    assert "<title>Tom &amp; &quot;Jerry&quot; &lt;b&gt;: −EUR 80.00 (-4.0%)</title>" in out


def test_a_line_chart_carries_its_points_for_the_crosshair():
    import html
    import re

    out = expand_charts(_block(LINE))
    raw = re.search(r'data-onw-line="([^"]*)"', out).group(1)
    data = json.loads(html.unescape(raw))
    assert data["dates"] == ["1 Sep", "15 Sep", "30 Sep"]
    assert [s["name"] for s in data["series"]] == ["Your portfolio", "S&P 500"]
    assert data["series"][0]["labels"] == ["0.0%", "+1.2%", "−3.2%"]
    assert len(data["series"][0]["ys"]) == 3


def test_the_interaction_layer_is_added_once_and_only_with_charts():
    page = f"<html><body>{_block(LINE)}{_block(LINE)}</body></html>"
    out = expand_charts(page)
    assert out.count("window.__onwCharts = true") == 1
    assert out.index("window.__onwCharts") < out.index("</body>")
    assert expand_charts(expand_charts(page)).count("window.__onwCharts = true") == 1
    assert "__onwCharts" not in expand_charts("<html><body><p>no charts</p></body></html>")


def test_a_line_chart_reads_without_tapping_scale_and_high_low():
    from backend.services.artifact_charts import _ticks

    assert _ticks(-1.2, 3.4) == [-1.0, 0.0, 1.0, 2.0, 3.0]
    assert 0.0 in _ticks(-0.3, 0.4)
    spec = dict(
        LINE,
        points=[
            {"date": "2026-09-01", "p": 0.0, "b": 0.0},
            {"date": "2026-09-03", "p": 0.031, "b": 0.01},
            {"date": "2026-09-28", "p": -0.009, "b": 0.0},
            {"date": "2026-09-30", "p": -0.005, "b": 0.003},
        ],
    )
    out = expand_charts(_block(spec))
    assert "High +3.1%, 3 Sep" in out and "Low −0.9%, 28 Sep" in out
    assert ">+3%<" in out and ">0%<" in out  # the scale is printed, not hidden behind a tap


# ── the screen form (owner, 2026-10-05: a laptop scaled chart text to 20px) ───────


def _screen(out: str) -> str:
    return out[out.index("<onw-screen") : out.index("</onw-screen>")]


def test_bar_charts_have_an_html_screen_form_whose_text_the_page_sets():
    div = {
        "type": "diverging",
        "format": "money",
        "currency": "SGD",
        "items": [
            {"label": "Alpha", "value": 1074.76, "note": "+3.8% this week"},
            {"label": "Beta", "value": -750.64},
        ],
    }
    scr = _screen(expand_charts(_block(div)))
    assert "<svg" not in scr  # plain HTML: nothing for a browser to scale
    assert "+SGD 1,074.76" in scr and "−SGD 750.64" in scr and "+3.8% this week" in scr
    assert 'class="onw-val onw-gain"' in scr and 'class="onw-val onw-loss"' in scr
    assert 'style="width:100.0%"' in scr  # the biggest move fills its half


def test_a_stack_screen_form_is_an_html_bar_and_legend():
    stack = {
        "type": "stack",
        "format": "percent",
        "items": [{"label": "Needs", "value": 60, "target": 50}, {"label": "Wants", "value": 40}],
    }
    scr = _screen(expand_charts(_block(stack)))
    assert "<svg" not in scr
    assert "Needs" in scr and "60.0%, target 50.0%" in scr and "flex:0.6000" in scr


def test_a_line_screen_form_keeps_its_labels_out_of_the_stretched_plot():
    scr = _screen(expand_charts(_block(LINE)))
    plot = scr[
        scr.index('<svg class="onw-plotsvg"') : scr.index("</svg>", scr.index("onw-plotsvg"))
    ]
    assert 'preserveAspectRatio="none"' in plot and "vector-effect" in plot
    assert "<text" not in plot  # tick and end labels sit in fixed-size SVGs beside it
    assert "Your portfolio" in scr and "−3.2%" in scr and "1 Sep" in scr


def test_the_screen_form_is_hostile_label_safe():
    div = {"type": "diverging", "format": "money", "items": [{"label": "<b>x</b>", "value": 1}]}
    scr = _screen(expand_charts(_block(div)))
    assert "<b>x</b>" not in scr and "&lt;b&gt;x&lt;/b&gt;" in scr


def test_the_pdf_drops_the_screen_form_and_stamps_the_print_svg():
    from backend.services.artifact_pdf import _lift_charts, _pdf_parts

    page = f"<html><body><h1>R</h1>{expand_charts(_block(LINE))}</body></html>"
    body, _css = _pdf_parts(page)
    assert "onw-screen" not in body
    body, charts = _lift_charts(body)
    assert len(charts) == 1 and 'class="onw-print"' in charts[0]


def test_a_stack_given_fractions_prints_percents_not_fractions():
    """Live 2026-10-06: shares passed as fractions printed "0.5%, target 0.5%"."""
    frac = {
        "type": "stack",
        "format": "percent",
        "items": [
            {"label": "Needs", "value": 0.464, "target": 0.5},
            {"label": "Wants", "value": 0.415, "target": 0.3},
            {"label": "Savings", "value": 0.121, "target": 0.2},
        ],
    }
    out = expand_charts(_block(frac))
    assert "46.4%, target 50.0%" in out and "12.1%, target 20.0%" in out
    pct = dict(
        frac,
        items=[dict(i, value=i["value"] * 100, target=i["target"] * 100) for i in frac["items"]],
    )
    assert "46.4%, target 50.0%" in expand_charts(_block(pct))  # percents stay as they are
