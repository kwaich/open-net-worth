"""Regression: json_safe strips non-finite floats so a payload can never 500 the JSON encoder.

The asset-detail endpoint 500'd (ValueError: Out of range float values are not JSON
compliant) when a stored brief carried a NaN peer change_pct (a peer ticker that doesn't
trade, e.g. private Axiom Space). Starlette renders with json.dumps(allow_nan=False), so the
serialization-boundary backstop must degrade any NaN/Inf to null."""

import json

from backend.core.jsonsafe import json_safe, none_if_null_string

NAN = float("nan")
INF = float("inf")
NEG_INF = float("-inf")


def test_replaces_non_finite_scalars():
    assert json_safe(NAN) is None
    assert json_safe(INF) is None
    assert json_safe(NEG_INF) is None


def test_keeps_finite_and_other_scalars():
    assert json_safe(192.5) == 192.5
    assert json_safe(0.0) == 0.0
    assert json_safe(-3) == -3
    assert json_safe("AXISZZX") == "AXISZZX"
    assert json_safe(None) is None
    assert json_safe(True) is True  # bool is a JSON-valid scalar, untouched


def test_recurses_into_nested_dicts_and_lists():
    payload = {
        "price": {"last": 192.5, "change_pct": NAN},
        "peers": [
            {"ticker": "BA", "change_pct": 4.58},
            {"ticker": "AXISZZX", "change_pct": NAN},
        ],
        "history": [1.0, INF, 3.0],
    }
    clean = json_safe(payload)
    assert clean["price"]["change_pct"] is None
    assert clean["peers"][1]["change_pct"] is None
    assert clean["history"] == [1.0, None, 3.0]
    # The whole point: the result is encodable under allow_nan=False.
    json.dumps(clean, allow_nan=False)


def test_the_exact_spacex_payload_shape_serializes():
    # Mirrors the field that broke the live endpoint.
    brief_peers = [
        {"name": "Boeing Company", "ticker": "BA", "change_pct": 4.58},
        {"name": "Axiom Space", "ticker": "AXISZZX", "change_pct": NAN},
    ]
    safe = json_safe({"peers": brief_peers})
    assert safe["peers"][1]["change_pct"] is None
    json.dumps(safe, allow_nan=False)  # must not raise


def test_does_not_mutate_the_input():
    src = {"a": [NAN]}
    json_safe(src)
    # original untouched (json_safe returns a new structure)
    assert src["a"][0] != src["a"][0]  # still NaN (NaN != NaN)


# ── none_if_null_string: a structured-output LLM's literal "null" string coerced to real None ──
# Regression: AssetDetailView's "Lucius's take on your position" card rendered the literal
# text "null" — the model emitted the STRING "null" for a nullable field instead of JSON's
# null token, which validates fine against Optional[str] and silently persists as data.


def test_none_if_null_string_coerces_literal_null_variants():
    for s in ("null", "Null", "NULL", "  null  ", "none", "n/a", "na", "undefined"):
        assert none_if_null_string(s) is None


def test_none_if_null_string_keeps_real_content():
    assert none_if_null_string("The company beat earnings.") == "The company beat earnings."
    assert (
        none_if_null_string("") == ""
    )  # empty string is a separate, pre-existing `or None` concern
    assert none_if_null_string(None) is None
    assert none_if_null_string(42) == 42  # non-string scalars pass through untouched
