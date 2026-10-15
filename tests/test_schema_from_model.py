"""The enforcement gate for model-derived tool schemas (``ai/tools/schema_from_model``).

``tests/golden/tool_input_schemas.json`` pins the ``input_schema`` every tool emitted
BEFORE any derivation work started. That file is what makes migrating a tool mechanical
rather than risky:

    1. write the model, moving each description across VERBATIM
    2. swap ``input_schema={...}`` for ``schema_of(Model, tool_name="…")``
    3. run this test — a mismatch is a red diff, never a silent behaviour change
    4. move on

``test_derived_matches_golden`` covers every migrated tool automatically (the registry in
``schema_from_model`` auto-collects, so there is no list to maintain), and
``test_unmigrated_tools_still_match_golden`` covers the rest — so the golden file also
catches an unrelated lane silently changing a literal schema.

DELIBERATE: the golden file is NOT auto-regenerated. Changing a tool's schema on purpose
means editing it by hand, which is exactly the review moment this file exists to create.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from pydantic import Field

import backend.ai.tools  # noqa: F401 — import for the @tool side effect (registry fill)
from backend.ai.tools.registry import build_tool_schemas
from backend.ai.tools.schema_from_model import (
    NestedInput,
    ToolInput,
    bare_object_schema,
    derived_schemas,
    explicit_null,
    schema_of,
    schema_of_no_required,
)

GOLDEN_PATH = Path(__file__).parent / "golden" / "tool_input_schemas.json"


def _golden() -> Dict[str, Any]:
    return json.loads(GOLDEN_PATH.read_text())


def _live() -> Dict[str, Any]:
    return {s["name"]: s["input_schema"] for s in build_tool_schemas()}


def _canonical(schema: Any) -> str:
    """JSON-object key order is not semantic; a schema's identity is its content.

    ``required``/``enum`` are LISTS and stay order-sensitive on purpose — reordering an
    enum changes what the model sees first, and a derived ``required`` follows field
    declaration order, so a mismatch there is a real signal that the model's fields are
    not in the schema's order.
    """
    return json.dumps(schema, sort_keys=True, ensure_ascii=False, indent=2)


def assert_derived_matches_literal(tool_name: str, model: Optional[type] = None) -> None:
    """Assert ``tool_name``'s CURRENT schema equals its pinned pre-migration schema.

    The public migration helper. Pass ``model`` to check a candidate model BEFORE wiring
    it into the ``@tool`` decorator — the recommended way to convert a tool, since it
    reports the exact diff without touching the registry:

        assert_derived_matches_literal("get_report", GetReportInput)
    """
    golden = _golden()
    assert tool_name in golden, (
        f"{tool_name} is not in the golden file. A NEW tool has no literal to match — "
        f"add it to {GOLDEN_PATH.name} deliberately, in its own commit."
    )
    derived = schema_of(model) if model is not None else _live()[tool_name]
    expected, actual = _canonical(golden[tool_name]), _canonical(derived)
    assert actual == expected, (
        f"derived schema for '{tool_name}' does not match its pinned literal.\n"
        f"--- pinned (golden) ---\n{expected}\n"
        f"--- derived (now) ---\n{actual}\n"
        "If the difference is intentional, edit the golden file in the same commit and "
        "say why. If it is not, the model does not describe the tool."
    )


# --- the gate -------------------------------------------------------------------------


def test_golden_file_covers_every_registered_tool():
    missing = sorted(set(_live()) - set(_golden()))
    assert not missing, (
        f"{len(missing)} tool(s) registered with no pinned schema: {missing}. "
        "A new tool must be added to the golden file deliberately."
    )


@pytest.mark.parametrize("tool_name", sorted(derived_schemas()))
def test_derived_matches_golden(tool_name):
    """Every MIGRATED tool still emits byte-for-byte what it emitted as a literal."""
    assert_derived_matches_literal(tool_name)


def test_unmigrated_tools_still_match_golden():
    """The 267 not-yet-derived schemas are pinned too, so drift anywhere is caught."""
    live, golden = _live(), _golden()
    drifted = [
        name
        for name in sorted(set(live) & set(golden))
        if _canonical(live[name]) != _canonical(golden[name])
    ]
    assert not drifted, f"literal schema(s) changed without updating the golden file: {drifted}"


def test_at_least_the_proof_set_is_migrated():
    """Guards against a migrated tool silently reverting to a literal schema."""
    assert set(derived_schemas()) >= {
        "get_equity_report",
        "get_rebalance_plan",
        "remember_fact",
        "forget_fact",
    }


# --- adapter unit tests: one per documented transform ----------------------------------


def test_optional_collapses_to_bare_type():
    """transform 2 — ``Optional[X]`` must not leak Pydantic's ``anyOf[X, null]``."""

    class M(ToolInput):
        note: Optional[str] = Field(default=None, description="d")

    assert schema_of(M)["properties"]["note"] == {"type": "string", "description": "d"}


