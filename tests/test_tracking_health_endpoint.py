from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_tracking_health_shape():
    r = client.get("/api/accounts/tracking-health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "accounts" in body and "straddle_count" in body and "hidden_total" in body
    assert isinstance(body["hidden_total"], int)
    assert isinstance(body["straddle_count"], int)
