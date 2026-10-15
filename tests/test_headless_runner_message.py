"""Regression: the headless/unattended turn must send a `content`-keyed user message
(not `text`), else the provider 400s and every agent/scheduled run fails silently."""

from backend.main import _headless_user_messages


def test_headless_user_message_uses_content_key():
    msgs = _headless_user_messages("run the monitor")
    assert msgs == [{"role": "user", "content": "run the monitor"}]
    assert "text" not in msgs[0]  # the bug was a `text` key → contentless message