def test_title_keyword_stripped_but_a_property_named_title_survives():
    """transform 3 — the ``create_artifact`` hazard: ``title`` is a real parameter there.

    A naive ``pop("title")`` would delete a REQUIRED parameter from a live tool.
    """

    class M(ToolInput):
        title: str = Field(description="Short human title")

    out = schema_of(M)
    assert out["properties"]["title"] == {"type": "string", "description": "Short human title"}
    assert out["required"] == ["title"]
    assert "title" not in out  # the keyword Pydantic injects for the model itself


def test_null_default_stripped_but_a_real_default_survives():
    """transform 4 — ``default: null`` is optionality residue; ``limit: int = 20`` is not."""

    class M(ToolInput):
        omitted: Optional[int] = Field(default=None)
        limit: int = Field(default=20, description="Maximum number of runs to return")

    props = schema_of(M)["properties"]
    assert "default" not in props["omitted"]
    assert props["limit"]["default"] == 20


def test_nested_model_is_inlined_not_reffed():
    """transform 1 — no ``$ref``/``$defs`` may survive into an Anthropic schema."""

    class Leg(ToolInput):
        account_id: str = Field(description="Account")

    class M(ToolInput):
        leg: Leg = Field(description="The leg")

    out = schema_of(M)
    assert "$defs" not in out
    assert "$ref" not in json.dumps(out)
    assert out["properties"]["leg"]["properties"]["account_id"]["type"] == "string"
    # the field's own description wins over the nested model's
    assert out["properties"]["leg"]["description"] == "The leg"


def test_explicit_null_is_not_collapsed():
    """The three money-field nullables where ``null`` CLEARS and omission does not.

    ``update_transaction.payee_id`` et al. mean something different for an explicit null
    than for an absent key. Collapsing that away would be a silent semantic change, so it
    must be opted into and must survive the nullable collapse.
    """

    class M(ToolInput):
        payee_id: Optional[str] = Field(
            default=None,
            json_schema_extra=explicit_null("string"),
            description="Payee id, or null to clear",
        )

    assert schema_of(M)["properties"]["payee_id"]["type"] == ["string", "null"]


def test_all_optional_model_still_declares_empty_required():
    class M(ToolInput):
        a: Optional[str] = Field(default=None)

    assert schema_of(M)["required"] == []


def test_extra_forbid_yields_additional_properties_false():
    class M(ToolInput):
        a: Optional[str] = Field(default=None)

    assert schema_of(M)["additionalProperties"] is False


def test_schema_of_no_required_omits_the_key_entirely():
    """The Phase-2 quirk class: 7 literals spell 'nothing required' by omitting the key."""

    class M(ToolInput):
        a: Optional[str] = Field(default=None)

    out = schema_of_no_required(M)
    assert "required" not in out
    assert out["properties"]["a"] == {"type": "string"}


def test_schema_of_no_required_refuses_a_model_with_a_required_field():
    class M(ToolInput):
        a: str = Field()

    with pytest.raises(ValueError, match="required fields"):
        schema_of_no_required(M)


def test_recursive_model_fails_loudly_rather_than_truncating():
    class Node(ToolInput):
        child: Optional["Node"] = Field(default=None)

    Node.model_rebuild()
    with pytest.raises(ValueError, match="recursive"):
        schema_of(Node)


# --- bare_object_schema — the promoted "bare free-form dict" escape hatch --------------


def test_bare_object_schema_strips_a_plain_dict_field():
    class M(ToolInput):
        params: Dict[str, Any] = Field(description="d", json_schema_extra=bare_object_schema)

    assert schema_of(M)["properties"]["params"] == {"type": "object", "description": "d"}


def test_bare_object_schema_strips_the_outer_anyOf_branch_for_an_optional_field():
    """The non-obvious part: Pydantic attaches ``json_schema_extra`` to the OUTER
    ``anyOf[X, null]`` wrapper for an ``Optional`` field, not the inner ``X`` branch — a
    callable that only pops the schema it's handed (rather than walking into ``anyOf``)
    silently no-ops here, since the wrapper itself is never ``type: object``."""

    class M(ToolInput):
        params: Optional[Dict[str, Any]] = Field(
            default=None, description="d", json_schema_extra=bare_object_schema
        )

    assert schema_of(M)["properties"]["params"] == {"type": "object", "description": "d"}


