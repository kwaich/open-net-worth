"""A report page is refused when it says too much to read on a phone (synthetic pages only).

Live 2026-10-07: a free-form monthly report reached 1,266 words and 171 numbers with no bars.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.services.artifact_density import check_density


def _page(summary="Spending ran <strong>12%</strong> above usual.", body=""):
    return (
        '<main class="onw onw-report"><div class="onw-summary">'
        + summary
        + "</div>"
        + body
        + "</main>"
    )


def _rows(n, sub="usual SGD 120.00"):
    cells = "".join(
        f'<tr><td>Row {i}<span class="onw-sub">{sub}</span></td><td class="r">SGD 1.00</td></tr>'
        for i in range(n)
    )
    return f'<table class="onw-rows">{cells}</table>'


def test_a_tight_report_passes():
    check_density(_page(body=_rows(6) + "<p>Bring dining back to its usual level.</p>"))


def test_a_page_that_is_not_a_report_is_never_checked():
    check_density('<main class="onw"><p>' + "word " * 500 + "</p></main>")


def test_a_long_summary_is_refused_with_its_length():
    with pytest.raises(ValidationError, match="summary is 90 words"):
        check_density(_page(summary="word " * 90))


def test_a_long_context_line_is_refused_naming_it():
    sub = (
        "Usual SGD 151.04; increase SGD 2,502.35 because of several large group dinners this month"
    )
    with pytest.raises(ValidationError, match="context line"):
        check_density(_page(body=_rows(2, sub=sub)))


def test_a_long_list_is_refused_but_bar_rows_do_not_count():
    bars = '<tr class="onw-bar-row"><td colspan="2"></td></tr>' * 6
    check_density(_page(body=_rows(6).replace("</table>", bars + "</table>")))
    with pytest.raises(ValidationError, match="list of 9 rows"):
        check_density(_page(body=_rows(9)))


def test_a_run_of_paragraphs_is_refused():
    with pytest.raises(ValidationError, match="3 plain paragraphs"):
        check_density(_page(body="<p>One.</p><p>Two.</p><p>Three.</p>"))


def test_paragraphs_inside_the_summary_are_the_summary_s_own():
    check_density(_page(summary="<p>One.</p><p>Two.</p><p>Three.</p>"))


def _notes(items):
    lis = "".join(f"<li>{t}</li>" for t in items)
    return f'<details class="onw-notes"><summary>Data notes</summary><ul>{lis}</ul></details>'


def test_more_than_four_data_notes_are_refused():
    check_density(_page(body=_notes(["One caveat."] * 4)))
    with pytest.raises(ValidationError, match="8 data notes"):
        check_density(_page(body=_notes(["All figures are SGD."] * 8)))


def test_a_long_data_note_is_refused():
    with pytest.raises(ValidationError, match="data note"):
        check_density(_page(body=_notes(["word " * 40])))


def test_a_list_marked_complete_is_exempt_from_the_row_limit():
    """Every bill and subscription of a month is listed in full (owner, 2026-10-07)."""
    complete = _rows(19).replace(
        '<table class="onw-rows">', '<table class="onw-rows" data-complete>'
    )
    check_density(_page(body=complete))


def test_dates_are_not_counted_as_numbers():
    """Live 2026-10-07: refused "78.2% below SGD 2,445.25 in 31 Aug–6 Sep" — two figures."""
    for line in (
        "78.2% below SGD 2,445.25 in 31 Aug–6 Sep",
        "24.0% below SGD 542.10 in 1–7 Sep",
        "SGD 3,120.10 since 31 Aug, up 2.4%",
    ):
        check_density(_page(body=_rows(2, sub=line)))
    with pytest.raises(ValidationError, match="context line"):
        check_density(_page(body=_rows(2, sub="SGD 1.00, SGD 2.00, SGD 3.00 and SGD 4.00")))
