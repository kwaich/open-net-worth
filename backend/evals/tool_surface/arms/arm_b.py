"""ARM B — consolidated: fewer, fatter tools with an ``action`` discriminator.

One tool per DOMAIN (``txns``, ``budget``, ``invest``, ``accounts``, ``categories``,
``reports``), each absorbing every op in that domain behind an ``action`` enum.

Two modelling choices, both deliberate and both reported as the arm's defining costs:

1. **No ``find_tools``.** Consolidation is what removes the need for discovery — that
   is its whole selling point. The consequence, which the token model predicted and
   this arm reproduces honestly, is that a fat tool DRAGS ITS COLD SIBLINGS into the
   always-loaded set: ``invest``'s rare ``dispose``/``realized_pnl`` shapes are in the
   context window on turn 1 of every task, including tasks about groceries.

2. **Flattened union schema.** The per-action properties are unioned at the top level
   with only ``action`` required, and per-action requirements are stated in prose in
   the description. This is what real consolidated tools look like; a strict
   ``oneOf``-per-action schema would be even fatter in tokens, so this choice is
   GENEROUS to arm B on the cost axis rather than rigged against it. The price is that
   JSON-schema validation can no longer enforce per-action required fields — that
   enforcement moves into the handler, and shows up as a recovered/unrecovered error.
"""

from __future__ import annotations

from backend.evals.tool_surface import ops as ops_module
from backend.evals.tool_surface.arms.base import Arm, DispatchTrace

DOMAIN_LABELS = {
    "txns": "Read and write transactions.",
    "budget": "Read the budget and move money between envelopes.",
    "invest": "Read and write investment positions and tax lots.",
    "accounts": "Account directory.",
    "categories": "Category directory.",
    "reports": "Computed portfolio and net-worth reports.",
}


class ArmB(Arm):
    key = "B"
    label = "Consolidated: one fat tool per domain, action discriminator, no discovery"

    def __init__(self, decoys: list[str] | None = None, spec=None) -> None:
        super().__init__(decoys, spec)
        self._domains: dict[str, list[ops_module.Op]] = {}
        for op in ops_module.all_ops().values():
            self._domains.setdefault(op.domain, []).append(op)

    # -- surface ------------------------------------------------------------

    def _raw_tools(self) -> list[dict]:
        offered = []
        for domain, domain_ops in sorted(self._domains.items()):
            properties: dict = {
                "action": {
                    "type": "string",
                    "enum": [o.action for o in domain_ops],
                    "description": "Which operation to perform.",
                }
            }
            lines = [DOMAIN_LABELS.get(domain, domain), "", "Actions:"]
            for op in domain_ops:
                required = ", ".join(op.required) if op.required else "(no required fields)"
                lines.append(f"- {op.action}: {op.summary} Requires: {required}.")
                # Union the per-action properties at the top level. First writer wins:
                # identically-named fields (date, amount) genuinely mean the same thing
                # within a domain, which is exactly why the domain is a coherent group.
                for key, spec in op.properties.items():
                    properties.setdefault(key, spec)
            offered.append(
                {
                    "name": domain,
                    "description": "\n".join(lines),
                    "input_schema": {
                        "type": "object",
                        "properties": properties,
                        "required": ["action"],
                    },
                }
            )
        return offered

    def system_suffix(self) -> str:
        names = self._catalog_names(list(self._domains))
        return (
            "TOOL CATALOG — your full toolset (names only). Every tool below is already "
            "loaded and callable; pick the tool for the domain, then set 'action'.\n"
            + ", ".join(names)
        )

    # -- dispatch -----------------------------------------------------------

    def dispatch(self, name: str, payload: dict) -> tuple[str, DispatchTrace]:
        domain_ops = self._domains.get(name)
        if domain_ops is None:
            return self._unknown(name, f"unknown tool: {name}")

        payload = dict(payload or {})
        action = payload.pop("action", None)
        if not action:
            return self._unknown(
                name, f"'action' is required — one of: {[o.action for o in domain_ops]}"
            )

        op = next((o for o in domain_ops if o.action == action), None)
        if op is None:
            return self._unknown(
                name, f"unknown action '{action}' — one of: {[o.action for o in domain_ops]}"
            )

        # NOTE: the missing-required-field check deliberately does NOT live here. It is
        # in the shared ``Arm._run`` so every arm gets byte-identical treatment — see the
        # note there. Arm B is the arm whose flattened schema makes this mistake most
        # likely, so owning the friendliest error for it was a real (if accidental) bias.
        text, ok, error = self._run(op, payload)
        return text, DispatchTrace(
            tool=f"{name}.{action}", op=op.name, ok=ok, error=error, payload=payload
        )
