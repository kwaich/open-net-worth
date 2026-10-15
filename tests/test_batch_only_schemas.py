"""The dual-shape batch tools advertise the ARRAY only — and still accept the bare form.

Two halves, and they pull in opposite directions, which is exactly why both are pinned:

  * the SCHEMA must not advertise a bare copy of the array's item fields (the fork the
    2026-07-19d naming-close lane removed — see ``schema_from_model.schema_of_batch_only``);
  * DISPATCH must still accept that bare copy, because un-advertising is not removing. A
    stored agent playbook, a skill body, an in-flight transcript or a replayed change dict
    may still send the bare shape, and every one of those must keep working byte-for-byte.

Only the second half needs a test — ``backend/scripts/tool_inventory.py --lint`` enforces
the first in CI (``make lint``), and duplicating a lint rule as a test is how the two drift.
"""

from __future__ import annotations

import pytest

from backend.ai.tools import TOOL_SCHEMAS
from backend.ai.tools.registry import _validator_for

_SCHEMAS = {t["name"]: (t.get("input_schema") or {}) for t in TOOL_SCHEMAS}


def _array_item_properties(schema: dict) -> list[tuple[str, dict]]:
    """Every (array param, its item properties) pair a tool declares."""
    out = []
    for name, spec in (schema.get("properties") or {}).items():
        items = spec.get("items") or {}
        if not items.get("properties"):
            for branch in spec.get("anyOf") or []:
                if isinstance(branch, dict) and "array" in str(branch.get("type")):
                    items = branch.get("items") or {}
                    break
        if items.get("properties"):
            out.append((name, items["properties"]))
    return out


def _sample(spec: dict):
    """A minimal in-type value for a property, so the payload exercises TYPE validation."""

    # `type` may be a LIST on an explicit-null field (`["string", "null"]`), which is
    # unhashable — folding it straight into a set is the exact crash registry.py's
    # `coerce_stringified_containers` note records hitting. Normalize before folding.
    def _flatten(node) -> list:
        raw = node.get("type")
        out = list(raw) if isinstance(raw, list) else [raw]
        for b in node.get("anyOf") or []:
            if isinstance(b, dict):
                out += _flatten(b)
        return out

    types = set(_flatten(spec))
    for t, v in (
        ("string", "x"),
        ("number", 1),
        ("integer", 1),
        ("boolean", True),
        ("array", []),
        ("object", {}),
    ):
        if t in types:
            return v
    return "x"


_BATCH_TOOLS = sorted(name for name, s in _SCHEMAS.items() if _array_item_properties(s))


@pytest.mark.parametrize("tool_name", _BATCH_TOOLS)
def test_bare_item_fields_still_pass_dispatch_validation(tool_name):
    """The un-advertised single-item shape must not be rejected before the handler.

    Dispatch validates the EFFECTIVE type only and never enforces ``additionalProperties``
    (``registry.dispatch``'s note), so an undeclared key reaches the handler untouched.
    That property is what makes ``schema_of_batch_only`` a documentation change rather than
    a breaking one — if it ever stops holding, every bare caller breaks silently and this
    test is the thing that says so.
    """
    validator = _validator_for(tool_name)
    if validator is None:
        pytest.skip(f"{tool_name} has no usable schema")

    for array_name, item_props in _array_item_properties(_SCHEMAS[tool_name]):
        bare = {k: _sample(v) for k, v in item_props.items()}
        errors = [e.message for e in validator.iter_errors(bare)]
        assert not errors, (
            f"{tool_name}: the bare single-item shape of '{array_name}' is REJECTED by "
            f"dispatch validation: {errors}. Un-advertising the bare fields was only safe "
            f"because they still validate — this is now a breaking change for every stored "
            f"caller still sending it."
        )


def test_batch_only_helper_refuses_a_model_with_no_bare_mirror():
    """The helper must not be usable as a generic 'drop fields' tool.

    It asserts a specific claim — "this schema duplicates its own array item" — and a
    caller reaching for it where that is untrue is describing the tool wrongly.
    """
    from pydantic import Field

    from backend.ai.tools.schema_from_model import NestedInput, ToolInput, schema_of_batch_only

    class _Item(NestedInput):
        name: str = Field()

    class _NoMirror(ToolInput):
        items: list[_Item] = Field(default_factory=list)

    with pytest.raises(ValueError, match="advertises no bare mirror"):
        schema_of_batch_only(_NoMirror, array="items")

    with pytest.raises(ValueError, match="not a field"):
        schema_of_batch_only(_NoMirror, array="nope")
