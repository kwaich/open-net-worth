"""REST smoke for the first-run completion signal (spec §5.5).

TestClient on a bare app with ONLY setup.router mounted — the surrounding app is irrelevant
to this contract, and mounting backend.main would drag in the whole lifespan.

The regression these pin: `is_fresh_setup()` used to key off `profile.name`, a field the
onboarding flow never wrote, which is why the frontend needed a per-browser localStorage flag
and why first run reappeared on a second device.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import setup as setup_api
from backend.db.onboarding_state_store import OnboardingStateStore


@pytest.fixture
def client(db_isolated):
    app = FastAPI()
    app.include_router(setup_api.router)
    return TestClient(app)


def test_fresh_instance_reports_is_fresh(client):
    body = client.get("/api/setup-status").json()
    assert body["is_fresh"] is True
    assert body["onboarding_completed_at"] is None


def test_complete_stamps_and_flips_is_fresh(client):
    stamp = client.post("/api/setup-status/complete").json()["onboarding_completed_at"]
    assert stamp

    body = client.get("/api/setup-status").json()
    assert body["is_fresh"] is False
    assert body["onboarding_completed_at"] == stamp


def test_complete_is_idempotent_over_http(client):
    first = client.post("/api/setup-status/complete").json()["onboarding_completed_at"]
    second = client.post("/api/setup-status/complete").json()["onboarding_completed_at"]
    assert first == second


def test_restored_book_still_needs_machine_setup_until_completed(client):
    from backend.db.account_store import AccountStore

    AccountStore().create({"name": "Restored QA account", "type": "cash", "currency": "SGD"})
    OnboardingStateStore().mark_restored()
    body = client.get("/api/setup-status").json()
    assert body["is_fresh"] is True
    assert body["restore_pending"] is True
    assert body["onboarding_completed_at"] is None

    client.post("/api/setup-status/complete")
    body = client.get("/api/setup-status").json()
    assert body["is_fresh"] is False
    assert body["restore_pending"] is False


def test_a_legacy_instance_with_accounts_is_not_fresh(client):
    """No stamp (it predates this store) but real data — must NOT re-open first run."""
    from backend.db.account_store import AccountStore

    AccountStore().create({"name": "Meridian Bank Checking", "type": "cash", "currency": "SGD"})

    assert OnboardingStateStore().completed_at() is None
    assert client.get("/api/setup-status").json()["is_fresh"] is False


def test_a_named_profile_alone_no_longer_suppresses_first_run(client, monkeypatch):
    """The retired signal: a profile name used to mean "set up". It must not any more —
    Lucius collects the name AFTER first run completes (spec §5.2), so keying off it would
    end first run before it started."""
    monkeypatch.setattr(
        "backend.services.profile.read_profile", lambda: {"name": "Alex"}, raising=False
    )
    assert client.get("/api/setup-status").json()["is_fresh"] is True


def test_api_and_service_predicates_are_the_same_implementation():
    """They were two independently-written copies that had already drifted — one read
    positions via api.portfolio, the other via positions_service. A freshness bug that
    reproduces on only one surface is the failure mode this pins shut."""
    from backend.api.setup import is_fresh_setup as api_predicate
    from backend.services.setup import is_fresh_setup as service_predicate

    assert api_predicate.__module__ == "backend.api.setup"
    assert service_predicate.__module__ == "backend.services.setup"

    import inspect

    src = inspect.getsource(api_predicate)
    assert "services.setup" in src, "the router must delegate, never re-implement"


def test_service_predicate_honours_the_stamp(db_isolated):
    from backend.services.setup import is_fresh_setup as service_predicate

    assert service_predicate() is True
    OnboardingStateStore().mark_complete()
    assert service_predicate() is False


def test_book_id_is_an_opaque_token_stable_across_reads(client):
    first = client.get("/api/setup-status").json()["book_id"]
    assert len(first) == 32 and int(first, 16) >= 0
    assert client.get("/api/setup-status").json()["book_id"] == first


def test_book_id_survives_completing_setup(client):
    """Finishing first run rewrites the same namespace doc — it must not drop the identity."""
    before = client.get("/api/setup-status").json()["book_id"]
    client.post("/api/setup-status/complete")
    OnboardingStateStore().mark_restored()
    assert client.get("/api/setup-status").json()["book_id"] == before


def test_rotate_book_id_replaces_it_and_keeps_the_stamp(client):
    client.post("/api/setup-status/complete")
    before = client.get("/api/setup-status").json()
    new = OnboardingStateStore().rotate_book_id()
    after = client.get("/api/setup-status").json()
    assert new == after["book_id"] != before["book_id"]
    assert after["onboarding_completed_at"] == before["onboarding_completed_at"]
