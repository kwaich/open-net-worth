"""``services.connectors.display_name_for`` must agree with the names connectors report.

Its caller is ``ai.tools.display``, which runs once per tool call on the streaming hot path, so
it answers from the catalog without building every ``ConnectorView`` (and its permission join)
to read one label. The catalog is the single source of the names both read — this pins that the
cheap path and the full view cannot disagree, which is what the 2026-08-14 drift bug (a catalog
card and a detail page showing different text) was.
"""

from __future__ import annotations

import pytest

from backend.services.connectors import display_name_for, list_connectors


@pytest.fixture(autouse=True)
def _db(db_isolated):
    pass


def test_every_built_in_connector_is_named_the_same_by_both_paths():
    views = list_connectors()
    assert views
    mismatched = {
        c.id: (display_name_for(c.id), c.name) for c in views if display_name_for(c.id) != c.name
    }
    assert not mismatched, f"display_name_for disagrees with the view (label, view): {mismatched}"


def test_an_mcp_server_is_named_by_its_row():
    from backend.db.mcp_client_store import McpClientStore
    from backend.models.mcp_client import McpServerConfig

    McpClientStore().create(McpServerConfig(id="gmail", name="Gmail MCP", url="https://x.test"))
    McpClientStore().create(McpServerConfig(id="plain", url="https://y.test"))
    assert display_name_for("mcp:gmail") == "Gmail MCP"
    assert display_name_for("mcp:plain") == "plain"
    assert display_name_for("mcp:never-added") == "never-added"


def test_unknown_id_degrades_to_something_readable_never_to_a_raw_id():
    assert display_name_for("some_new_thing") == "some new thing"
    assert display_name_for("web_search:newprovider") == "web search • newprovider"
    assert display_name_for("") == ""
