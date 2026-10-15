"""Plan-state service: validation + write choke point for Outlook scenarios and the FIRE baseline.

The simulation itself is ``services.montecarlo`` (resolved by ``services.retirement_plan``).
This service only persists the user's lever set, income streams, life events, and shock
configuration across sessions.

Validation philosophy: only the fields that matter for server-side safety are checked
(name non-empty, numeric levers in sane range, list lengths bounded against abuse).
Unrecognised camelCase fields are passed through untouched.
"""

from __future__ import annotations

import math

from backend.core.errors import NotFoundError, ValidationError
from backend.db.plan_state_store import PlanStateStore

_store = PlanStateStore()

_MAX_STREAMS = 50
_MAX_EVENTS = 50


def get_scenarios() -> list[dict]:
    return _store.list_scenarios()


# The simulation's item contracts. Top-level scenario fields stay pass-through (the UI
# owns that shape), but stream/event ITEMS feed the simulation directly — an off-contract key
# (e.g. snake_case ``end_age``) is silently invisible to the sim, so it is REFUSED with a
# corrective message instead of coerced (the create_transaction convention: the caller —
# usually the AI — fixes its output and retries; nothing is silently rewritten).
_STREAM_ITEM_KEYS = frozenset({"label", "amount", "age", "endAge"})
_EVENT_ITEM_KEYS = frozenset({"id", "label", "amount", "age"})


def _snake_to_camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(part.title() for part in rest)


def _validate_age(kind: str, item: dict, field: str, *, required: bool) -> None:
    """``age``/``endAge`` are a discrete year a person picks — never a fraction of one (a
    stream/event default that inherits the sim's precise, DOB-derived currentAge used to leak
    fractions like 33.3 into here). Mirrors the existing lifeExp/retireAge lever check below:
    reject rather than silently truncate, so a caller that still sends a float finds out."""
    v = item.get(field)
    if v is None:
        if required:
            raise ValidationError(f"{kind[:-1]} '{field}' is required")
        return
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValidationError(f"{kind[:-1]} '{field}' must be an integer, got {v!r}")
    if float(v) != int(v):
        raise ValidationError(f"{kind[:-1]} '{field}' must be a whole number of years, got {v}")
    if not (0 <= int(v) <= 150):
        raise ValidationError(f"{kind[:-1]} '{field}' must be between 0 and 150, got {v}")


def _validate_item_fields(data: dict) -> None:
    for kind, allowed in (("streams", _STREAM_ITEM_KEYS), ("events", _EVENT_ITEM_KEYS)):
        for item in data.get(kind) or []:
            if not isinstance(item, dict):
                raise ValidationError(f"each {kind} item must be an object")
            for key in item:
                if key in allowed:
                    continue
                suggestion = _snake_to_camel(key)
                hint = f" — use '{suggestion}'" if suggestion in allowed else ""
                raise ValidationError(
                    f"unknown {kind[:-1]} field '{key}'{hint} "
                    f"(allowed: {', '.join(sorted(allowed))})"
                )
            _validate_age(kind, item, "age", required=True)
            if kind == "streams":
                _validate_age(kind, item, "endAge", required=False)


def add_scenario(data: dict) -> dict:
    """Validate + persist a new scenario. Returns the stored object (with server-assigned id)."""
    _validate_item_fields(data)
    _validate_scenario(data)
    return _store.add_scenario(data)


def update_scenario(scenario_id: str, data: dict) -> dict:
    """Validate + replace scenario ``scenario_id``. Raises NotFoundError if missing."""
    _validate_item_fields(data)
    _validate_scenario(data)
    result = _store.update_scenario(scenario_id, data)
    if result is None:
        raise NotFoundError(f"Scenario '{scenario_id}' not found", scenario_id=scenario_id)
    return result


def delete_scenario(scenario_id: str) -> None:
    """Remove a scenario. Raises NotFoundError if missing."""
    if not _store.delete_scenario(scenario_id):
        raise NotFoundError(f"Scenario '{scenario_id}' not found", scenario_id=scenario_id)


def get_baseline() -> dict | None:
    return _store.get_baseline()


def set_baseline(obj: dict | None) -> dict | None:
    """Store or clear the FIRE baseline override."""
    if obj is not None:
        _validate_item_fields(obj)
        _validate_levers(obj)
        _validate_list_bounds(obj)
    return _store.set_baseline(obj)


def release_stale_baseline_levers(profile_values: dict[str, float | None]) -> None:
    """A profile edit is newer than the baseline's copy of the same setting: drop each baseline
    lever whose NEW profile value differs, so the profile shows through instead of the baseline
    silently shadowing what the user just said. A lever that already agrees is left alone (the
    Outlook's own "Update my FIRE plan" writes both with the same value and must not look like
    an outside change)."""
    baseline = _store.get_baseline()
    if not baseline:
        return
    kept = {
        lever: value
        for lever, value in baseline.items()
        if lever not in profile_values
        or value is None
        or (profile_values[lever] is not None and float(value) == float(profile_values[lever]))
    }
    if kept != baseline:
        _store.set_baseline(kept or None)


# ── Validation ────────────────────────────────────────────────────────────────


def _validate_scenario(data: dict) -> None:
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("Scenario name must not be empty")
    _validate_levers(data)
    _validate_list_bounds(data)


def _validate_levers(data: dict) -> None:
    """Sanity-check stable numeric lever fields. Missing/None values are skipped."""
    if "spend" in data:
        raise ValidationError(
            "'spend' is retired — use 'monthlyRetirementSpending' in the base currency"
        )
    for field in ("ret", "vol", "swr"):
        v = data.get(field)
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise ValidationError(f"'{field}' must be a number")
        if not (0.0 <= fv <= 100.0):
            raise ValidationError(f"'{field}' must be between 0 and 100, got {fv}")

    # A saving may be negative (spending more than they earn is a real fact); retirement
    # spending may not.
    for field, floor in (("saving", None), ("monthlyRetirementSpending", 0)):
        v = data.get(field)
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise ValidationError(f"'{field}' must be a number")
        if not math.isfinite(fv) or (floor is not None and fv < floor):
            raise ValidationError(f"'{field}' must be non-negative, got {fv}")

    for field in ("lifeExp", "retireAge"):
        v = data.get(field)
        if v is None:
            continue
        try:
            iv = int(v)
        except (TypeError, ValueError):
            raise ValidationError(f"'{field}' must be an integer")
        if not (1 <= iv <= 150):
            raise ValidationError(f"'{field}' must be between 1 and 150, got {iv}")


def _validate_list_bounds(data: dict) -> None:
    if len(data.get("streams") or []) > _MAX_STREAMS:
        raise ValidationError(f"Too many income streams (max {_MAX_STREAMS})")
    if len(data.get("events") or []) > _MAX_EVENTS:
        raise ValidationError(f"Too many life events (max {_MAX_EVENTS})")
