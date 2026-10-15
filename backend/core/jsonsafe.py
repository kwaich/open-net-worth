"""json_safe — strip non-finite floats from a payload before it hits the JSON encoder.

JSON has no representation for NaN / Infinity, and Starlette renders responses with
``json.dumps(content, allow_nan=False)`` — so a single ``nan`` anywhere in a response dict
500s the whole endpoint. Non-finite values sneak in from flaky upstreams (yfinance
``fast_info`` returns NaN for sparse tickers) and persist in stored briefs, so guarding at
each arithmetic site is whack-a-mole. This is the serialization-boundary backstop: a finance
API should degrade a bad number to ``null`` (unknown), never crash the page.

Pure stdlib — lives in the ``core`` leaf so any layer can call it.
"""

from __future__ import annotations

import math
from typing import Any


def json_safe(obj: Any) -> Any:
    """Recursively replace non-finite floats (NaN, ±Infinity) with None.

    Returns a new structure for dicts/lists/tuples; scalars are returned as-is unless they
    are a non-finite float. ``bool`` is left untouched (it is a JSON-valid scalar)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj


_NULL_STRINGS = frozenset({"null", "none", "n/a", "na", "undefined"})


def none_if_null_string(value: Any) -> Any:
    """Coerce a string scalar that IS (exactly, case-insensitively) 'null' / 'none' / 'n/a' /
    'undefined' to a real ``None``.

    A structured-output LLM asked for a nullable field occasionally emits the LITERAL STRING
    for "no value" instead of JSON's ``null`` token — it still validates (``Optional[str]``
    accepts any string), so it silently persists and renders as the word "null" in the UI
    (owner report 2026-08-11: AssetDetailView's "Lucius's take on your position" card showing
    literal "null"). Applied at the LLM-result boundary before persisting, and again at the
    read boundary so data already persisted before this existed self-heals on next read —
    same posture as ``json_safe``'s non-finite-float backstop, scoped to one scalar at a
    time (the caller applies it to the specific nullable fields at risk, not a blanket
    recursive sweep — most persisted strings, e.g. article text, are never LLM-nullable)."""
    if isinstance(value, str) and value.strip().lower() in _NULL_STRINGS:
        return None
    return value
