"""Intel alerts never show a typed-out "\\n" (synthetic data only).

Live 2026-09-29 →: with Claude in the CLI's schema-validated mode, 23 of 33 Telegram alerts
showed a literal "\\n\\n" — the model copied the prompt example's escaped paragraph break.
"""

from __future__ import annotations

import pytest

from backend.monitoring import analyst

_EVIDENCE = [
    {"title": "Oil rises", "source": "Example Wire", "published": "2026-10-01", "snippet": "Up."}
]


def test_a_typed_out_line_break_becomes_a_real_one():
    parsed = {"why": "x", "message": "Oil rose $3.\\n\\nWatch Brent above $100."}
    out = analyst._format_analysis("Iran–US War / Conflict", "", parsed, sources=["[Wire](u)"])
    assert "\\n" not in out
    assert "Oil rose $3.\n\nWatch Brent above $100." in out
    assert analyst._real_line_breaks("a\\r\\nb") == "a\nb"


@pytest.mark.parametrize(
    "row",
    [
        {
            "id": "t",
            "subject_type": "topic",
            "subject": "A theme",
            "label": "A theme",
            "trigger": "news_spike",
            "ticker": "",
        },
        {
            "id": "a",
            "subject_type": "asset",
            "subject": "ACME",
            "label": "Acme",
            "trigger": "crash",
            "ticker": "ACME",
        },
    ],
)
def test_the_prompt_never_shows_an_escaped_line_break_to_copy(row):
    user, system = analyst._build_prompts(row, _EVIDENCE, profile={})
    assert "\\n" not in user and "\\n" not in system
