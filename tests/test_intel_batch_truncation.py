"""Regression tests for I10: intel batch synthesis silently producing zero stories.

Root cause: ``INTEL_MAX_TOKENS=2000`` was too small for the
forced ``write_stories`` tool call once the existing-stories block grew large enough
across a many-holding book. Anthropic returned ``stop_reason=max_tokens`` with an EMPTY
tool ``input`` ({}) — the transport serialized that to text ``"{}"``, ``_parse_batch_result``
parsed it to an empty stories list, and NOTHING logged the cause: ``apply_intel_batch_results``
only logged on a nonzero count, so every scan silently no-opped.

This pins:
- ``_parse_batch_result`` treats ``stop_reason="max_tokens"`` (Anthropic) AND
  ``stop_reason="length"`` (OpenAI/openai_compatible, the default provider) as a loud
  ERROR (a lost-output incident), not an empty opinion — provider-agnostic detection.
- Every other zero-story path (bad JSON, missing 'stories' key, empty text, custom_id
  mismatch) logs a WARNING naming the specific cause — no silent path left.
- ``apply_intel_batch_results`` logs the applied count even when it is 0.
- The happy path (valid ``{"stories": [...]}``) is unchanged.
- ``BatchResult``'s new ``stop_reason`` field defaults to None so existing call sites
  that don't pass it stay valid.
- The ``write_stories`` tool schema structurally caps ``stories`` at 12 items (a backstop
  behind the prompt's advisory output-budget line).

All I/O isolated to tmp_path / caplog / db_isolated (IntelStoriesStore is DB-backed).
No LLM, no network.
"""

from __future__ import annotations

import json
import logging

from backend.core.llm_seam import BatchResult
from backend.monitoring.intel_agent import (
    _OUTPUT_SCHEMA,
    _parse_batch_result,
    apply_intel_batch_results,
)

_LOGGER_NAME = "backend.monitoring.intel_agent"
# A story is applied only if it cites a source from the scan's gathered evidence
# (``metadata.url_to_date``); a source-less story is skipped.
_EVIDENCE_URL = "https://example.test/news"
_EVIDENCE = {"url_to_date": {_EVIDENCE_URL: "2026-07-08"}}


def _valid_story_payload() -> str:
    return json.dumps(
        {
            "stories": [
                {
                    "type": "asset",
                    "label": "Acme Corp",
                    "status": "monitoring",
                    "summary": "all good",
                    "new_timeline_event": {
                        "date": "2026-07-08",
                        "title": "check",
                        "summary": "ok",
                        "sources": [{"url": _EVIDENCE_URL, "title": "Acme Corp results"}],
                    },
                }
            ]
        }
    )


def test_batch_result_stop_reason_defaults_to_none():
    """New field is additive — existing constructions (no stop_reason kwarg) stay valid."""
    result = BatchResult(custom_id="c1", text="{}")
    assert result.stop_reason is None


def test_output_tool_schema_caps_stories_at_12():
    """The write_stories tool schema structurally enforces the output budget — not just
    the advisory prompt line — so a model that ignores the prose still can't blow past
    12 stories in one tool call."""
    stories_schema = _OUTPUT_SCHEMA.schema["properties"]["stories"]
    assert stories_schema["maxItems"] == 12


def test_parse_batch_result_truncated_by_max_tokens_logs_error(caplog):
    """A result truncated mid-JSON (empty tool input serialized to '{}') must be treated
    as a lost-output incident: ERROR naming the custom_id + the truncation cause, not a
    silently-accepted empty opinion."""
    results = [BatchResult(custom_id="intel-abc123", text="{}", stop_reason="max_tokens")]

    with caplog.at_level(logging.ERROR, logger=_LOGGER_NAME):
        stories = _parse_batch_result(results, "intel-abc123")

    assert stories == []
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("max_tokens" in r.message and "intel-abc123" in r.message for r in errors), (
        f"expected an ERROR naming custom_id + max_tokens, got: {[r.message for r in errors]}"
    )


def test_parse_batch_result_truncated_by_length_logs_error(caplog):
    """OpenAI's truncation vocabulary is "length", not Anthropic's "max_tokens" — the
    openai_compatible transport (the DEFAULT provider) is a reachable truncation path and
    must be treated identically: a loud ERROR, not a silent empty opinion."""
    results = [BatchResult(custom_id="intel-oai1", text="{}", stop_reason="length")]

    with caplog.at_level(logging.ERROR, logger=_LOGGER_NAME):
        stories = _parse_batch_result(results, "intel-oai1")

    assert stories == []
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("length" in r.message and "intel-oai1" in r.message for r in errors), (
        f"expected an ERROR naming custom_id + length, got: {[r.message for r in errors]}"
    )


def test_parse_batch_result_undecodable_text_logs_warning(caplog):
    """Malformed JSON is a distinct, discriminated cause — logs a WARNING, never silent."""
    results = [BatchResult(custom_id="intel-bad", text="not json{{{")]

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        stories = _parse_batch_result(results, "intel-bad")

    assert stories == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("intel-bad" in r.message for r in warnings), (
        f"expected a WARNING naming the failing custom_id, got: {[r.message for r in warnings]}"
    )


def test_parse_batch_result_missing_stories_key_logs_warning(caplog):
    """Valid JSON that simply lacks the 'stories' key is its own discriminated cause."""
    results = [BatchResult(custom_id="intel-nostories", text=json.dumps({"other": 1}))]

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        stories = _parse_batch_result(results, "intel-nostories")

    assert stories == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("stories" in r.message for r in warnings)


def test_parse_batch_result_happy_path_unchanged():
    """A well-formed result still parses to the stories list — behavior unchanged."""
    results = [BatchResult(custom_id="intel-ok", text=_valid_story_payload())]

    stories = _parse_batch_result(results, "intel-ok")

    assert len(stories) == 1
    assert stories[0]["label"] == "Acme Corp"


def test_apply_intel_batch_results_zero_story_path_logs_count(caplog, db_isolated):
    """Even the 0-applied case must log — this is the exact silent path that hid the
    incident from the reap sweep for days."""
    results = [BatchResult(custom_id="intel-empty", text="{}", stop_reason="max_tokens")]
    ledger_row = {"custom_ids": ["intel-empty"], "metadata": {}}

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        count = apply_intel_batch_results(results, "handle-1", ledger_row)

    assert count == 0
    assert any(
        "0 stories" in r.message and "handle-1" in r.message
        for r in caplog.records
        if r.levelno == logging.INFO
    ), f"expected an INFO log of the 0-applied count, got: {[r.message for r in caplog.records]}"


def test_apply_intel_batch_results_happy_path_unchanged(db_isolated):
    """Nonzero-story apply path still persists + returns the applied count."""
    results = [BatchResult(custom_id="intel-ok2", text=_valid_story_payload())]
    ledger_row = {"custom_ids": ["intel-ok2"], "metadata": _EVIDENCE}

    count = apply_intel_batch_results(results, "handle-2", ledger_row)

    assert count == 1
