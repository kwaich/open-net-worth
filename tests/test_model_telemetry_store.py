"""ModelTelemetryStore CRUD — the store's OWN coverage.

It had none: every assertion about it lived in the routing-engine tests, deleted with
automatic model switching (ADR 2026-08-06-auto-model-switching-removed). The store was
deliberately KEPT (it still records a sample on every model turn), so it needs its own
test rather than inheriting one from a consumer that no longer exists.
"""

from __future__ import annotations

import pytest

from backend.db.model_telemetry_store import _MAX_SAMPLES, ModelTelemetryStore


@pytest.fixture
def store(db_isolated):
    return ModelTelemetryStore()


def test_an_unmeasured_model_has_no_summary(store):
    assert store.summary("never-run") is None
    assert store.all_summaries() == {}


def test_one_sample_round_trips_through_the_summary(store):
    store.record_sample("haiku", ttft_ms=200.0, duration_ms=1000.0, output_tokens=50)

    s = store.summary("haiku")
    assert s["ttft_ms_p50"] == 200.0
    assert s["duration_ms_p50"] == 1000.0
    assert s["tok_per_sec_p50"] == 50.0  # 50 tokens / 1.0s
    assert s["error_rate"] == 0.0
    assert s["errors"] == 0
    assert s["samples"] == 1


def test_a_blank_model_is_ignored(store):
    store.record_sample("", duration_ms=100.0, output_tokens=1)
    store.record_sample("   ", duration_ms=100.0, output_tokens=1)
    assert store.all_summaries() == {}


def test_tok_per_sec_is_omitted_without_both_duration_and_tokens(store):
    store.record_sample("m", ttft_ms=10.0, duration_ms=0, output_tokens=100)
    assert store.summary("m")["tok_per_sec_p50"] is None


def test_errors_join_the_window_but_are_excluded_from_latency_samples(store):
    # D1 (RFC 2026-09-04-ai-model-performance-telemetry §0): the error joins the rolling
    # window `error_rate` is computed over — but `samples` (the latency count) still excludes it.
    store.record_sample("m", ttft_ms=100.0, duration_ms=1000.0, output_tokens=10)
    store.record_sample("m", error=True)

    s = store.summary("m")
    assert s["samples"] == 1  # latency samples only
    assert s["errors"] == 1
    assert s["error_rate"] == 0.5  # 1 error / 2 window entries


def test_a_model_with_only_errors_still_reports(store):
    store.record_sample("m", error=True)
    s = store.summary("m")
    assert s["samples"] == 0
    assert s["errors"] == 1
    assert s["error_rate"] == 1.0
    assert s["ttft_ms_p50"] is None
    assert s["duration_ms_p50"] is None


def test_the_sample_window_is_bounded(store):
    for i in range(_MAX_SAMPLES + 25):
        store.record_sample("m", ttft_ms=float(i), duration_ms=1000.0, output_tokens=10)

    s = store.summary("m")
    assert s["samples"] == _MAX_SAMPLES
    assert s["error_rate"] == 0.0  # total keeps counting every call


def test_errors_fall_out_of_the_window_once_it_fills(store):
    # An error recorded, then pushed out by _MAX_SAMPLES successes, no longer counts toward
    # error_rate — the window is rolling for errors exactly like it is for latency samples.
    store.record_sample("m", error=True)
    for i in range(_MAX_SAMPLES):
        store.record_sample("m", ttft_ms=float(i), duration_ms=1000.0, output_tokens=10)

    s = store.summary("m")
    assert s["samples"] == _MAX_SAMPLES
    assert s["errors"] == 0
    assert s["error_rate"] == 0.0


def test_a_sample_without_duration_ms_degrades_that_percentile_to_none(store):
    # A sample recorded before this change has no `duration_ms` key at all (here: never given
    # one) — `_p50`'s type filter drops it from that percentile only, no crash, no fabricated
    # value (RFC §2 / AC 2). Other fields on the same sample still resolve normally.
    store.record_sample("m", ttft_ms=50.0, output_tokens=10)

    s = store.summary("m")
    assert s["ttft_ms_p50"] == 50.0
    assert s["duration_ms_p50"] is None


def test_models_are_tracked_independently(store):
    store.record_sample("a", ttft_ms=10.0, duration_ms=1000.0, output_tokens=10)
    store.record_sample("b", ttft_ms=90.0, duration_ms=1000.0, output_tokens=20)

    summaries = store.all_summaries()
    assert set(summaries) == {"a", "b"}
    assert summaries["a"]["ttft_ms_p50"] == 10.0
    assert summaries["b"]["tok_per_sec_p50"] == 20.0


def test_it_persists_across_instances(store):
    store.record_sample("m", ttft_ms=5.0, duration_ms=1000.0, output_tokens=10)
    # A fresh instance resolves the SAME db_isolated-redirected engine as `store` — DB-backed
    # persistence (plan 4c task 1), not file re-read.
    assert ModelTelemetryStore().summary("m")["samples"] == 1


# test_a_corrupt_file_degrades_to_empty_rather_than_raising — RETIRED (plan 4c task 1):
# ModelTelemetryStore is DB-backed now (PayloadTableStore), so there is no `_path`/file to
# corrupt and no degrade-on-corrupt-file behavior to pin. The intent this test protected —
# "an unmeasured model reports empty, never raises" — is a DIFFERENT, still-real property,
# already covered by test_an_unmeasured_model_has_no_summary above (no row for that model,
# not a corrupt one).
