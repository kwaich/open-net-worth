"""A QA run schedules nothing that calls models, outside services or notifications.

Found 2026-10-09: a QA clone of the owner's REAL book (clone_data_for_qa) booted with 13 active
Automations and monitoring on, fired a price alert within seconds, and with a model configured
would have spent the owner's budget on AI scans. ONW_QA_MODE now keeps those jobs off unless
the run opts in with ONW_QA_RUN_SCHEDULES=1; local housekeeping still runs, and no data changes.
"""

from __future__ import annotations

import pytest

import backend.scheduler as scheduler
from tests.test_scheduler_no_ingest_job import fake_scheduler  # noqa: F401

_OUTWARD = {"price_check", "intel_scan", "analysis_sweep", "rss_poll_slow", "refresh_dividend_meta"}


def _qa_mode(monkeypatch, on: bool) -> None:
    import dataclasses

    from backend.core import config

    settings = dataclasses.replace(config.get_settings(), onw_qa_mode=on)
    monkeypatch.setattr(config, "get_settings", lambda: settings)


def _job_ids(fake) -> set[str]:
    return {j["id"] for j in fake.jobs if "id" in j}


@pytest.fixture
def monitoring_on(monkeypatch):
    monkeypatch.setattr(scheduler, "_get_config", lambda: {"enabled": True})


def test_a_qa_run_schedules_no_outward_jobs(fake_scheduler, monitoring_on, monkeypatch):  # noqa: F811
    _qa_mode(monkeypatch, True)
    monkeypatch.delenv("ONW_QA_RUN_SCHEDULES", raising=False)

    scheduler.start()

    ids = _job_ids(fake_scheduler)
    assert not ids & _OUTWARD, ids & _OUTWARD
    assert not any("automation" in i for i in ids), ids
    assert {"reap_batches", "daily_backup", "watchlist_sync"} <= ids, "housekeeping still runs"


def test_a_qa_run_can_opt_back_in(fake_scheduler, monitoring_on, monkeypatch):  # noqa: F811
    _qa_mode(monkeypatch, True)
    monkeypatch.setenv("ONW_QA_RUN_SCHEDULES", "1")

    scheduler.start()

    assert _OUTWARD <= _job_ids(fake_scheduler)


def test_a_normal_run_is_unchanged(fake_scheduler, monitoring_on, monkeypatch):  # noqa: F811
    _qa_mode(monkeypatch, False)
    monkeypatch.delenv("ONW_QA_RUN_SCHEDULES", raising=False)

    scheduler.start()

    ids = _job_ids(fake_scheduler)
    assert _OUTWARD <= ids
    assert any("automation" in i for i in ids)


def test_turning_monitoring_on_in_a_qa_run_still_schedules_nothing_outward(
    fake_scheduler,  # noqa: F811
    monitoring_on,
    monkeypatch,
):
    """The Monitoring panel's toggles call reschedule(); a QA spec flips them."""
    _qa_mode(monkeypatch, True)
    monkeypatch.delenv("ONW_QA_RUN_SCHEDULES", raising=False)
    scheduler.start()
    scheduler.reschedule({"enabled": True, "price_check_interval_seconds": 60})

    assert not _job_ids(fake_scheduler) & _OUTWARD
