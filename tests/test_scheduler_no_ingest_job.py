"""AC1 (RFC connectors-as-data-sources): booting the scheduler registers NO hidden
connector-ingest job. The retired ``_INGEST_SOURCES``/``_schedule_source_job``/
``reschedule_<provider>`` machinery used to register one job per enabled provider
(``ingest:<provider>``) on a poll interval; there is no scheduled poll any more — a
connector's own rows are only ever seen through a Lucius turn calling its fetch tool.

Fakes ``AsyncIOScheduler`` itself (not just the module-level ``_scheduler`` variable,
which ``scheduler.start()`` unconditionally reassigns) so the real APScheduler never
actually starts a background thread in a test process.
"""

from __future__ import annotations

import pytest

import backend.scheduler as scheduler
from backend.services.aggregation.providers import PROVIDER_KEYS


class _FakeAPScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict] = []
        self.running = False

    def add_job(self, fn, **kwargs):
        self.jobs.append({"fn": fn, **kwargs})

    def start(self) -> None:
        self.running = True

    def shutdown(self, wait: bool = False) -> None:
        self.running = False

    def get_job(self, job_id):
        return next((j for j in self.jobs if j.get("id") == job_id), None)


@pytest.fixture
def fake_scheduler(db_isolated, monkeypatch):
    fake = _FakeAPScheduler()
    monkeypatch.setattr(scheduler, "AsyncIOScheduler", lambda: fake)
    original = scheduler._scheduler
    try:
        yield fake
    finally:
        # start() reassigns the module-level _scheduler global to whatever
        # AsyncIOScheduler() returns — restore it explicitly so this test can never leak
        # a fake scheduler into a later test that reads the real one.
        scheduler._scheduler = original


def test_boot_registers_no_ingest_job(fake_scheduler):
    scheduler.start()

    job_ids = {j["id"] for j in fake_scheduler.jobs if "id" in j}
    assert not any(job_id.startswith("ingest") for job_id in job_ids), job_ids
    # Belt and braces: no job id names a provider key either (an ingest job's id was
    # `ingest:<provider>`, e.g. `ingest:plaid`) — catches a differently-named revival too.
    for provider_key in PROVIDER_KEYS:
        assert not any(provider_key in job_id for job_id in job_ids), (provider_key, job_ids)


def test_scheduler_module_has_no_leftover_ingest_reschedule_functions():
    """The reschedule_<provider> shims (called from connectors.py whenever a config
    changed) are retired along with the poll — asserted against the module namespace so a
    partial revival (the function exists but is simply never called) still fails this."""
    for provider_key in PROVIDER_KEYS:
        assert not hasattr(scheduler, f"reschedule_{provider_key}")
    assert not hasattr(scheduler, "reschedule_source")
    assert not hasattr(scheduler, "_schedule_source_job")
    assert not hasattr(scheduler, "_INGEST_SOURCES")
