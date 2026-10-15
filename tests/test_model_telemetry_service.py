"""backend/services/model_telemetry.py — read-only projection over ModelTelemetryStore.

Uses a `store=None` injection seam (`news_latency.py`'s shape) so these tests never touch
the real DB-backed store — a fake object with the same `all_summaries()` contract stands in.
"""

from __future__ import annotations

from backend.services.model_telemetry import model_telemetry


class _FakeStore:
    def __init__(self, summaries: dict, *, raises: bool = False):
        self._summaries = summaries
        self._raises = raises

    def all_summaries(self) -> dict:
        if self._raises:
            raise RuntimeError("db unreachable")
        return self._summaries


def _summary(**overrides) -> dict:
    base = {
        "ttft_ms_p50": 100.0,
        "duration_ms_p50": 900.0,
        "tok_per_sec_p50": 20.0,
        "error_rate": 0.0,
        "errors": 0,
        "samples": 5,
    }
    base.update(overrides)
    return base


def test_known_model_id_gets_a_provider_label():
    rows = model_telemetry(store=_FakeStore({"sonnet": _summary()}))
    assert rows == [{"model": "sonnet", "provider": "Claude Code CLI", **_summary()}]


def test_unknown_model_id_gets_no_provider_label_not_the_string_unknown():
    rows = model_telemetry(store=_FakeStore({"some-local-override": _summary()}))
    assert rows[0]["model"] == "some-local-override"
    assert rows[0]["provider"] is None


def test_a_none_summary_is_dropped_not_shown_as_a_row():
    store = _FakeStore({"sonnet": _summary(), "never-measured": None})
    rows = model_telemetry(store=store)
    assert [r["model"] for r in rows] == ["sonnet"]


def test_ordering_is_samples_descending_then_model_ascending():
    store = _FakeStore(
        {
            "b-model": _summary(samples=10),
            "a-model": _summary(samples=10),
            "c-model": _summary(samples=30),
        }
    )
    rows = model_telemetry(store=store)
    assert [r["model"] for r in rows] == ["c-model", "a-model", "b-model"]


def test_an_empty_store_returns_an_empty_list():
    assert model_telemetry(store=_FakeStore({})) == []


def test_a_raising_store_degrades_to_an_empty_list_not_an_exception():
    assert model_telemetry(store=_FakeStore({}, raises=True)) == []
