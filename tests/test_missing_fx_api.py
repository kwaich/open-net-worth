"""Missing FX must be actionable, never a fabricated number or opaque 500."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.fx_errors import missing_fx_response
from backend.core.errors import MissingFxRateError
from backend.core.fx import to_base


def test_missing_fx_response_has_pair_and_retry_guidance():
    app = FastAPI()
    app.add_exception_handler(MissingFxRateError, missing_fx_response)

    @app.get("/valuation")
    def valuation():
        return {"value": to_base(25_000_000, "VND", "SGD", rates={"SGD": 1})}

    response = TestClient(app).get("/valuation")
    assert response.status_code == 503
    assert response.json()["code"] == "missing_fx_rate"
    assert response.json()["currency"] == "VND"
    assert response.json()["target"] == "SGD"
    assert "retry" in response.json()["detail"]
    assert "value" not in response.json()
