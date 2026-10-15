"""The Arm protocol — what a tool surface must provide to be measurable.

An arm answers exactly two questions:

* ``tools()`` — the tool schemas offered to the model on THIS turn (an arm may grow
  this set mid-run, e.g. after ``find_tools`` loads a rare tool).
* ``dispatch(name, payload)`` — execute a tool call, returning the JSON string fed
  back to the model plus a ``DispatchTrace`` recording what really happened.

Everything an arm does bottoms out in ``ops.Op.handler``. An arm may reshape the call
(unpack an ``action`` discriminator, look up an op by name, fetch a schema) but must
never implement business logic of its own — that is what keeps the comparison honest.

``DispatchTrace.op`` is the canonical op the call resolved to (or ``None`` if the model
failed to select a real capability), which is how the scorer measures tool-selection
accuracy in an arm-independent way: arm A's ``add_transaction``, arm B's
``txns{action:add}`` and arm C's ``apply{op:txn.add}`` all resolve to ``txn.add``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from backend.evals.tool_surface import ops as ops_module


@dataclass
class DispatchTrace:
    """What one tool call actually did, normalised across arms."""

    tool: str  # the tool name the model called
    op: str | None  # canonical op it resolved to, None if unresolved
    ok: bool  # did it execute without an error result
    error: str | None = None
    is_discovery: bool = False  # find_tools / describe — a round-trip that does no work
    payload: dict = field(default_factory=dict)


class Arm:
    """Base class. Subclasses implement ``tools`` and ``dispatch``."""

    #: short id used in reports
    key: str = ""
    #: human-readable one-liner for the report
    label: str = ""

    def __init__(self, decoys: list[str] | None = None, spec: Any = None) -> None:
        from backend.evals.tool_surface.spec import ArmSpec

        self.decoys = decoys or []
        self.spec = spec or ArmSpec(key=self.key or "A", surface=self.key or "A")
        self._loaded: set[str] = set()

    # -- surface ------------------------------------------------------------

    def tools(self) -> list[dict]:
        """Offered tool schemas, with the arm's description policy applied.

        Subclasses implement ``_raw_tools``; compression is applied HERE so the lever
        works identically on every surface and cannot be implemented differently by one.
        """
        schemas = self._raw_tools()
        if getattr(self.spec, "descriptions", "full") == "compressed":
            from backend.evals.tool_surface.spec import compress_schema

            schemas = [compress_schema(s) for s in schemas]
        return schemas

    def _raw_tools(self) -> list[dict]:  # pragma: no cover - abstract
        raise NotImplementedError

    def system_suffix(self) -> str:
        """Arm-specific instructions appended to the SHARED system prompt.

        Kept as short as possible and reported verbatim in the confound list, because
        any asymmetry here is the most likely alternative explanation for a result.
        """
        return ""

    def dispatch(self, name: str, payload: dict) -> tuple[str, DispatchTrace]:  # pragma: no cover
        raise NotImplementedError

    # -- shared helpers -----------------------------------------------------

    @staticmethod
    def _run(op: ops_module.Op, params: dict) -> tuple[str, bool, str | None]:
        """Execute an op handler, converting any exception into an error result.

        Identical for every arm, so error presentation can never differ between them.

        The missing-required-field check lives HERE, in the shared layer, rather than in
        any one arm. It used to live in arm B's dispatch, which quietly handed arm B the
        best error message for the very mistake its flattened schema makes most likely
        (A and C instead surfaced a raw ``KeyError: 'date'`` from deep inside the store).
        An arm may reshape a call; it may not own error quality.
        """
        missing = [f for f in op.required if (params or {}).get(f) is None]
        if missing:
            message = f"missing required field(s) for {op.name}: {', '.join(missing)}"
            return json.dumps({"error": message}), False, message

        try:
            result = op.handler(dict(params or {}))
        except Exception as exc:  # noqa: BLE001 - an arm must never crash the runner
            result = {"error": f"{type(exc).__name__}: {exc}"}
        error = result.get("error") if isinstance(result, dict) else None
        return json.dumps(result, default=str), error is None, error

    def _unknown(self, tool: str, message: str) -> tuple[str, DispatchTrace]:
        return json.dumps({"error": message}), DispatchTrace(
            tool=tool, op=None, ok=False, error=message
        )

    def _catalog_names(self, visible: list[str]) -> list[str]:
        """Sorted tool-name catalog, padded with decoys so catalog size is realistic.

        Decoys are real tool names from the production registry that this harness does
        not implement. They cost the same tokens in every arm and make tool SELECTION
        genuinely hard (the model must find the right name among many), which is the
        thing the catalog is supposed to make hard in production.
        """
        return sorted(set(visible) | set(self.decoys))


def json_dumps(value: Any) -> str:
    return json.dumps(value, default=str)
