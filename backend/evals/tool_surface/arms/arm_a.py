"""ARM A — the current surface: many named tools, full schemas inline for the core set.

Mirrors production's discovery model as closely as a harness can:

* the ``core`` ops are offered with full inline schemas from turn 1;
* every other op is hidden behind ``find_tools(query=...)``, which keyword-searches
  names + descriptions and LOADS matches for the rest of the session (sticky, exactly
  like ``ai/tools/_groups.py``);
* a names-only catalog of everything (core + rare + decoys) rides the system prompt,
  so the model knows a capability exists before it knows the capability's shape.

The ``find_tools`` scoring below is deliberately a port of the production one
(``_search_tools``: name substring 3 / whole-word 6, description substring 1 /
whole-word 3) so discovery difficulty matches the real app rather than being a
strawman the other arms beat easily.
"""

from __future__ import annotations

import json
import re

from backend.evals.tool_surface import ops as ops_module
from backend.evals.tool_surface.arms.base import Arm, DispatchTrace

FIND_TOOLS_SCHEMA = {
    "name": "find_tools",
    "description": (
        "Search for a tool by keyword when the tool you need is not already available. "
        "Returns matching tools and loads them for the rest of this session so you can call them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keyword search over every tool's name and description (e.g. 'realized gains', 'sell shares').",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum tools to return (default 10).",
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": ["query"],
    },
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


class ArmA(Arm):
    key = "A"
    label = "Current: many named tools, core schemas inline, rare via find_tools"

    def __init__(self, decoys: list[str] | None = None, spec=None) -> None:
        super().__init__(decoys, spec)
        self._by_tool = {o.tool_name: o for o in ops_module.all_ops().values()}

    # -- surface ------------------------------------------------------------

    def _raw_tools(self) -> list[dict]:
        offered = [
            {"name": o.tool_name, "description": o.summary, "input_schema": o.input_schema()}
            for o in ops_module.all_ops().values()
            if o.tier == "core" or o.tool_name in self._loaded
        ]
        offered.append(FIND_TOOLS_SCHEMA)
        return offered

    def system_suffix(self) -> str:
        names = self._catalog_names([o.tool_name for o in ops_module.all_ops().values()])
        # Mirrors production's catalog preamble (ai/tools/catalog.py). It must say that
        # calling a name directly LOADS it, because that is what the dispatch gate now
        # does — telling the model find_tools is mandatory would suppress the auto-load
        # path and inflate arm A's round-trip count for a reason that is not real.
        return (
            "TOOL CATALOG — your full toolset (names only). Call a name directly and its "
            'schema loads automatically, or find_tools(query="...") to search descriptions '
            "and load matches.\n" + ", ".join(names)
        )

    # -- dispatch -----------------------------------------------------------

    def dispatch(self, name: str, payload: dict) -> tuple[str, DispatchTrace]:
        if name == "find_tools":
            return self._find_tools(payload)

        op = self._by_tool.get(name)
        if op is None:
            return self._unknown(name, f"unknown tool: {name}")

        # Production's INTERACTIVE DISCOVERY GATE (ai/assistant.py dispatch loop): a
        # known-but-unloaded internal tool is NOT refused — it is auto-loaded and the
        # model is bounced with "call it again", so the retry uses the real schema. That
        # makes find_tools optional for internal tools (CLAUDE.md: "just call them").
        #
        # Modelling this as a hard refusal — which this arm originally did — would
        # handicap arm A with an obstacle the real product does not have, and the
        # handicap would land exactly on the needs_discovery tasks. That is a bias
        # unrelated to surface shape, so it is reproduced faithfully here instead.
        if op.tier != "core" and op.tool_name not in self._loaded:
            self._loaded.add(op.tool_name)
            message = f"tool '{name}' is now loaded — call it again."
            return json.dumps({"status": message}), DispatchTrace(
                tool=name, op=None, ok=True, is_discovery=True, payload=payload
            )

        text, ok, error = self._run(op, payload)
        return text, DispatchTrace(tool=name, op=op.name, ok=ok, error=error, payload=payload)

    def _find_tools(self, payload: dict) -> tuple[str, DispatchTrace]:
        query = (payload or {}).get("query") or ""
        if not query:
            message = "'query' is required — search for a tool by keyword."
            return json.dumps({"error": message}), DispatchTrace(
                tool="find_tools", op=None, ok=False, error=message, is_discovery=True
            )

        limit = int((payload or {}).get("max_results") or 10)
        scored: list[tuple[int, str, str]] = []
        for op in ops_module.all_ops().values():
            score = 0
            name_tokens = _tokens(op.tool_name)
            desc_tokens = _tokens(op.summary)
            for token in _tokens(query):
                if token in op.tool_name.lower():
                    score += 6 if token in name_tokens else 3
                if token in op.summary.lower():
                    score += 3 if token in desc_tokens else 1
            if score > 0:
                scored.append((score, op.tool_name, op.summary))

        scored.sort(key=lambda row: (-row[0], row[1]))
        hits = scored[:limit]
        loaded = []
        for _, tool_name, summary in hits:
            op = self._by_tool[tool_name]
            if op.tier == "core":
                continue  # already offered — production filters these out too
            self._loaded.add(tool_name)
            loaded.append({"name": tool_name, "summary": summary})

        return json.dumps({"loaded": loaded}), DispatchTrace(
            tool="find_tools", op=None, ok=True, is_discovery=True, payload=payload
        )
