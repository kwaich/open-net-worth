"""
Regression test for the `POST /api/import` route collision (fixed 2026-08-23).

THE BUG: backend/api/import_api.py (the CSV/OFX/QIF statement importer) and
backend/api/portability.py (the full-bundle restore) both declared
`POST /api/import`. FastAPI does not error on a duplicate route registration
-- it resolves by FIRST-REGISTRATION ORDER and silently shadows every route
after the first. backend/main.py included import_api's router (~line 922)
before portability's (~line 985), so every POST to /api/import for a bundle
restore was actually dispatched to the statement-CSV handler, which choked on
the zip bytes with `_csv.Error: new-line character seen in unquoted field`.
The documented restore path (paired with /api/export and
/api/import/validate) was unreachable from every caller, and nothing at
import time, startup, or in the OpenAPI schema flagged it -- a curl/TestClient
hit on the surviving route looks completely healthy.

THE FIX (in two steps, same day): first import_api.py's handler moved to
`POST /api/statements/import` to break the collision; portability.py kept
`POST /api/import`. Later the same day portability's whole surface moved
again, for prefix coherence, onto `/api/data/*` (export/import/import-validate)
-- so today the bundle restore lives at `POST /api/data/import`, not
`POST /api/import` at all. Both renames are why this file pins the *current*
handler for each path rather than the bug report's original path names: a
stale literal path would stop resolving to anything and silently degrade
into a no-op assertion, not catch a regression.

These tests pin both directions shut: a general guard that would have caught
the original collision (and catches the next one, on any path), and a narrow
pin that today's two import-shaped paths resolve to the exact handlers the
fixes intended.
"""

from __future__ import annotations

from collections import defaultdict

import fastapi.routing as fastapi_routing

from backend.main import app

# (path, method) pairs that are deliberately declared by more than one route.
# Add an entry here ONLY for a verified-intentional duplicate, with a comment
# naming it and why -- never to silence a collision that hasn't been reviewed.
#
# Empty as of 2026-08-23: a full walk of backend.main.app.routes turned up one
# real (non-deliberate) duplicate -- `activity_router` was `app.include_router`-ed
# twice in backend/main.py, registering GET /api/investments/activity to the same
# handler twice over. That is exactly the failure shape this guard exists to
# catch, so it was fixed at the source (the redundant include_router call
# removed) rather than allowlisted here, which would have hidden a real defect
# behind a guard whose entire purpose is to surface it.
_ALLOWED_DUPLICATES: set[tuple[str, str]] = set()


def _iter_route_method_pairs():
    """Yield (path, method, endpoint) for every concrete APIRoute reachable from `app`.

    FastAPI's `app.routes` is no longer a flat list of APIRoute objects here --
    `include_router` now appends a lazy `_IncludedRouter` wrapper, so a naive
    walk of `app.routes` sees zero API routes at all. `fastapi.routing.
    iter_route_contexts` is the same public flattening helper FastAPI's own
    OpenAPI-schema generator uses (`fastapi/openapi/utils.py::get_openapi`), so
    walking it here matches what the app itself considers "the registered
    routes" rather than reimplementing route resolution by hand.
    """
    for route_context in fastapi_routing.iter_route_contexts(app.routes):
        if not isinstance(route_context.original_route, fastapi_routing.APIRoute):
            continue  # e.g. the SPA catch-all mount -- a prefix match, not an exact path
        path = route_context.path
        endpoint = route_context.endpoint
        methods = route_context.methods or set()
        if path is None or endpoint is None:
            continue
        for method in methods:
            yield path, method, endpoint


def test_no_duplicate_path_method_pairs():
    """No (path, method) may be declared by two different routes.

    This is the bug in general form: FastAPI resolves a duplicate
    registration by include-order and silently shadows every handler after
    the first, so the only way to catch it is to walk the resolved route
    table itself -- exactly how POST /api/import used to silently route
    bundle restores into the CSV importer until someone tried to actually
    restore a bundle.
    """
    endpoints_by_key: dict[tuple[str, str], list] = defaultdict(list)
    for path, method, endpoint in _iter_route_method_pairs():
        endpoints_by_key[(path, method)].append(endpoint)

    offenders = {
        key: endpoints
        for key, endpoints in endpoints_by_key.items()
        if len(endpoints) > 1 and key not in _ALLOWED_DUPLICATES
    }

    if offenders:
        lines = [
            f"  {method} {path} -> " + ", ".join(f"{e.__module__}.{e.__name__}" for e in endpoints)
            for (path, method), endpoints in sorted(offenders.items())
        ]
        raise AssertionError(
            "Duplicate (path, method) route registrations found. FastAPI "
            "resolves these by first-registration order in backend/main.py "
            "and silently shadows every handler after the first -- this is "
            "exactly how POST /api/import used to route bundle restores into "
            "the statement CSV importer:\n" + "\n".join(lines)
        )


def _endpoint_for(path: str, method: str):
    matches = [
        endpoint
        for route_path, route_method, endpoint in _iter_route_method_pairs()
        if route_path == path and route_method == method
    ]
    assert matches, f"no route registered for {method} {path}"
    return matches[0]


def test_import_shaped_routes_resolve_to_the_correct_handlers():
    """Pins the fix: which handler owns the bundle restore vs. the statement importer.

    The bundle restore (backend/api/portability.py) must resolve to
    `import_data`, and the statement-file commit (backend/api/import_api.py,
    the router that had to move to break the original collision) must resolve
    to `commit_import`. Neither may ever again resolve to the other.
    """
    bundle_restore = _endpoint_for("/api/data/import", "POST")
    assert bundle_restore.__module__ == "backend.api.portability"
    assert bundle_restore.__name__ == "import_data"

    statement_commit = _endpoint_for("/api/statements/import", "POST")
    assert statement_commit.__module__ == "backend.api.import_api"
    assert statement_commit.__name__ == "commit_import"
