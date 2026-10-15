"""The permission ladder's DEAD-ROUTE scar (H2) — the frontend seam's routes, pinned.

Split out of ``test_tool_permissions.py`` (formerly ``test_connector_permissions.py``)
deliberately: these assert the shape the FRONTEND calls (``connectors/permissions.ts``,
``connectors/mcpApi.ts``) rather than the service's own behaviour, and they were written
in a lane running concurrently with that file's owner.

## The defect

``PATCH /api/mcp-clients/{id}/tool-permissions`` was deleted in 8d4afa9d along with the
per-server ``tool_permissions`` map — but BOTH frontend writers kept calling it, so every
level change failed (live: 405) while the grid rendered the new level anyway. A tool the
operator had just BLOCKED stayed at ``ask`` and was still offered to Lucius. The add flow's
step-4 choices were discarded the same way, and the seam additionally mapped the three levels
onto a retired two-value ``allow``/``deny`` vocabulary, which turned "Needs approval" into
``allow`` — an upgrade, in the unsafe direction.

Its own unit test asserted the dead path against a mocked ``authFetch``, so CI agreed with the
bug. ``tests/test_route_path_consistency.py`` now resolves template-literal URLs and catches a
missing PATH; these two cover the other half of the class — the wrong METHOD, and the route
coming back.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.services import tool_permissions as svc


@pytest.fixture()
def client(db_isolated):
    from backend.main import app

    return TestClient(app)


def test_the_retired_mcp_only_permissions_route_is_really_gone(client):
    """REGRESSION GUARD for a fail-open defect that shipped and stayed green.

    ``PATCH /api/mcp-clients/{id}/tool-permissions`` was deleted in 8d4afa9d along with the
    per-server ``tool_permissions`` map, but BOTH frontend writers kept calling it —
    ``connectors/permissions.ts`` (the detail page's ladder) and ``connectors/mcpApi.ts``
    (the add flow's step 4). Every level change failed, the grid rendered the new level
    anyway, and a tool the operator had just BLOCKED stayed at ``ask`` and was still offered
    to Lucius. Its own unit test asserted the dead path, so CI agreed with the bug.

    If this route ever comes back, it means the MCP-only special case came back with it —
    which is the thing the one-ladder-for-every-family design removed.
    """
    res = client.patch(
        "/api/mcp-clients/ibkr/tool-permissions", json={"tool": "x", "level": "blocked"}
    )
    assert res.status_code in (404, 405), (
        "The MCP-only permission route is back. Permissions belong on the family-agnostic "
        "/api/connectors/{id}/permissions routes, addressed as mcp:<server_id>."
    )


def test_permissions_route_wins_over_the_connector_path_route(client):
    """REGRESSION GUARD, pinned by name from ``backend/main.py``'s own router-registration
    comment (search this test's name there). ``api/connectors_api.py`` owns
    ``GET /api/connectors/{connector_id:path}`` and ``:path`` matches slashes, so if the
    routers are ever registered the other way round this request resolves to
    ``get_connector("plaid/permissions")`` — a 404 that takes the whole permission UI
    down. The shape assertion below is what tells the two apart."""
    body = client.get("/api/connectors/plaid/permissions").json()
    assert "default_level" in body and "permissions" in body
    assert "capabilities" not in body  # i.e. NOT a ConnectorView


def test_the_ladder_writes_are_PUT_and_a_PATCH_never_sets_a_level(client):
    """The verb the frontend seam uses, pinned against the live router.

    Path existence is guarded by ``test_route_path_consistency.py``; this covers the other
    half of the same class — a route reached with the wrong METHOD, which is what the
    operator actually hit (a 405, not a 404).

    A PATCH here does NOT 405, and that is worth pinning explicitly: ``connectors_api``'s
    ``PATCH /api/connectors/{connector_id:path}`` matches slashes, so the request lands on
    the CONNECTOR patch route with ``connector_id="mcp:srv/permissions/a_tool"`` and fails
    validation there. It is a different endpoint answering, not the ladder — so the only
    honest assertion is that no level is stored.
    """
    assert (
        client.put(
            "/api/connectors/mcp:srv/permissions/a_tool", json={"level": "blocked"}
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/api/connectors/mcp:srv/permissions",
            json={"tools": ["b_tool"], "level": "always_allow"},
        ).status_code
        == 200
    )

    assert (
        client.patch(
            "/api/connectors/mcp:srv/permissions/c_tool", json={"level": "blocked"}
        ).status_code
        != 200
    )
    assert svc.get_tool_permission("mcp:srv", "c_tool") == "needs_approval"


# ── Enforcement is universal (superseded 2026-08-14) ──────────────────────────
#
# Was: "get_tool_permission has exactly ONE caller in the backend (mcp/client_manager), so
# a level stored against a native connector is recorded and displayed but is not what gates
# that tool." The one-permission-store merge (ADR
# `2026-08-14-one-permission-store-for-every-tool`) closed that gap —
# `ai/tools/_confirm_gate.py::effective_level` is now the ONE dispatch-time read for every
# family, native and MCP alike — so a level set through either route here actually gates
# the tool it names on every surface; end-to-end proof of that lives in
# `tests/test_native_permission_enforcement.py`
# (`test_a_level_set_through_the_connector_route_gates_a_native_tool`), not here.
