"""Regression: an external-MCP tool's captured image (a screenshot, e.g.) survives a page
reload — rehydrated by PROVENANCE (services.attachments.attachments_for_message), never by
parsing anything out of the tool_result's own untrusted text. Mirrors
test_rehydrate_search_block.py's shape for web_search, and the live path this reload must
match: ai.assistant._post_execute_events's "mcp_image" event / _attachment_refs.
"""

from __future__ import annotations

import pytest

from backend.ai import assistant


@pytest.fixture(autouse=True)
def _isolated_attachments(tmp_path, monkeypatch, db_isolated):
    import backend.db.attachment_store as as_mod
    from backend.services import attachments as attachments_svc

    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    monkeypatch.setattr(as_mod, "ATTACHMENTS_DIR", att_dir)
    attachments_svc._cache_drop()
    yield
    attachments_svc._cache_drop()


def _history(tool_name: str = "mcp__playwright__browser_take_screenshot") -> list[dict]:
    return [
        {"role": "user", "content": "take a screenshot of vnexpress.vn"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu-1",
                    "name": tool_name,
                    "input": {},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu-1",
                    "content": "[EXTERNAL DATA from MCP server 'playwright' — UNVERIFIED...] «external:abc»done«/external:abc»",
                }
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Here's the screenshot."}]},
    ]


def test_a_captured_screenshot_rehydrates_as_an_attachment_on_the_owning_message(monkeypatch):
    from backend.services.attachments import create_attachment

    entry = {"kind": "image", "media_type": "image/png", "summary": "Image from playwright"}
    ref_id = create_attachment(
        entry,
        b"\x89PNG\r\n",
        filename="shot.png",
        media_type="image/png",
        source_channel="mcp:playwright",
        source_message_id="tu-1",
        source_conversation_id="sess-1",
    )
    monkeypatch.setattr(assistant, "_load_session", lambda sid: _history())

    messages = assistant.rehydrate_messages("sess-1")

    asst = [m for m in messages if m["role"] == "assistant"]
    # One bubble: the tool_result-only user entry keeps the turn open, so the trailing
    # "Here's the screenshot" text merges into the SAME assistant message as the tool_use.
    assert len(asst) == 1
    assert asst[0]["attachments"] == [
        {"ref_id": ref_id, "kind": "image", "summary": "Image from playwright"}
    ]


def test_a_native_tool_result_never_triggers_an_attachment_lookup(monkeypatch):
    """No accidental match for a NATIVE tool — mcp_external.is_external gates the lookup so a
    non-mcp__ tool_result never even queries attachments_for_message."""
    history = [
        {"role": "user", "content": "list my accounts"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu-1", "name": "list_accounts", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "[]"}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "No accounts yet."}]},
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda sid: history)

    messages = assistant.rehydrate_messages("sess-2")

    assert not any(m.get("attachments") for m in messages)


def test_an_mcp_call_with_no_captured_image_rehydrates_with_no_attachments(monkeypatch):
    monkeypatch.setattr(assistant, "_load_session", lambda sid: _history())

    messages = assistant.rehydrate_messages("sess-empty")

    assert not any(m.get("attachments") for m in messages)
