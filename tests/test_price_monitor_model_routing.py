"""price_monitor alert narration is routed through the per-task config seam.

It used to be pinned two ways at once: a hardcoded ``MODEL = "claude-sonnet-4-6"``
AND a directly-constructed Anthropic client, so the task was invisible to
``task_models`` and could not be re-pointed at a cheaper model or a different
provider no matter what the operator configured. The alert's FIRING is decided by
a deterministic threshold comparison; the model only narrates it.
"""

from __future__ import annotations

import pytest

from backend.db.model_config_store import VALID_TASKS
from backend.monitoring import price_monitor


def test_price_monitor_is_a_configurable_task():
    assert "price_monitor" in VALID_TASKS


class TestAlertModelResolution:
    def test_defaults_to_the_instance_model(self, monkeypatch):
        """Empty string = "use whatever the instance is configured with" —
        the same convention news_screen._screen_model uses."""
        import backend.db.model_config_store as mcs

        monkeypatch.setattr(mcs.ModelConfigStore, "task_model_override", lambda self, task: None)
        assert price_monitor._alert_model() == ""

    def test_honours_a_task_override(self, monkeypatch):
        import backend.db.model_config_store as mcs

        seen: list[str] = []

        def _override(self, task):
            seen.append(task)
            return "openrouter:x-ai/grok-4.3"

        monkeypatch.setattr(mcs.ModelConfigStore, "task_model_override", _override)
        assert price_monitor._alert_model() == "openrouter:x-ai/grok-4.3"
        assert seen == ["price_monitor"]

    def test_unreadable_config_degrades_to_the_instance_default(self, monkeypatch):
        import backend.db.model_config_store as mcs

        def _boom(self, task):
            raise OSError("config unreadable")

        monkeypatch.setattr(mcs.ModelConfigStore, "task_model_override", _boom)
        assert price_monitor._alert_model() == ""

    def test_no_hardcoded_model_constant_remains(self):
        assert not hasattr(price_monitor, "MODEL")


class TestAlertNarrationGoesThroughTheSeam:
    @pytest.mark.asyncio
    async def test_uses_the_llm_seam_and_tags_the_cost_source(self, monkeypatch):
        calls: list[dict] = []

        async def _fake_complete(prompt, *, system="", model="", max_tokens=2048, source="", **kw):
            calls.append({"model": model, "max_tokens": max_tokens, "source": source})
            return '{"summary": "s", "portfolio_impact": "p", "action_items": ["a"]}'

        monkeypatch.setattr("backend.core.llm_seam.complete", _fake_complete)
        monkeypatch.setattr(price_monitor, "_alert_model", lambda: "test-model")

        signal = await price_monitor._ai_signal(
            signal_id="sig1",
            label="ACME",
            ticker="ACME",
            op=">=",
            target=100.0,
            current_price=101.0,
            position_ctx="",
            user_note="",
            profile={},
            now="2026-07-19T00:00:00Z",
        )

        assert len(calls) == 1
        assert calls[0]["model"] == "test-model"
        # The seam's provider records cost itself; the source tag must survive so
        # the spend still lands under price_monitor rather than a generic bucket.
        assert calls[0]["source"] == "price_monitor"
        # Owner-reproduced live bug, 2026-08-31: the old 600-token budget left NOTHING for the
        # actual answer once the model's own (un-disableable, despite effort="none") reasoning
        # ate the whole thing — every real alert silently fell back to its bare price line with
        # no summary/impact/actions. See _ALERT_NARRATION_MAX_TOKENS's own comment.
        assert calls[0]["max_tokens"] == price_monitor._ALERT_NARRATION_MAX_TOKENS
        assert price_monitor._ALERT_NARRATION_MAX_TOKENS > 600
        assert signal["id"] == "sig1"
        assert signal["type"] == "price_alert"
        assert "risen above" in signal["current_summary"]

    @pytest.mark.asyncio
    async def test_cost_is_recorded_once_not_twice(self, monkeypatch):
        """The old code called cost_tracker.record by hand; the seam's provider
        already records. Doing both would double-count every alert."""
        recorded: list[tuple] = []

        async def _fake_complete(prompt, *, system="", model="", max_tokens=2048, source="", **kw):
            return '{"summary": "s", "portfolio_impact": "p", "action_items": []}'

        monkeypatch.setattr("backend.core.llm_seam.complete", _fake_complete)
        monkeypatch.setattr("backend.ai.cost_tracker.record", lambda *a, **k: recorded.append(a))

        await price_monitor._ai_signal(
            signal_id="s",
            label="L",
            ticker="T",
            op="<=",
            target=1.0,
            current_price=0.5,
            position_ctx="",
            user_note="",
            profile={},
            now="2026-07-19T00:00:00Z",
        )
        assert recorded == []

    @pytest.mark.asyncio
    async def test_fenced_json_is_still_unwrapped(self, monkeypatch):
        """Behaviour preserved across the transport change."""

        async def _fake_complete(prompt, *, system="", model="", max_tokens=2048, source="", **kw):
            return '```json\n{"summary": "fenced", "portfolio_impact": "", "action_items": []}\n```'

        # Pin the extractor for the duration of this test instead of trusting the global
        # one the `ai` layer registers at import. `llm_seam._parse_json` is module-global
        # MUTABLE state that a dozen other test modules legitimately monkeypatch (to
        # `json.loads`, or to None, to exercise the degraded path) — so whether this test
        # saw the real fence-stripping extractor depended on suite ordering, not on the
        # behaviour under test. Registering it here makes the assertion mean what it says.
        from backend.ai.memory import _parse_json as _real_parse_json

        monkeypatch.setattr("backend.core.llm_seam._parse_json", _real_parse_json)
        monkeypatch.setattr("backend.core.llm_seam.complete", _fake_complete)
        signal = await price_monitor._ai_signal(
            signal_id="s",
            label="L",
            ticker="T",
            op=">=",
            target=1.0,
            current_price=2.0,
            position_ctx="",
            user_note="",
            profile={},
            now="2026-07-19T00:00:00Z",
        )
        assert "fenced" in signal["current_summary"]