def test_bare_object_schema_strips_array_items():
    """``List[Dict[str, Any]]`` puts the bare object at ``schema["items"]``, one level
    below what the callable is handed directly."""

    class M(ToolInput):
        decisions: List[Dict[str, Any]] = Field(
            default_factory=list, description="d", json_schema_extra=bare_object_schema
        )

    assert schema_of(M)["properties"]["decisions"] == {
        "type": "array",
        "items": {"type": "object"},
        "description": "d",
    }


def test_bare_object_schema_strips_optional_array_items():
    """Both quirks composed: ``Optional[List[Dict[str, Any]]]``."""

    class M(ToolInput):
        decisions: Optional[List[Dict[str, Any]]] = Field(
            default=None, description="d", json_schema_extra=bare_object_schema
        )

    assert schema_of(M)["properties"]["decisions"] == {
        "type": "array",
        "items": {"type": "object"},
        "description": "d",
    }


# --- NestedInput — the promoted "nested object without additionalProperties" base ------


def test_nested_input_emits_no_additional_properties_key():
    """A hand-written nested-object literal routinely declares ``properties`` but omits
    ``additionalProperties`` entirely — the author never wrote one. ``ToolInput``'s
    ``extra="forbid"`` would add ``additionalProperties: false``, a byte mismatch;
    ``NestedInput`` must emit no such keyword at all."""

    class Leg(NestedInput):
        account_id: str = Field(description="Account")

    class M(ToolInput):
        leg: Leg = Field(description="The leg")

    out = schema_of(M)["properties"]["leg"]
    assert "additionalProperties" not in out
    assert out["properties"]["account_id"]["type"] == "string"


def test_nested_input_suppresses_its_own_docstring_too():
    """Transform 5 (docstring → root ``description``) applies to a nested model exactly
    like the root — a nested model's docstring leaks into ``$defs`` and would surface
    wherever the field lacks its own overriding description."""

    class Leg(NestedInput):
        """Schema-of-record for a leg — must not leak as a description anywhere."""

        account_id: str = Field(description="Account")

    class M(ToolInput):
        legs: List[Leg] = Field(default_factory=list)

    out = schema_of(M)["properties"]["legs"]
    assert "description" not in out["items"]


def test_plain_basemodel_docstrings_never_leak_into_the_schema():
    """``drop_docstrings=True`` applies transform 5 to plain ``BaseModel``s, root and nested:
    a ``complete_model`` response model is a plain ``BaseModel``,
    and its schema rides to the provider as ``--json-schema``. The analyst's leaked docstring
    (prose about hidden thinking and a "reasoning channel") tripped Sonnet 5.5's
    ``reasoning_extraction`` safeguard on every analysis call (2026-09-30). Field
    descriptions are model-facing and must survive."""
    from pydantic import BaseModel

    class Issue(BaseModel):
        """Engineer notes about the nested shape."""

        question: str = Field(description="The question")

    class Out(BaseModel):
        """Engineer notes about the root shape."""

        why: str = Field(description="The core development")
        issues: List[Issue] = Field(default_factory=list)

    out = schema_of(Out, drop_docstrings=True)
    assert "description" not in out
    assert "description" not in out["properties"]["issues"]["items"]
    assert out["properties"]["why"]["description"] == "The core development"
    assert out["properties"]["issues"]["items"]["properties"]["question"]["description"] == (
        "The question"
    )


def test_nested_input_used_at_root_would_be_a_bug_not_a_feature():
    """Documents the contract, not a runtime guard: ``NestedInput`` at the ROOT emits no
    ``additionalProperties: false``, unlike every other hand-written root in this repo —
    it must only ever be used for a NESTED field's model."""

    class Root(NestedInput):
        a: str = Field()

    assert "additionalProperties" not in schema_of(Root)


def test_tool_schemas_never_use_top_level_oneof_allof_anyof():
    """Anthropic's tool-use API rejects a top-level ``oneOf``/``allOf``/``anyOf`` on a
    tool's ``input_schema`` outright — HTTP 400 on the very first message. Owner-reproduced
    live, 2026-09-03: ``find_tools`` (a CORE tool loaded in EVERY session) carried a
    top-level ``anyOf`` — verified only against OpenAI's Structured Outputs docs, never
    against Anthropic's — and broke every session on the raw Anthropic API-key transport
    from the first message onward. A NESTED oneOf/allOf/anyOf (inside a property, e.g. an
    Optional field) is fine — only the schema's own top level is the landmine."""
    banned = {"oneOf", "allOf", "anyOf"}
    offenders = [
        s["name"] for s in build_tool_schemas() if banned & set(s.get("input_schema", {}).keys())
    ]
    assert offenders == [], (
        f"tool(s) {offenders} carry a top-level oneOf/allOf/anyOf — Anthropic's tool-use "
        "API rejects this outright. Move the constraint into the runtime handler instead."
    )
