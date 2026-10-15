"""
Tests for the plan-state persistence layer: store CRUD, service validation, and
endpoint smoke tests.

Isolated to tmp_path — never touches live backend/data/*.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.db.plan_state_store as store_mod
import backend.services.plan_state as svc_mod
from backend.api.plan_state import router as plan_state_router
from backend.core.errors import NotFoundError, ValidationError
from backend.db.plan_state_store import PlanStateStore

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def store(db_isolated):
    return PlanStateStore()


@pytest.fixture()
def svc(db_isolated, monkeypatch):
    fresh_store = PlanStateStore()
    monkeypatch.setattr(svc_mod, "_store", fresh_store)
    return svc_mod


@pytest.fixture()
def client(db_isolated, monkeypatch):
    fresh_store = PlanStateStore()
    monkeypatch.setattr(svc_mod, "_store", fresh_store)
    app = FastAPI()
    app.include_router(plan_state_router)
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Store CRUD tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPlanStateStore:
    def test_empty_on_first_load(self, store):
        state = store.get_state()
        assert state == {"scenarios": [], "baseline": None}

    def test_add_scenario_assigns_server_id(self, store):
        scen = store.add_scenario({"name": "Retire early", "ret": 7.0, "saving": 3000})
        assert "id" in scen
        assert scen["id"].startswith("scen_")

    def test_list_scenarios_reflects_added(self, store):
        store.add_scenario({"name": "A", "ret": 6.0})
        store.add_scenario({"name": "B", "ret": 8.0})
        scenarios = store.list_scenarios()
        assert len(scenarios) == 2
        assert {s["name"] for s in scenarios} == {"A", "B"}

    def test_persistence_across_store_reload(self, db_isolated):
        s1 = PlanStateStore()
        s1.add_scenario({"name": "Persisted", "saving": 2500})
        # Fresh store instance reads the same (redirected) engine
        s2 = PlanStateStore()
        scenarios = s2.list_scenarios()
        assert len(scenarios) == 1
        assert scenarios[0]["name"] == "Persisted"

    def test_update_scenario(self, store):
        scen = store.add_scenario({"name": "Old name", "ret": 7.0})
        updated = store.update_scenario(scen["id"], {"name": "New name", "ret": 8.0})
        assert updated["name"] == "New name"
        assert updated["id"] == scen["id"]
        # Verify it's in the list
        assert store.list_scenarios()[0]["name"] == "New name"

    def test_update_scenario_returns_none_for_missing(self, store):
        result = store.update_scenario("scen_nonexistent", {"name": "x"})
        assert result is None

    def test_delete_scenario(self, store):
        scen = store.add_scenario({"name": "To delete", "ret": 5.0})
        removed = store.delete_scenario(scen["id"])
        assert removed is True
        assert store.list_scenarios() == []

    def test_delete_scenario_returns_false_for_missing(self, store):
        assert store.delete_scenario("scen_no_such") is False

    def test_set_and_get_baseline(self, store):
        baseline = {"saving": 4000, "ret": 7.5, "lifeExp": 90}
        stored = store.set_baseline(baseline)
        assert stored == baseline
        assert store.get_baseline() == baseline

    def test_clear_baseline_with_none(self, store):
        store.set_baseline({"saving": 3000})
        store.set_baseline(None)
        assert store.get_baseline() is None

    def test_baseline_and_scenarios_coexist(self, store):
        store.add_scenario({"name": "S1", "ret": 6.0})
        store.set_baseline({"saving": 5000})
        state = store.get_state()
        assert len(state["scenarios"]) == 1
        assert state["baseline"]["saving"] == 5000

    def test_scenario_cap_raises_validation_error(self, db_isolated, monkeypatch):
        monkeypatch.setattr(store_mod, "MAX_SCENARIOS", 2)
        s = PlanStateStore()
        s.add_scenario({"name": "A"})
        s.add_scenario({"name": "B"})
        with pytest.raises(ValidationError, match="limit"):
            s.add_scenario({"name": "C"})

    def test_id_is_stable_through_updates(self, store):
        scen = store.add_scenario({"name": "Stable", "ret": 7.0})
        original_id = scen["id"]
        updated = store.update_scenario(
            original_id, {"name": "Renamed", "ret": 8.0, "id": "ignored"}
        )
        assert updated["id"] == original_id


# ─────────────────────────────────────────────────────────────────────────────
# 2. Service unit tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPlanStateService:
    def test_empty_name_rejected(self, svc):
        with pytest.raises(ValidationError, match="name"):
            svc.add_scenario({"name": "", "ret": 7.0})

    def test_missing_name_rejected(self, svc):
        with pytest.raises(ValidationError, match="name"):
            svc.add_scenario({"ret": 7.0})

    def test_add_scenario_happy_path(self, svc):
        scen = svc.add_scenario({"name": "Retire at 50", "ret": 7.0, "saving": 2000})
        assert scen["name"] == "Retire at 50"
        assert scen["id"].startswith("scen_")

    def test_update_scenario_not_found(self, svc):
        svc.add_scenario({"name": "Real", "ret": 7.0})
        with pytest.raises(NotFoundError):
            svc.update_scenario("scen_missing", {"name": "Ghost", "ret": 6.0})

    def test_delete_scenario_not_found(self, svc):
        with pytest.raises(NotFoundError):
            svc.delete_scenario("scen_ghost")

    def test_scenario_cap_enforced(self, svc, monkeypatch):
        monkeypatch.setattr(store_mod, "MAX_SCENARIOS", 2)
        svc.add_scenario({"name": "A", "ret": 6.0})
        svc.add_scenario({"name": "B", "ret": 7.0})
        with pytest.raises(ValidationError):
            svc.add_scenario({"name": "C", "ret": 8.0})

    def test_invalid_ret_percentage(self, svc):
        with pytest.raises(ValidationError, match="ret"):
            svc.add_scenario({"name": "Bad ret", "ret": 150.0})

    def test_invalid_vol_percentage(self, svc):
        with pytest.raises(ValidationError, match="vol"):
            svc.add_scenario({"name": "Bad vol", "vol": -1.0})

    def test_invalid_swr_percentage(self, svc):
        with pytest.raises(ValidationError, match="swr"):
            svc.add_scenario({"name": "Bad swr", "swr": 101.0})

    def test_negative_saving_is_a_real_fact_but_negative_spending_is_not(self, svc):
        assert svc.add_scenario({"name": "Overspender", "saving": -100})["saving"] == -100
        assert svc.set_baseline({"saving": -250})["saving"] == -250
        with pytest.raises(ValidationError, match="monthlyRetirementSpending"):
            svc.add_scenario({"name": "Neg spend", "monthlyRetirementSpending": -1})

    def test_non_finite_saving_rejected(self, svc):
        with pytest.raises(ValidationError, match="saving"):
            svc.add_scenario({"name": "Bad saving", "saving": float("nan")})

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_monthly_retirement_spending_rejected(self, svc, value):
        with pytest.raises(ValidationError, match="monthlyRetirementSpending"):
            svc.add_scenario({"name": "Invalid spending", "monthlyRetirementSpending": value})

    def test_invalid_life_exp(self, svc):
        with pytest.raises(ValidationError, match="lifeExp"):
            svc.add_scenario({"name": "Immortal", "lifeExp": 200})

    def test_invalid_retire_age(self, svc):
        with pytest.raises(ValidationError, match="retireAge"):
            svc.add_scenario({"name": "Time traveller", "retireAge": 0})

    def test_too_many_streams(self, svc):
        streams = [{"label": f"Income {i}", "amount": 1000, "age": 65} for i in range(51)]
        with pytest.raises(ValidationError, match="streams"):
            svc.add_scenario({"name": "Stream overload", "streams": streams})

    def test_too_many_events(self, svc):
        events = [{"id": f"e{i}", "label": "Home", "amount": 10000, "age": 40} for i in range(51)]
        with pytest.raises(ValidationError, match="events"):
            svc.add_scenario({"name": "Event overload", "events": events})

    def test_fractional_event_age_rejected(self, svc):
        events = [{"id": "e1", "label": "Home", "amount": 500000, "age": 33.3}]
        with pytest.raises(ValidationError, match="whole number"):
            svc.add_scenario({"name": "Fractional age", "events": events})

    def test_whole_number_float_event_age_accepted(self, svc):
        """40.0 is a whole number, just spelled as a float — not what this guards against."""
        events = [{"id": "e1", "label": "Home", "amount": 500000, "age": 40.0}]
        scen = svc.add_scenario({"name": "Whole float age", "events": events})
        assert scen["events"][0]["age"] == 40.0

    def test_missing_event_age_rejected(self, svc):
        events = [{"id": "e1", "label": "Home", "amount": 500000}]
        with pytest.raises(ValidationError, match="'age' is required"):
            svc.add_scenario({"name": "No age", "events": events})

    def test_fractional_stream_age_rejected(self, svc):
        streams = [{"label": "CPF LIFE", "amount": 2000, "age": 64.5}]
        with pytest.raises(ValidationError, match="whole number"):
            svc.add_scenario({"name": "Fractional stream age", "streams": streams})

    def test_fractional_stream_end_age_rejected(self, svc):
        streams = [{"label": "Rental", "amount": 1200, "age": 55, "endAge": 74.9}]
        with pytest.raises(ValidationError, match="endAge.*whole number"):
            svc.add_scenario({"name": "Fractional end age", "streams": streams})

    def test_stream_end_age_is_optional(self, svc):
        streams = [{"label": "CPF LIFE", "amount": 2000, "age": 65}]
        scen = svc.add_scenario({"name": "No end age", "streams": streams})
        assert scen["streams"][0].get("endAge") is None

    def test_event_age_out_of_range_rejected(self, svc):
        events = [{"id": "e1", "label": "Home", "amount": 500000, "age": 200}]
        with pytest.raises(ValidationError, match="between 0 and 150"):
            svc.add_scenario({"name": "Too old", "events": events})

    def test_unknown_extra_fields_pass_through(self, svc):
        scen = svc.add_scenario({"name": "With extras", "ret": 7.0, "shockCfg": {"crashAge": 55}})
        assert scen["shockCfg"] == {"crashAge": 55}

    def test_baseline_set_and_clear(self, svc):
        svc.set_baseline({"saving": 3000, "lifeExp": 88})
        assert svc.get_baseline()["saving"] == 3000
        svc.set_baseline(None)
        assert svc.get_baseline() is None

    def test_baseline_lever_validation(self, svc):
        with pytest.raises(ValidationError, match="ret"):
            svc.set_baseline({"saving": 3000, "ret": 999.0})


# ─────────────────────────────────────────────────────────────────────────────
# 3. Endpoint smoke tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPlanStateEndpoints:
    def test_list_scenarios_empty(self, client):
        r = client.get("/api/plan/scenarios")
        assert r.status_code == 200
        assert r.json() == {"scenarios": []}

    def test_create_scenario(self, client):
        r = client.post(
            "/api/plan/scenarios",
            json={"name": "Retire at 55", "ret": 7.0, "saving": 3500},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "Retire at 55"
        assert body["id"].startswith("scen_")

    def test_list_after_create(self, client):
        client.post("/api/plan/scenarios", json={"name": "S1", "ret": 6.0})
        client.post("/api/plan/scenarios", json={"name": "S2", "ret": 8.0})
        r = client.get("/api/plan/scenarios")
        assert r.status_code == 200
        assert len(r.json()["scenarios"]) == 2

    def test_update_scenario(self, client):
        r = client.post("/api/plan/scenarios", json={"name": "Original", "ret": 7.0})
        scen_id = r.json()["id"]
        r2 = client.put(f"/api/plan/scenarios/{scen_id}", json={"name": "Updated", "ret": 8.0})
        assert r2.status_code == 200
        assert r2.json()["name"] == "Updated"
        assert r2.json()["id"] == scen_id

    def test_update_missing_scenario_404(self, client):
        r = client.put("/api/plan/scenarios/scen_ghost", json={"name": "Ghost", "ret": 7.0})
        assert r.status_code == 404

    def test_delete_scenario(self, client):
        r = client.post("/api/plan/scenarios", json={"name": "Delete me", "ret": 5.0})
        scen_id = r.json()["id"]
        r2 = client.delete(f"/api/plan/scenarios/{scen_id}")
        assert r2.status_code == 204
        assert client.get("/api/plan/scenarios").json()["scenarios"] == []

    def test_delete_missing_scenario_404(self, client):
        r = client.delete("/api/plan/scenarios/scen_gone")
        assert r.status_code == 404

    def test_create_scenario_invalid_name_422(self, client):
        r = client.post("/api/plan/scenarios", json={"name": "", "ret": 7.0})
        assert r.status_code == 422

    def test_create_scenario_invalid_lever_422(self, client):
        r = client.post("/api/plan/scenarios", json={"name": "Bad", "ret": 200.0})
        assert r.status_code == 422

    def test_get_baseline_null(self, client):
        r = client.get("/api/plan/baseline")
        assert r.status_code == 200
        assert r.json() == {"baseline": None}

    def test_set_baseline(self, client):
        r = client.put("/api/plan/baseline", json={"saving": 4000, "lifeExp": 90})
        assert r.status_code == 200
        assert r.json()["baseline"]["saving"] == 4000

    def test_clear_baseline_with_null(self, client):
        client.put("/api/plan/baseline", json={"saving": 3000})
        r = client.put("/api/plan/baseline", json=None)
        assert r.status_code == 200
        assert r.json()["baseline"] is None

    def test_clear_baseline_with_empty_dict(self, client):
        client.put("/api/plan/baseline", json={"saving": 3000})
        r = client.put("/api/plan/baseline", json={})
        assert r.status_code == 200
        assert r.json()["baseline"] is None

    def test_full_scenario_roundtrip(self, client):
        """A scenario with all expected fields survives a create→list→get cycle."""
        payload = {
            "name": "Full scenario",
            "successPct": 87,
            "odds": 87,
            "saving": 3000,
            "ret": 7.0,
            "vol": 12.0,
            "monthlyRetirementSpending": 5000,
            "swr": 4.0,
            "lifeExp": 90,
            "retireAge": 55,
            "atNumber": False,
            "streams": [{"label": "CPF", "amount": 1500, "age": 65}],
            "events": [{"id": "e1", "label": "Home", "amount": 150000, "age": 40}],
            "crash": False,
            "weakSeq": False,
            "lostDec": False,
            "highInfl": False,
            "shockCfg": {},
        }
        r = client.post("/api/plan/scenarios", json=payload)
        assert r.status_code == 201
        created = r.json()
        assert created["streams"] == payload["streams"]
        assert created["shockCfg"] == {}
        # Verify it appears in the list
        list_r = client.get("/api/plan/scenarios")
        assert len(list_r.json()["scenarios"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Item-field contract at the write choke — off-contract keys REFUSED, never coerced
# ─────────────────────────────────────────────────────────────────────────────


class TestItemFieldValidation:
    def test_snake_case_end_age_refused_with_corrective_hint(self, svc):
        with pytest.raises(ValidationError) as exc:
            svc.add_scenario(
                {
                    "name": "ai scenario",
                    "streams": [{"label": "Pension", "amount": 3500, "age": 55, "end_age": 75}],
                }
            )
        assert "end_age" in str(exc.value)
        assert "endAge" in str(exc.value)

    def test_update_and_baseline_refuse_off_contract_items(self, svc):
        created = svc.add_scenario({"name": "s"})
        with pytest.raises(ValidationError):
            svc.update_scenario(
                created["id"],
                {"name": "s", "streams": [{"label": "x", "amount": 1, "age": 60, "end_age": 70}]},
            )
        with pytest.raises(ValidationError):
            svc.set_baseline({"events": [{"label": "y", "amount": 2, "age": 50, "note": "hi"}]})

    def test_on_contract_items_pass_through(self, svc):
        created = svc.add_scenario(
            {
                "name": "clean",
                "streams": [{"label": "CPF", "amount": 1500, "age": 65, "endAge": 80}],
                "events": [{"id": "e1", "label": "Home", "amount": 150000, "age": 40}],
            }
        )
        assert created["streams"][0]["endAge"] == 80
        assert created["events"][0]["id"] == "e1"


# ─────────────────────────────────────────────────────────────────────────────
# Life-event identity (2026-07-18 incident: two chat-created, id-less events
# deleted and dragged as a pair because the UI keys both on `event.id`)
# ─────────────────────────────────────────────────────────────────────────────


def test_baseline_events_get_distinct_server_minted_ids(store):
    """An event persisted without an id is stamped — and never shares one."""
    stored = store.set_baseline(
        {
            "events": [
                {"label": "🏠 Home", "amount": -500000, "age": 30},
                {"label": "🚗 Car", "amount": -200000, "age": 32},
            ]
        }
    )
    ids = [ev["id"] for ev in stored["events"]]
    assert all(ids), "every persisted life event must carry an id"
    assert len(set(ids)) == 2, "life events must not share an id"
    assert store.get_baseline()["events"][1]["id"] == ids[1]


def test_caller_supplied_event_id_is_preserved(store):
    stored = store.set_baseline(
        {"events": [{"id": "e-keep", "label": "X", "amount": -1, "age": 40}]}
    )
    assert stored["events"][0]["id"] == "e-keep"


def test_scenario_events_get_ids(store):
    scen = store.add_scenario({"name": "s", "events": [{"label": "X", "amount": -1, "age": 40}]})
    assert scen["events"][0]["id"]
    updated = store.update_scenario(
        scen["id"], {"name": "s", "events": [{"label": "Y", "amount": -2, "age": 41}]}
    )
    assert updated["events"][0]["id"]
