"""The ``describe`` primitive arm C depends on.

STATUS (verified at run time, reported in every run's header): the parallel lane's
``backend/services/domain_introspect.py`` and ``backend/ai/tools/describe.py`` HAVE now
landed — but they are **not substitutable** for arm C's describe, and arm C therefore
uses the local implementation below. This is a correctness constraint, not a preference:

``domain_introspect.describe`` introspects the **live service write chokes** by parsing
their source — its op namespace is the real dispatcher's (``apply_budget_change`` change
types and friends). Arm C's ``apply`` executes the **harness's** ``ops.py`` registry.
Pointing describe at one namespace while apply executes another would hand the model op
names it cannot call, which is a harness bug, not a property of the describe/apply
surface — it showed up immediately in the first mock run as unresolvable-op errors.

So the honest reading of any arm C result is: it measures the describe/apply SHAPE
(runtime schema fetch, opaque items, no API-level validation) using a describe whose
content is generated from the same op table apply executes. It does NOT measure the
production ``domain_introspect`` implementation's own output quality. Wiring arm C to
the real primitive would require the real ``apply`` too — a bigger change than this
harness should make, and one that would stop isolating the surface as the variable.

``real_introspect_status()`` reports what was found so a reader never has to guess.

The local implementation deliberately covers ONLY what the describe/apply contract
needs — anything richer would flatter arm C with a better introspection surface than a
real one would have:

  describe("ops")            -> the op index (names + one-line summaries)
  describe("ops/<op name>")  -> that op's full parameter schema
"""

from __future__ import annotations

from typing import Any

from backend.evals.tool_surface import ops as ops_module

_REAL_MODULE: Any = None
_TRIED = False


def _real() -> Any:
    """Return the production introspection module if it exists, else None."""
    global _REAL_MODULE, _TRIED
    if not _TRIED:
        _TRIED = True
        try:  # pragma: no cover - depends on a parallel lane landing
            from backend.services import domain_introspect

            _REAL_MODULE = domain_introspect
        except Exception:
            _REAL_MODULE = None
    return _REAL_MODULE


def real_introspect_status() -> str:
    """One line for the run header describing what arm C's describe actually is."""
    if _real() is None:
        return "local implementation (services.domain_introspect not present)"
    return (
        "local implementation (services.domain_introspect EXISTS but describes the live "
        "service chokes, a different op namespace than arm C's apply executes)"
    )


def describe(path: str) -> dict:
    """Resolve an introspection path to a JSON-serialisable description.

    Deliberately does NOT delegate to ``domain_introspect`` — see the module docstring.
    """
    return _describe_local(path)


def _describe_local(path: str) -> dict:
    cleaned = (path or "").strip().strip("/")
    if cleaned in ("", "ops"):
        return {
            "path": "ops",
            "ops": [
                {"op": op.name, "summary": op.summary, "writes": op.writes}
                for op in ops_module.all_ops().values()
            ],
        }

    if cleaned.startswith("ops/"):
        name = cleaned[len("ops/") :]
        op = ops_module.get_op(name)
        if op is None:
            close = [o.name for o in ops_module.all_ops().values() if name.split(".")[0] in o.name]
            return {
                "error": f"unknown op '{name}'",
                "did_you_mean": close[:8] or [o.name for o in ops_module.all_ops().values()][:8],
            }
        return {
            "path": cleaned,
            "op": op.name,
            "summary": op.summary,
            "writes": op.writes,
            "parameters": op.properties,
            "required": op.required,
        }

    return {"error": f"unknown describe path '{path}' — try 'ops' or 'ops/<op name>'"}
