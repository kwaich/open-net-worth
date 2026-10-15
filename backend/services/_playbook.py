"""
_playbook — legacy helpers for stored agent tool allowlists.

Skills do not use this module: a skill carries no tool list (its experimental standard
``allowed-tools`` field is dropped on read). These helpers remain for agent
configurations that still have an application-level tool scope:

  * resolving the live registered-tool-name set (via the ``core.tool_registry_seam``);
  * a validation core — non-empty allowlist + every listed tool exists in the live
    registry (with the "empty registry → skip the membership check" carve so a unit test
    that imports ``services`` without ``ai`` doesn't reject everything). Existence is the
    ONLY judgement — the tighten-only ``ceiling`` a caller could pass is gone with the rest
    of the permission system;
  * the degraded / missing-tools computation + the list-row projection.

Layering: this is a ``services``-internal leaf. It imports only ``core`` (the tool-registry
seam) + stdlib — never ``ai`` / ``api`` — so all four import-linter contracts stay green.

Note on the registry seam: ``known_tools`` is always passed IN by the caller (each module
resolves it through its own ``tool_names``/``_registered_tool_names`` symbol). This keeps a
single source for the seam read AND preserves each module's monkeypatch point — the
agents/skills test suites stub their own module's seam, not this helper's.
"""

from __future__ import annotations

from collections.abc import Iterable


def validate_tool_allowlist(
    allowed: Iterable[str] | None,
    *,
    kind: str,
    known_tools: set[str] | None,
) -> list[str]:
    """Shared validation core for a playbook's ``allowed_tools``. Returns a list of
    human-readable problems (empty = valid); the caller prepends/appends its own domain
    checks.

    Checks, in order:
      * non-empty allowlist (``"A {kind} must allow at least one tool …"``);
      * if the registry (``known_tools``) is populated, every tool must exist in it — an
        empty registry skips this (a ``services``-only import sees no tools), so authoring
        isn't blocked just because ``ai`` wasn't imported.

    There used to be a third check here: a tighten-only ``ceiling`` a caller could pass to
    refuse a tool by POLICY (agents passed the read+notify+propose set). It is gone with the
    rest of the permission system — existence is the only thing a name is judged on.
    """
    problems: list[str] = []
    allowed = list(allowed or [])
    if not allowed:
        problems.append(f"A {kind} must allow at least one tool (allowed_tools is empty).")
        return problems

    # A stored name is judged EXACTLY as written: tool names are final, so there is no
    # former name to resolve and a stale one is genuinely unknown.
    for name in allowed:
        if known_tools and name not in known_tools:
            problems.append(f"Unknown tool in allowed_tools: '{name}'.")

    return problems


def playbook_missing_tools(
    allowed: Iterable[str] | None, known_tools: set[str] | None
) -> list[str]:
    """Tools the playbook references that are no longer in the registry, so the operator
    can find and fix them. With an empty registry we can't tell, so nothing is reported
    missing."""
    if not known_tools:
        return []
    return [name for name in (allowed or []) if name not in known_tools]


def playbook_list_row(
    item: dict, *, known_tools: set[str] | None, extra: dict | None = None
) -> dict:
    """The shared roster/list projection: the item plus a ``degraded`` flag + its
    ``missing_tools`` list. ``extra`` carries agent-domain row fields."""
    missing = playbook_missing_tools(item.get("allowed_tools"), known_tools)
    return {
        **item,
        "degraded": bool(missing),
        "missing_tools": missing,
        **(extra or {}),
    }
