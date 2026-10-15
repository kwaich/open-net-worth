"""Regression tests for I11: intel batch apply crashing on a double-encoded stories field.

Root cause (live evidence, a scheduled batch run, log
``scheduler: _apply_batch_results intel failed``): the model
double-encoded its forced ``write_stories`` tool input — instead of
``{"stories": [ {...}, ... ]}`` it emitted ``{"stories": "<a JSON STRING containing the
whole {\"stories\":[...]} object>"}``. ``_parse_batch_result`` parsed the outer text to a
dict, ``"stories" in parsed`` was True, and ``parsed.get("stories", [])`` returned that
STRING. The caller then iterated the string character-by-character and ``_persist_story``
crashed with ``AttributeError: 'str' object has no attribute 'get'`` on the first
character. The ledger row stayed ``pending`` so the reap sweep retried (and re-crashed)
every 5 minutes.

This pins:
- The exact double-encoded shape observed in the live batch unwraps to its stories list,
  with an INFO log marking the recovery (so recurrence is visible).
- A JSON-string of a bare list also unwraps.
- An undecodable string logs a WARNING and yields [] — never a crash.
- A stories list mixing dicts and non-dict items keeps the dicts and logs ONE aggregate
  WARNING naming the count and the first offender — a single malformed item can never
  abort the whole batch.
- ``apply_intel_batch_results`` end-to-end against a tmp store: the double-encoded shape
  persists its stories without raising, logging the applied count.

All I/O isolated to tmp_path / caplog / db_isolated (IntelStoriesStore is DB-backed).
No LLM, no network.
"""

from __future__ import annotations

import json
import logging

from backend.core.llm_seam import BatchResult
from backend.monitoring.intel_agent import _parse_batch_result, apply_intel_batch_results

_LOGGER_NAME = "backend.monitoring.intel_agent"


# A story is applied only if it cites a source from the scan's gathered evidence
# (``metadata.url_to_date``); a source-less story is skipped.
_EVIDENCE_URL = "https://example.test/news"
_EVIDENCE = {"url_to_date": {_EVIDENCE_URL: "2026-07-08"}}


def _story(label: str, ticker: str = "") -> dict:
    return {
        "type": "asset",
        "label": label,
        "ticker": ticker,
        "status": "monitoring",
        "status_reason": "routine check",
        "summary": f"{label} — nothing new this scan.",
        "portfolio_impact": "",
        "action_items": [],
        "new_timeline_event": {
            "date": "2026-07-08",
            "title": "Routine check — no new developments",
            "summary": "No change.",
            "sources": [{"url": _EVIDENCE_URL, "title": f"{label} reports"}],
        },
    }


def test_parse_batch_result_unwraps_live_double_encoded_shape(caplog):
    """The exact double-encoding observed live: the outer 'stories' value is itself a
    JSON string of the whole {"stories": [...]} object."""
    stories_payload = [_story("Acme Corp", "ACME"), _story("Globex Inc", "GBX")]
    inner_text = json.dumps({"stories": stories_payload})
    outer_text = json.dumps({"stories": inner_text})
    results = [BatchResult(custom_id="intel-dbl1", text=outer_text)]

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        stories = _parse_batch_result(results, "intel-dbl1")

    assert stories == stories_payload
    assert all(isinstance(s, dict) for s in stories)
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any(
        "unwrapped double-encoded" in r.message and "2 stories" in r.message for r in infos
    ), f"expected an INFO log of the unwrap + count, got: {[r.message for r in infos]}"


def test_parse_batch_result_unwraps_json_string_of_bare_list():
    """The 'stories' value can also be a JSON-string of a bare list (no wrapping dict)."""
    stories_payload = [_story("Acme Corp")]
    outer_text = json.dumps({"stories": json.dumps(stories_payload)})
    results = [BatchResult(custom_id="intel-dbl2", text=outer_text)]

    stories = _parse_batch_result(results, "intel-dbl2")

    assert stories == stories_payload


def test_parse_batch_result_undecodable_double_encoded_string_logs_warning(caplog):
    """A stories string that isn't valid JSON at all can't be unwrapped — WARNING + []."""
    outer_text = json.dumps({"stories": "not json{{{"})
    results = [BatchResult(custom_id="intel-dbl3", text=outer_text)]

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        stories = _parse_batch_result(results, "intel-dbl3")

    assert stories == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "intel-dbl3" in r.message and "could not be unwrapped" in r.message for r in warnings
    ), f"expected a WARNING naming the unwrap failure, got: {[r.message for r in warnings]}"


def test_parse_batch_result_mixed_dict_and_non_dict_items_keeps_dicts(caplog):
    """A malformed item alongside real stories must be skipped, not abort the batch."""
    good = _story("Acme Corp")
    outer_text = json.dumps({"stories": [good, "not a story", 42]})
    results = [BatchResult(custom_id="intel-mix1", text=outer_text)]

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        stories = _parse_batch_result(results, "intel-mix1")

    assert stories == [good]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("skipped 2 non-dict" in r.message and "intel-mix1" in r.message for r in warnings), (
        f"expected ONE aggregate WARNING naming the count, got: {[r.message for r in warnings]}"
    )


def test_apply_intel_batch_results_persists_double_encoded_stories(caplog, db_isolated):
    """End-to-end reap path: the live double-encoded shape must persist without raising."""
    stories_payload = [_story("Acme Corp", "ACME"), _story("Globex Inc", "GBX")]
    inner_text = json.dumps({"stories": stories_payload})
    outer_text = json.dumps({"stories": inner_text})
    results = [BatchResult(custom_id="intel-dbl-e2e", text=outer_text)]
    ledger_row = {"custom_ids": ["intel-dbl-e2e"], "metadata": _EVIDENCE}

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        count = apply_intel_batch_results(results, "handle-dbl-e2e", ledger_row)

        from backend.db.intel_stories_store import IntelStoriesStore

        persisted = IntelStoriesStore().all()

    assert count == 2
    assert {s["label"] for s in persisted} == {"Acme Corp", "Globex Inc"}
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any(
        "reap applied 2 stories" in r.message and "handle-dbl-e2e" in r.message for r in infos
    ), f"expected an INFO log of the applied count, got: {[r.message for r in infos]}"
