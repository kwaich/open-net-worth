"""The retired import/export paths must never come back.

2026-08-23: the import/export REST surface moved onto two coherent prefixes —
``/api/data/*`` for whole-book portability, ``/api/statements/*`` for a single bank or
broker file. See ``docs/adr/2026-08-23-import-export-routes-coherent-prefixes.md``.

The rename was a CLEAN BREAK: no aliases, the old paths simply stop existing. That decision
is what makes this file necessary. With an alias, a stale caller degrades quietly; without
one it is a hard 404 — so the cost of a path sneaking back in (or of a caller being missed)
is a broken feature, not a deprecation warning.

Two guards, because the app is two languages:

  * The BACKEND guard is a runtime assertion over the real route table — the strongest form
    available, since it cannot be fooled by a path built from an f-string or a router prefix.
  * The FRONTEND guard is a source scan, because those callers are TypeScript and never
    appear in the Python route table. It matches only real call positions
    (``authFetch('…')`` / ``fetch('…')``), never prose, so the comments that explain this
    history are free to keep naming the old paths.

The bug that started all this: ``import_api.py`` and ``portability.py`` BOTH declared
``POST /api/import``. FastAPI resolves on first registration, so the bundle-restore endpoint
was unreachable and an uploaded ``.zip`` was fed to the CSV statement parser. Route
uniqueness itself is guarded separately, in ``tests/test_route_uniqueness.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Every path retired by the 2026-08-23 rename. A bare "/api/import" and "/api/export" are
# included: they are not merely renamed, they must resolve to NOTHING, since re-adding either
# is exactly how the original collision would return.
RETIRED_PATHS = (
    "/api/export",
    "/api/import",
    "/api/import/validate",
    "/api/import/preview",
    "/api/import/source-peek",
    "/api/import/commit",
    "/api/import/formats",
    "/api/import/merge",
    "/api/import/csv",
    "/api/import/csv/preview",
)

_FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"

# A real call site, not prose: the path sits inside quotes as a fetch/authFetch argument.
# Deliberately narrow — this file and the route-uniqueness test both discuss the old paths in
# comments, and a docstring naming history is accurate, not a defect.
_CALL_RE = re.compile(
    r"""(?:authFetch|fetch)\(\s*['"`](/api/(?:import|export)(?:/[a-z-]+)*)['"`]""",
)


def _registered_paths() -> set[str]:
    """Every path the live app actually serves.

    Uses the same flattening FastAPI's own OpenAPI generator uses: since fastapi 0.141 an
    ``include_router`` call appends a lazy wrapper rather than splicing routes into
    ``app.routes``, so walking ``app.routes`` naively finds no API routes at all and every
    assertion here would pass vacuously.
    """
    from fastapi.routing import iter_route_contexts

    from backend.main import app

    return {ctx.route.path for ctx in iter_route_contexts(app.routes) if hasattr(ctx.route, "path")}


@pytest.mark.parametrize("path", RETIRED_PATHS)
def test_retired_path_is_not_served(path: str):
    served = _registered_paths()
    assert path not in served, (
        f"{path} was retired on 2026-08-23 and must not be served again. "
        f"Whole-book portability lives at /api/data/*, statement import at /api/statements/*. "
        f"See docs/adr/2026-08-23-import-export-routes-coherent-prefixes.md."
    )


def test_the_replacement_paths_are_actually_served():
    """The mirror of the guard above — proves it is asserting absence against a real table
    rather than passing because the table is empty."""
    served = _registered_paths()
    for path in (
        "/api/data/export",
        "/api/data/import",
        "/api/data/import/validate",
        "/api/statements/import",
    ):
        assert path in served, f"{path} should be served after the rename but is not"


def test_no_frontend_caller_uses_a_retired_path():
    offenders: list[str] = []
    for f in _FRONTEND.rglob("*.ts*"):
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for hit in _CALL_RE.finditer(line):
                offenders.append(f"{f.relative_to(_FRONTEND.parent.parent)}:{i} → {hit.group(1)}")

    assert not offenders, (
        "These call retired import/export paths, which now 404 (the rename kept no aliases):\n  "
        + "\n  ".join(offenders)
        + "\nUse /api/data/* for whole-book bundles, /api/statements/* for one statement file."
    )
