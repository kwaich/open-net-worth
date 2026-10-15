"""Derive a tool's Anthropic ``input_schema`` from a typed Pydantic model.

WHY THIS EXISTS
---------------
Every ``@tool`` in this package declares an ``input_schema`` dict by hand, separately
from the handler that reads it. Nothing keeps the two in agreement, and measurement
says they routinely disagree: descriptions that describe a parameter the handler
renamed, handlers reading keys the schema never declares (unreachable under
``additionalProperties: false``), and ``required`` lists naming fields the handler
treats as optional. That divergence is also why ``registry.validation_error`` can only
enforce *effective types* — see the long note there. The schemas do not describe what
the handlers accept, so they cannot be enforced.

The fix is to stop maintaining two artifacts. A model is the single source of truth;
the schema is projected from it mechanically. A parameter the handler does not read
cannot appear in the schema, because the schema IS the model.

WHAT THIS IS NOT
----------------
This is not a rewrite of the 272 existing schemas. It is the mechanism plus a proof,
and it is deliberately additive: a derived tool registers alongside a literal one with
zero registry changes. ``docs/adr/2026-07-19-derive-tool-schemas-from-models.md``
records the per-quirk policy and the migration order.

THE PROJECTION
--------------
``model_json_schema()`` is close to Anthropic's shape but not equal to it. Five
transforms close the gap; each is documented at its implementation below:

  1. ``$defs``/``$ref``   → inlined (Anthropic schemas here are self-contained)
  2. ``anyOf[X, null]``   → ``X``          (Pydantic's spelling of ``Optional[X]``)
  3. ``title``            → dropped        (Pydantic injects one per field; no
                                            hand-written schema in this repo has one)
  4. ``default: null``    → dropped        (the artifact of an optional field; a real
                                            default is kept — 5 tools declare one)
  5. class docstring      → dropped        (Pydantic promotes ``__doc__`` to a root
                                            ``description``; that is engineer-facing
                                            documentation, not model-facing prose)

Transform 5 was NOT predicted by the design sketch — the enforcement assertion caught it
on its first run, which is the mechanism working as intended.

Every walk is STRUCTURE-AWARE: a keyword is only a keyword in keyword position. Three
tools (``create_artifact``, ``schedule_task``, ``update_scheduled_task``) have a
parameter literally named ``title``, and a naive ``pop("title")`` would silently delete
a required parameter from ``create_artifact``. This is the same hazard
``registry._permissive`` guards against, so the two walkers share ONE keyword
classification — imported below rather than re-declared, because a divergence between
them is exactly the class of bug this module exists to prevent.

TWO OPT-IN ESCAPE HATCHES
-------------------------
Two quirk classes cannot be expressed by a plain ``ToolInput`` subclass and need an
explicit opt-in on the field that has the quirk:

  - ``bare_object_schema`` — a nested field whose hand-written literal is a BARE
    ``{"type": "object"}`` (no ``properties``, no ``additionalProperties`` at all). A
    plain ``Dict[str, Any]`` always emits ``additionalProperties: true``; this
    ``json_schema_extra`` callable strips it back out, recursively, wherever the field's
    actual shape puts it (the field itself, an ``Optional`` wrapper's ``anyOf`` branches,
    a ``List[...]``'s ``items``, or both composed). Found by three independent lanes
    duplicating the same ~10-line callable before it was promoted here.
  - ``NestedInput`` — a base for a NESTED model (never the tool's own root) whose
    hand-written literal declares ``properties`` but omits ``additionalProperties``
    entirely, because the author never wrote one. ``ToolInput``'s ``extra="forbid"``
    would add ``additionalProperties: false`` there, a byte mismatch; ``NestedInput``
    is a plain Pydantic model (default ``extra="ignore"``), which is exactly what
    emits no such keyword at all.
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, Optional, Type, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic.json_schema import GenerateJsonSchema

from backend.ai.tools.registry import (
    _SCHEMA_KEYWORDS,
    _SCHEMA_LIST_KEYWORDS,
    _SCHEMA_MAP_KEYWORDS,
)

__all__ = [
    "ToolInput",
    "NestedInput",
    "schema_of",
    "schema_of_no_required",
    "derived_schemas",
    "explicit_null",
    "bare_object_schema",
    "require_nonempty",
]


class _SuppressDocstringDescription:
    """Mixin: suppress transform 5 — the class docstring becoming a schema ``description``.

    Pydantic promotes a model's docstring to a root-level ``description``. That docstring
    is documentation for the next engineer ("Schema-of-record for X"); the model-facing
    prose is the per-FIELD description, and no hand-written schema in this repo carries a
    description at all — root or nested. Emitting one would silently add developer
    commentary to every tool's prompt.

    Suppressed HERE rather than by matching ``cls.__doc__`` in the walk, because this also
    covers a nested model inlined from ``$defs``, where the docstring is not reachable from
    the JSON. A nested-model FIELD's own description is a sibling of the ``$ref`` and is
    re-applied by ``_inline_refs``, so it is unaffected.

    Shared by ``ToolInput`` (root, ``extra="forbid"``) and ``NestedInput`` (nested field,
    no ``extra="forbid"``) so the two bases can never drift on this behavior.
    """

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):  # type: ignore[override]
        schema = handler(core_schema)
        schema.pop("description", None)
        return schema


class ToolInput(_SuppressDocstringDescription, BaseModel):
    """Base for a tool's OWN (root) input model.

    ``extra="forbid"`` is what makes Pydantic emit ``additionalProperties: false``,
    matching the 268 of 272 hand-written roots that set it. A tool whose schema
    deliberately omits it (the propose→apply replay paths dispatch stored change dicts
    carrying extra bookkeeping keys) must NOT use this base as-is — see the ADR.
    """

    model_config = ConfigDict(extra="forbid")


class NestedInput(_SuppressDocstringDescription, BaseModel):
    """Base for a NESTED field's own model — never the tool's root.

    A hand-written nested-object literal in this repo (``import_transactions.mapping``,
    ``schedule_task.transaction``, a batch tool's per-item ``updates[]``/``renames[]``/…)
    routinely declares ``properties`` but has NO ``additionalProperties`` key at all —
    the author never wrote one, and a bare dict there was never a deliberate "reject
    extras" decision the way the tool's own root schema makes. ``ToolInput``'s
    ``extra="forbid"`` would emit ``additionalProperties: false`` on that nested object, a
    byte mismatch against a literal that omits the key. Pydantic's default
    ``model_config`` (``extra="ignore"``) is what a plain ``BaseModel`` already does and is
    exactly this: no ``additionalProperties`` keyword emitted at all (verified directly —
    see ``schema_from_model_test``). Use this as the base for a NESTED model only; the
    tool's own root model must still forbid extras via ``ToolInput``.
    """


def bare_object_schema(schema: Dict[str, Any]) -> None:
    """``json_schema_extra`` callable for a free-form-dict field whose hand-written
    literal is a BARE ``{"type": "object"}`` — no ``properties`` key and no
    ``additionalProperties`` key at all (the "bare free-form dict" quirk class, ADR
    `2026-07-19-derive-tool-schemas-from-models`). A plain ``Dict[str, Any]`` always emits
    ``additionalProperties: true``, a byte mismatch; this strips it back out.

    Pass directly as ``json_schema_extra=bare_object_schema`` (Pydantic calls it with the
    field's own projected schema and applies whatever it mutates in place — this is not a
    factory to invoke, unlike ``explicit_null``)::

        input: Dict[str, Any] = Field(json_schema_extra=bare_object_schema, ...)

    Recurses into every shape the field's type can actually take, because Pydantic hangs
    ``json_schema_extra`` off the OUTERMOST schema for the field, not the bare object
    itself:

    - a plain ``Dict[str, Any]`` field — the schema passed in IS the bare object; handled
      directly.
    - an ``Optional[Dict[str, Any]]`` field — Pydantic attaches the callable to the OUTER
      ``anyOf: [X, null]`` wrapper, not the inner ``X`` branch (the non-obvious part this
      class exists to preserve — a naive top-level-only ``pop`` silently no-ops here,
      since the wrapper itself is never ``type: object``). Every ``anyOf`` branch is
      walked and fixed.
    - a ``List[Dict[str, Any]]`` field (``import_transactions.decisions``,
      ``create_skill.parameters``, …) — the bare object lives at ``schema["items"]``, one
      level down from what the callable receives; walked too.
    - an ``Optional[List[Dict[str, Any]]]`` field — both of the above, composed (an
      ``anyOf`` branch whose own ``items`` is the bare object).
    """
    if schema.get("type") == "object" and "properties" not in schema:
        schema.pop("additionalProperties", None)
    for branch in schema.get("anyOf", []):
        if isinstance(branch, dict):
            bare_object_schema(branch)
    items = schema.get("items")
    if isinstance(items, dict):
        bare_object_schema(items)


def require_nonempty(schema: Dict[str, Any]) -> None:
    """``json_schema_extra`` callable for a ``Union[str, List[str]]`` field where an empty
    value of EITHER shape is meaningless (an id/key field, never a "clear everything"
    signal — unlike e.g. ``set_instrument_constituents.constituents``, which stays plain
    ``List[...]`` precisely because ``[]`` there DOES mean something and must stay callable
    empty).

    Pydantic's own ``Field(min_length=1, ...)`` on a ``Union[str, List[str]]`` lands
    ``minLength`` on the OUTERMOST schema — a sibling of ``anyOf``, not inside either
    branch. That keyword only applies to a string instance; against the array branch it is
    simply inapplicable and silently ignored, so ``[]`` still validates. This callable
    fixes that by pushing the RIGHT keyword into each branch instead: ``minLength: 1`` onto
    the ``type: string`` branch, ``minItems: 1`` onto the ``type: array`` branch — and
    drops the outer ``minLength`` Pydantic left behind, since a sibling-of-``anyOf``
    keyword with no matching ``type`` at that level is dead weight, not a real constraint.

    Pass directly as ``json_schema_extra=require_nonempty`` alongside
    ``Field(min_length=1, ...)`` (the ``min_length=1`` is what makes Pydantic reject an
    empty STRING even before this callable runs; this callable is what makes the ARRAY
    branch enforce the same rule)::

        alert_ids: Union[str, List[str]] = Field(
            min_length=1, json_schema_extra=require_nonempty, description=...
        )
    """
    schema.pop("minLength", None)
    for branch in schema.get("anyOf", []):
        if not isinstance(branch, dict):
            continue
        if branch.get("type") == "string":
            branch["minLength"] = 1
        elif branch.get("type") == "array":
            branch["minItems"] = 1


_M = TypeVar("_M", bound=BaseModel)

# tool name → model, recorded by ``schema_of`` so the enforcement test can iterate every
# migrated tool without a hand-maintained list (the same reason the tool registry
# auto-collects instead of keeping a schema list).
_DERIVED: Dict[str, Type[BaseModel]] = {}


def derived_schemas() -> Dict[str, Type[BaseModel]]:
    """A copy of the tool-name → input-model map for every tool migrated so far."""
    return dict(_DERIVED)


def explicit_null(inner_type: str) -> Dict[str, Any]:
    """``json_schema_extra`` for a field where an explicit ``null`` is MEANINGFUL.

    Three tools spell a nullable as ``{"type": ["string", "null"]}`` rather than as an
    optional field, and the difference is load-bearing: for
    ``update_transaction.payee_id``, ``bulk_update_transactions.payee_id`` and
    ``set_payee_default_category.category``, passing ``null`` CLEARS the stored value
    while omitting the key leaves it untouched. Collapsing that to a bare
    ``{"type": "string"}`` — which is what transform 2 does to a plain ``Optional[str]``
    — would silently remove the model's ability to clear a field on money records.

    So those fields must opt out explicitly::

        payee_id: Optional[str] = Field(
            default=None,
            json_schema_extra=explicit_null("string"),
            description="...",
        )

    The returned extra replaces the collapsed ``type``, and ``schema_of`` leaves a
    type-as-list alone. Migrate these three LAST, with a human confirming each.
    """
    return {"type": [inner_type, "null"]}


class _NoDocstringDescriptions(GenerateJsonSchema):
    """Transform 5 for a plain ``BaseModel`` too (``schema_of(..., drop_docstrings=True)``).

    A ``complete_model`` response model is a plain ``BaseModel`` and its schema is sent to the
    provider verbatim. A leaked docstring there is engineer prose in the model's prompt: the
    analyst's (describing hidden thinking and a "reasoning channel") tripped Sonnet 5.5's
    ``reasoning_extraction`` safeguard on every analysis call (2026-09-30). Only a description
    equal to the class docstring is dropped, so an explicit ``json_schema_extra`` one survives.
    """

    def model_schema(self, schema):  # type: ignore[override]
        out = super().model_schema(schema)
        doc = schema["cls"].__doc__
        if doc and out.get("description") == inspect.cleandoc(doc):
            out.pop("description")
        return out


def schema_of(
    model: Type[_M], *, tool_name: Optional[str] = None, drop_docstrings: bool = False
) -> Dict[str, Any]:
    """The Anthropic-shaped ``input_schema`` for ``model``.

    Pass ``tool_name`` to record the model against that tool, which is what lets
    ``tests/test_schema_from_model.py`` assert every migrated tool still matches its
    pinned pre-migration schema. ``drop_docstrings`` applies transform 5 to plain
    ``BaseModel``s too (root and nested) — every response schema passes it; tool schemas
    don't, since a few pinned literals carry a nested model's docstring on purpose.
    """
    generator = _NoDocstringDescriptions if drop_docstrings else GenerateJsonSchema
    raw = model.model_json_schema(schema_generator=generator)
    defs = raw.pop("$defs", {})
    out = _normalize(_inline_refs(raw, defs, frozenset()))
    # Pydantic omits ``required`` entirely when no field is required; 265 of 272
    # hand-written roots spell it as ``[]``. Match the majority so an all-optional tool
    # does not gratuitously differ from its literal.
    if out.get("type") == "object":
        out.setdefault("required", [])
    if tool_name is not None:
        _DERIVED[tool_name] = model
    return out


def schema_of_batch_only(
    model: Type[_M],
    *,
    array: str,
    tool_name: Optional[str] = None,
) -> Dict[str, Any]:
    """``schema_of``, advertising ONLY the batch ``array`` — not its bare-field mirror.

    A dual-shape tool declares both an ``array`` of items AND a copy of that item's fields
    at the root, so a single-target call can skip the wrapper. Advertising both spends
    schema bytes twice on one shape and, worse, makes the model choose: the two forms mean
    the same thing, so the choice carries no information and is pure branch surface. The
    duplication is also what lets a model half-mix them (a bare ``payee_id`` alongside an
    ``updates`` array) — a call that type-checks and means something nobody designed.

    ``update_transactions`` is the shape this matches: it advertises ``updates`` only, its
    handler still accepts bare top-level fields, and that leniency is deliberately
    undocumented (see ``ai/tools/ledger.py``'s ``_update_one``). Un-advertising is NOT
    removing: ``registry.dispatch`` validates the EFFECTIVE type only and never enforces
    ``additionalProperties``, so an undeclared key still reaches the handler untouched —
    every existing bare call, including one replayed from a stored agent playbook or an
    in-flight transcript, keeps working byte-for-byte.

    The bare fields stay declared on the MODEL on purpose. The model is the record of what
    the handler accepts; the schema is the record of what the model is TOLD to send. Those
    are different questions, and deleting the fields outright would destroy the answer to
    the first one to change the second.

    Any root ``required`` entry naming an omitted field is replaced by ``array`` itself,
    since a required field that is not advertised is a schema that contradicts itself.
    """
    out = schema_of(model, tool_name=tool_name)
    props = out.get("properties") or {}
    if array not in props:
        raise ValueError(f"schema_of_batch_only: {array!r} is not a field of {model.__name__}")

    items = props[array].get("items") or {}
    for branch in props[array].get("anyOf") or []:
        if isinstance(branch, dict) and "array" in str(branch.get("type")):
            items = branch.get("items") or items
    mirrored = set(items.get("properties") or {})
    if not mirrored:
        raise ValueError(f"schema_of_batch_only: {array!r} has no item properties to mirror")

    omitted = sorted((set(props) - {array}) & mirrored)
    if not omitted:
        raise ValueError(
            f"schema_of_batch_only: {model.__name__} advertises no bare mirror of {array!r} "
            "— use schema_of instead of asserting a duplication that is not there"
        )
    for name in omitted:
        props.pop(name, None)

    required = [r for r in (out.get("required") or []) if r not in omitted]
    if len(required) != len(out.get("required") or []) and array not in required:
        required.append(array)
    out["required"] = required
    return out


def schema_of_no_required(model: Type[_M], *, tool_name: Optional[str] = None) -> Dict[str, Any]:
    """``schema_of``, but omitting the ``required`` key entirely rather than the default ``[]``.

    Only 265 of 272 hand-written roots spell "nothing is required" as ``required: []``; 7
    omit the key altogether (``list_scheduled_tasks``, ``list_import_batches``,
    ``delete_transactions_where``, among others). ``schema_of`` defaults to matching the
    majority spelling (see its own docstring) — this is the escape hatch for the minority,
    used ONLY where the pinned literal has no ``required`` key at all; a caller must not
    reach for it just to tidy up a schema.

    Refuses a model that actually declares a required field, since silently dropping a
    non-empty ``required`` would be a real schema change, not a spelling difference.
    """
    out = schema_of(model, tool_name=tool_name)
    if out.get("required"):
        raise ValueError(
            f"schema_of_no_required called on a model with required fields: {out['required']}"
        )
    out.pop("required", None)
    return out


# --- transform 1: inline $defs/$ref ---------------------------------------------------
# A nested model becomes a ``$ref`` into ``$defs``. Anthropic accepts refs, but every
# hand-written schema in this repo is self-contained, so inlining keeps derived and
# literal schemas comparable (and keeps the diff at migration time to zero).


def _inline_refs(node: Any, defs: Dict[str, Any], seen: frozenset) -> Any:
    if isinstance(node, list):
        return [_inline_refs(v, defs, seen) for v in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        if name in seen:
            # A self-referential model cannot be inlined into a finite document. Fail
            # loudly at import time rather than emitting a truncated schema that would
            # silently under-describe the tool.
            raise ValueError(
                f"recursive $ref to '{name}' cannot be inlined; "
                "flatten the model or hand-write this tool's schema"
            )
        if name not in defs:
            raise ValueError(f"unresolvable $ref: {ref}")
        target = _inline_refs(defs[name], defs, seen | {name})
        # Sibling keys (a per-field description on a nested-model field) override the
        # definition's own.
        siblings = {k: v for k, v in node.items() if k != "$ref"}
        return {**target, **siblings}
    return _walk(node, lambda sub: _inline_refs(sub, defs, seen))


def _walk(node: Dict[str, Any], fn) -> Dict[str, Any]:
    """Apply ``fn`` to every SUBSCHEMA of ``node``, leaving non-schema values alone.

    Keys inside ``properties``/``$defs``/``patternProperties`` are user-chosen names,
    never keywords — the ``create_artifact.title`` hazard from the module docstring.
    """
    out: Dict[str, Any] = {}
    for key, value in node.items():
        if key in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
            out[key] = {name: fn(sub) for name, sub in value.items()}
        elif key in _SCHEMA_LIST_KEYWORDS and isinstance(value, list):
            out[key] = [fn(sub) for sub in value]
        elif key in _SCHEMA_KEYWORDS and isinstance(value, (dict, list)):
            out[key] = fn(value)
        else:
            out[key] = value
    return out


# --- transforms 2-4: nullable collapse, title strip, null-default strip ---------------


def _normalize(node: Any) -> Any:
    if isinstance(node, list):
        return [_normalize(v) for v in node]
    if not isinstance(node, dict):
        return node
    node = _collapse_nullable(node)
    out: Dict[str, Any] = {}
    for key, value in node.items():
        # transform 3 — Pydantic titles every field ("Wash Window Days"); no schema in
        # this repo carries the keyword, and it is pure prompt weight.
        if key == "title":
            continue
        # transform 4 — ``default: null`` is the residue of an optional field, not a
        # declared default. A real default (``limit: int = 20``) survives, matching the
        # 5 tools that declare one.
        if key == "default" and value is None:
            continue
        out[key] = value
    return _walk(out, _normalize)


def _collapse_nullable(node: Dict[str, Any]) -> Dict[str, Any]:
    """transform 2 — ``Optional[X]`` renders as ``anyOf: [X, {"type": "null"}]``.

    Collapse it to ``X``, which is how every hand-written schema spells an optional
    field. Only the exact two-branch X-or-null shape collapses: a genuine multi-branch
    ``anyOf`` (``get_transactions`` has four) is left intact, and a field that opted into
    ``explicit_null`` never produced an ``anyOf`` to begin with.
    """
    branches = node.get("anyOf")
    if not isinstance(branches, list) or len(branches) != 2:
        return node
    null_branch = {"type": "null"}
    others = [b for b in branches if b != null_branch]
    if len(others) != 1 or (len(branches) - len(others)) != 1:
        return node
    rest = {k: v for k, v in node.items() if k != "anyOf"}
    # The branch's own keys first (``type`` leads, as in the literals), then the field's
    # own description/default, which win on conflict.
    return {**others[0], **rest}
