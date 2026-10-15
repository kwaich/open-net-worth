"""Regression: a user turn stored with ``attachment_ref`` blocks (every chat upload since the
cross-channel-attachments RFC Phase 2 — see ai/attachments.py) survives a reload. A
photo-only message used to rehydrate with no text and no attachments and was dropped from
the conversation entirely, so a receipt thread opened with Lucius's reply (2026-10-03).
"""

from __future__ import annotations

from backend.ai import assistant


def _ref(att_id: str, filename: str, media_type: str) -> dict:
    return {
        "type": "attachment_ref",
        "attachment_id": att_id,
        "filename": filename,
        "media_type": media_type,
    }


def test_a_photo_only_message_rehydrates_as_a_user_bubble_with_its_thumbnails(monkeypatch):
    history = [
        {
            "role": "user",
            "content": [
                _ref("att_aaaaaaaaaaaa", "IMG_1.jpeg", "image/jpeg"),
                _ref("att_bbbbbbbbbbbb", "IMG_2.jpeg", "image/jpeg"),
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "The receipt shows…"}]},
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda sid: history)

    messages = assistant.rehydrate_messages("sess-1")

    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["text"] == ""
    assert messages[0]["attachments"] == [
        {"ref_id": "att_aaaaaaaaaaaa", "kind": "image", "summary": "IMG_1.jpeg"},
        {"ref_id": "att_bbbbbbbbbbbb", "kind": "image", "summary": "IMG_2.jpeg"},
    ]


def test_a_non_image_ref_rehydrates_as_a_download_pill_beside_the_typed_text(monkeypatch):
    history = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "import this"},
                _ref("att_cccccccccccc", "statement.pdf", "application/pdf"),
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda sid: history)

    messages = assistant.rehydrate_messages("sess-1")

    assert messages[0]["text"] == "import this"
    assert messages[0]["attachments"] == [
        {"ref_id": "att_cccccccccccc", "kind": "text", "summary": "statement.pdf"},
    ]
