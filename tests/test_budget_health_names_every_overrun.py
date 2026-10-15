"""The budget view's health reasons are the COMPLETE list of what is over budget.

Persona QA round 7: asked what was over budget, Lucius named Entertainment and missed Rent, which
was S$600 over its monthly cap (the Budget screen and the briefing both showed it). Health used to
carry only groups past their target, so the full overrun list existed nowhere for the model to read
and it had to scan rows. Every envelope past its cap, and every overspent Zero-Based envelope, is
now a named reason.
"""

from __future__ import annotations

from backend.services.budget_engines import proportional_view as view_
from backend.services.budget_engines import zero_based


def _env(label: str, activity: float, cap: float | None, excluded: bool = False) -> dict:
    return {"label": label, "activity": activity, "cap": cap, "excluded": excluded}


GROUPS = [{"name": "Needs", "activity": 500.0, "target": None}]


def test_every_envelope_past_its_cap_is_a_reason_biggest_first():
    over = view_.over_cap_envelopes(
        [
            _env("Entertainment", 150.0, 100.0),
            _env("Rent", 2600.0, 2000.0),
            _env("Groceries", 300.0, 400.0),
            _env("Dining", 90.0, None),
        ]
    )

    assert [e["label"] for e in over] == ["Rent", "Entertainment"]
    reasons = view_.health(100.0, 3000.0, GROUPS, over).reasons
    assert reasons == [
        "Rent is over its cap by 600.00 (spent 2600.00 of 2000.00).",
        "Entertainment is over its cap by 50.00 (spent 150.00 of 100.00).",
    ]


def test_an_over_cap_envelope_turns_a_good_month_into_attention():
    assert view_.health(100.0, 3000.0, GROUPS, []).verdict == "good"
    over = view_.over_cap_envelopes([_env("Rent", 2600.0, 2000.0)])
    assert view_.health(100.0, 3000.0, GROUPS, over).verdict == "attention"


def test_an_excluded_envelope_over_its_cap_is_still_named():
    over = view_.over_cap_envelopes([_env("Rent", 2600.0, 2000.0, excluded=True)])
    assert [e["label"] for e in over] == ["Rent"]


def test_overruns_are_named_even_when_spending_exceeds_income():
    over = view_.over_cap_envelopes([_env("Rent", 2600.0, 2000.0)])
    health = view_.health(-50.0, 3000.0, GROUPS, over)
    assert health.verdict == "bad"
    assert any("Rent is over its cap by 600.00" in r for r in health.reasons)


def test_zero_based_names_each_overspent_envelope():
    table = {
        "groups": [
            {
                "categories": [
                    {"label": "Entertainment", "available": -50.0},
                    {"label": "Rent", "available": -600.0},
                    {"label": "Groceries", "available": 40.0},
                ]
            }
        ]
    }
    summary = {"overspent_count": 2, "underfunded_count": 0}

    reasons = zero_based._health_reasons(summary, zero_based._overspent(table))

    assert reasons == [
        "2 envelope(s) overspent",
        "Rent is overspent by 600.00.",
        "Entertainment is overspent by 50.00.",
    ]
