"""ARM A-real — the FIDELITY CONTROL, not a fourth candidate surface.

Arms A/B/C are all shims over ``ops.py``. That is what isolates the surface as the
only variable — but it invites the obvious objection: *"your arm A is a strawman; the
real thing behaves differently."*

This arm answers that objection with a measurement instead of an argument. It offers
the ACTUAL production surface — real ``TOOL_SCHEMAS`` filtered to the real
``CORE_TOOL_NAMES``, the real ``find_tools``, and the real ``execute_tool`` dispatch
into the real tool handlers — against the same fixture and the same outcome-based
scorer.

Read the result as a validity check on the whole harness:

* **A-real ≈ A** → the arm A shim is faithful, so the A-vs-B-vs-C comparison is about
  surface shape and can be trusted.
* **A-real ≫ A or A-real ≪ A** → the shim is NOT faithful. In that case the B/C
  numbers should be discarded, because the shimmed baseline they are measured against
  does not represent the thing the decision is about.

It is deliberately excluded from the default arm set: it does not share ``ops.py``
handlers, so it is not a like-for-like surface comparison — only a calibration.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from backend.evals.tool_surface.arms.base import Arm, DispatchTrace

# Maps a real production tool name onto the canonical op it corresponds to, so
# tool-selection accuracy is scored on the same axis as the other arms. Only the tools
# the task set can legitimately need are mapped; anything else scores as an unmapped
# (but still executed) call.
REAL_TOOL_TO_OP: dict[str, str] = {
    "list_accounts": "accounts.list",
    "list_categories": "categories.list",
    "get_transactions": "txn.list",
    "get_net_worth": "networth.get",
    "get_budget_month": "budget.month",
    "get_portfolio": "positions.list",
    "get_realized_pnl": "lots.realized_pnl",
    "get_lots": "lots.list",
    "add_transaction": "txn.add",
    "add_transactions": "txn.add_many",
    "bulk_add_transactions": "txn.add_many",
    "update_transaction": "txn.update",
    "delete_transaction": "txn.delete",
    "assign_budget": "budget.assign",
    "move_money": "budget.move_money",
    "set_envelope_target": "budget.set_target",
    "create_envelope": "budget.create_envelope",
    "acquire_lots": "lots.acquire",
    "dispose_lots": "lots.dispose",
}


class ArmAReal(Arm):
    key = "A-real"
    label = "FIDELITY CONTROL: the real production registry (real schemas + real handlers)"

    def __init__(self, decoys: list[str] | None = None, spec=None) -> None:
        super().__init__(decoys, spec)
        from backend.ai.tools import TOOL_SCHEMAS
        from backend.ai.tools._groups import CORE_TOOL_NAMES

        self._all: dict[str, dict] = {s["name"]: s for s in TOOL_SCHEMAS}
        self._core = set(CORE_TOOL_NAMES)
        self._loop = asyncio.new_event_loop()

    def close(self) -> None:
        try:
            self._loop.close()
        except Exception:  # pragma: no cover
            pass

    # -- surface ------------------------------------------------------------

    def _raw_tools(self) -> list[dict]:
        names = (self._core | self._loaded) & set(self._all)
        return [self._all[n] for n in sorted(names)]

    def system_suffix(self) -> str:
        from backend.ai.tools.catalog import build_tool_catalog

        try:
            catalog = build_tool_catalog()
        except Exception:  # pragma: no cover - catalog is best-effort in production too
            catalog = ", ".join(sorted(self._all))
        return "TOOL CATALOG — your full toolset.\n" + catalog

    # -- dispatch -----------------------------------------------------------

    def dispatch(self, name: str, payload: dict) -> tuple[str, DispatchTrace]:
        from backend.ai.tools import execute_tool

        if name not in self._all:
            return self._unknown(name, f"unknown tool: {name}")

        # Production's interactive discovery gate: a known-but-unloaded tool is
        # auto-loaded and the model is bounced to call it again with the real schema.
        if name not in self._core and name not in self._loaded and name != "find_tools":
            self._loaded.add(name)
            message = f"tool '{name}' is now loaded — call it again."
            return json.dumps({"status": message}), DispatchTrace(
                tool=name, op=None, ok=True, is_discovery=True, payload=payload
            )

        try:
            text, _ = self._loop.run_until_complete(execute_tool(name, dict(payload or {})))
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"
            return json.dumps({"error": message}), DispatchTrace(
                tool=name, op=REAL_TOOL_TO_OP.get(name), ok=False, error=message, payload=payload
            )

        error = _error_of(text)
        if name == "find_tools":
            self._absorb_find_tools(text)
        return text, DispatchTrace(
            tool=name,
            op=REAL_TOOL_TO_OP.get(name),
            ok=error is None,
            error=error,
            is_discovery=(name == "find_tools"),
            payload=payload,
        )

    def _absorb_find_tools(self, text: str) -> None:
        try:
            for entry in (json.loads(text) or {}).get("loaded", []):
                if isinstance(entry, dict) and entry.get("name"):
                    self._loaded.add(entry["name"])
        except Exception:  # pragma: no cover
            pass


def _error_of(text: str) -> str | None:
    try:
        parsed: Any = json.loads(text)
    except Exception:
        return None
    if isinstance(parsed, dict) and parsed.get("error"):
        return str(parsed["error"])
    return None
