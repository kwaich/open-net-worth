"""ARM C — describe/apply: a thin dispatcher whose per-op shapes live outside the schema.

Two tools, total, for the entire application:

  describe(path)     -> "ops" for the index, "ops/<name>" for one op's parameter schema
  apply(op, items)   -> execute an op over one or more item payloads

The defining property, and the thing being measured: ``apply``'s ``items`` is an array
of OPAQUE objects (``additionalProperties: true``, no declared per-op properties). So:

* the per-turn schema cost is tiny and CONSTANT in the number of ops — the potential
  big win;
* the model must spend a round-trip on ``describe`` before it can correctly shape a
  call it has not already seen — the potential cost;
* the API can no longer reject a malformed payload at the schema layer. A wrong field
  name reaches the handler. That is a real loss of a safety net, and the scorer counts
  the resulting errors (and whether the model recovered from them) rather than hiding
  them.

``items`` being a list is not decoration: it is what makes a describe/apply surface
competitive on multi-row work like a statement import, so removing it would rig the
arm. A single-call op is expressed as a one-element list.
"""

from __future__ import annotations

import json

from backend.evals.tool_surface import introspect
from backend.evals.tool_surface import ops as ops_module
from backend.evals.tool_surface.arms.base import Arm, DispatchTrace

DESCRIBE_SCHEMA = {
    "name": "describe",
    "description": (
        "Look up the exact parameters an op takes before calling it with apply. "
        "Use 'ops' to list every available op, or 'ops/<op name>' for one op's full "
        "parameter schema and required fields."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "'ops' for the index, or 'ops/<op name>' e.g. 'ops/txn.add'.",
            }
        },
        "required": ["path"],
    },
}

APPLY_SCHEMA = {
    "name": "apply",
    "description": (
        "Execute an op. 'op' is an op name from describe('ops'); 'items' is a list of "
        "payload objects, one per record to process (pass a single-element list for a "
        "one-off call, or many items to process a batch in one call). Call "
        "describe('ops/<op name>') first if you do not already know the op's exact "
        "field names — apply does not validate field names for you."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "op": {"type": "string", "description": "Op name, e.g. 'txn.add'."},
            "items": {
                "type": "array",
                "description": "One payload object per record. Shape depends on the op — see describe.",
                "items": {"type": "object", "additionalProperties": True},
            },
        },
        "required": ["op", "items"],
    },
}


class ArmC(Arm):
    key = "C"
    label = "Describe/apply: thin dispatcher, per-op shapes fetched at runtime"

    # -- surface ------------------------------------------------------------

    def _raw_tools(self) -> list[dict]:
        return [DESCRIBE_SCHEMA, APPLY_SCHEMA]

    def system_suffix(self) -> str:
        names = self._catalog_names([op.name for op in ops_module.all_ops().values()])
        return (
            "OP CATALOG — every capability available to you (names only). To call one, use "
            'apply(op="<name>", items=[{...}]). If you are not certain of an op\'s exact '
            'field names, call describe("ops/<name>") first — apply will not correct a '
            "misspelled field for you.\n" + ", ".join(names)
        )

    # -- dispatch -----------------------------------------------------------

    def dispatch(self, name: str, payload: dict) -> tuple[str, DispatchTrace]:
        payload = dict(payload or {})
        if name == "describe":
            result = introspect.describe(payload.get("path") or "ops")
            return json.dumps(result, default=str), DispatchTrace(
                tool="describe",
                op=None,
                ok="error" not in result,
                error=result.get("error"),
                is_discovery=True,
                payload=payload,
            )

        if name != "apply":
            return self._unknown(name, f"unknown tool: {name}")

        op_name = payload.get("op")
        op = ops_module.get_op(op_name) if op_name else None
        if op is None:
            return self._unknown(
                "apply", f"unknown op '{op_name}' — call describe('ops') to list valid ops"
            )

        items = payload.get("items")
        if items is None:
            items = [{}]
        if not isinstance(items, list):
            return self._unknown("apply", "'items' must be a list of payload objects")
        if not items:
            items = [{}]

        results = []
        any_error: str | None = None
        for item in items:
            if not isinstance(item, dict):
                results.append({"error": "each item must be an object"})
                any_error = any_error or "each item must be an object"
                continue
            text, ok, error = self._run(op, item)
            results.append(json.loads(text))
            if not ok:
                any_error = any_error or error

        # A single-item call returns the bare result so the model is not forced to
        # unwrap a one-element list on every ordinary call.
        body = results[0] if len(results) == 1 else {"results": results}
        return json.dumps(body, default=str), DispatchTrace(
            tool=f"apply:{op_name}",
            op=op.name,
            ok=any_error is None,
            error=any_error,
            payload=payload,
        )
