"""Problem-report API: the draft is redacted, and Send posts exactly the previewed payload."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import problem_reports as problem_reports_api
from backend.services import problem_report as service

LOG = [
    "2026-10-04T10:00:00 INFO     backend.services.ledger created $1,250.00 at Acme Hardware",
    "2026-10-04T10:00:01 ERROR    backend.api.import failed for /Users/alice/Statements/chase.pdf",
    "Traceback (most recent call last):",
    "ValueError: bad row for jane@example.com",
]


@pytest.fixture(autouse=True)
def _book(monkeypatch):
    monkeypatch.setattr(service, "_names", lambda: (["Chase Checking"], ["Acme Hardware"]))
    monkeypatch.setattr(service, "tail_log", lambda n=200, **_kw: list(LOG))
    monkeypatch.setattr(service, "_known_paths", lambda: [("/Users/alice", "~")])


def _client(host: str = "testclient") -> TestClient:
    app = FastAPI()
    app.include_router(problem_reports_api.router)
    return TestClient(app, client=(host, 50000))


class _Receiver:
    """A stand-in for the portal that records what it was sent."""

    def __init__(self, status: int = 201):
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append(
                    {"path": self.path, "body": body, "type": self.headers["Content-Type"]}
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"id": "rep_123"}')

            def log_message(self, *_args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/problem-reports"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def receiver(monkeypatch):
    made: list[_Receiver] = []

    def make(status: int = 201) -> _Receiver:
        made.append(_Receiver(status))
        monkeypatch.setenv("ONW_REPORTS_URL", made[-1].url)
        return made[-1]

    yield make
    for item in made:
        item.close()


def test_draft_redacts_logs_error_and_screen():
    body = {
        "error": "failed for jane@example.com",
        "screen": "/accounts/Chase Checking",
        "include_logs": True,
    }
    draft = _client().post("/api/problem-reports/draft", json=body).json()
    assert draft["description"] == "" and draft["contact_email"] is None
    assert draft["error"] == "failed for [email]"
    assert draft["screen"] == "/accounts/[account]"
    assert "1,250" not in draft["logs"] and "Acme Hardware" not in draft["logs"]
    assert "alice" not in draft["logs"] and "jane@example.com" not in draft["logs"]
    assert draft["logs"].splitlines()[0].endswith("created [amount] at [payee]")
    assert draft["app_version"] and draft["platform"]


def test_draft_without_logs_reads_no_log_file(monkeypatch):
    monkeypatch.setattr(service, "tail_log", lambda *a, **k: pytest.fail("log file was read"))
    draft = _client().post("/api/problem-reports/draft", json={"include_logs": False}).json()
    assert draft["logs"] is None


def test_mode_is_web_on_this_machine_and_server_from_another():
    assert _client().post("/api/problem-reports/draft", json={}).json()["mode"] == "web"
    assert (
        _client("203.0.113.9").post("/api/problem-reports/draft", json={}).json()["mode"]
        == "server"
    )


def test_platform_names_the_browser():
    ua = {
        "user-agent": "Mozilla/5.0 (X11) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
    }
    assert (
        "Edge 130"
        in _client().post("/api/problem-reports/draft", json={}, headers=ua).json()["platform"]
    )


def test_the_preview_is_exactly_what_is_sent(receiver):
    sink = receiver()
    client = _client()
    draft = client.post("/api/problem-reports/draft", json={"error": "boom"}).json()
    draft["description"] = "Import froze on the second page"
    draft["contact_email"] = "me@example.com"
    draft["logs"] = draft["logs"] + "\n(edited by the user)"
    response = client.post("/api/problem-reports/send", json=draft)
    assert response.status_code == 200 and response.json() == {"sent": True, "id": "rep_123"}
    [seen] = sink.requests
    assert seen["path"] == "/api/problem-reports" and seen["type"] == "application/json"
    assert json.loads(seen["body"]) == draft


def test_a_blank_description_or_unknown_field_is_refused_before_sending(receiver):
    sink = receiver()
    draft = _client().post("/api/problem-reports/draft", json={}).json()
    assert (
        _client().post("/api/problem-reports/send", json={**draft, "description": "  "}).status_code
        == 422
    )
    assert (
        _client()
        .post("/api/problem-reports/send", json={**draft, "description": "x", "book": "no"})
        .status_code
        == 422
    )
    assert (
        _client()
        .post(
            "/api/problem-reports/send", json={**draft, "description": "x", "contact_email": "nope"}
        )
        .status_code
        == 422
    )
    assert sink.requests == []


@pytest.mark.parametrize("status", [429, 413, 500])
def test_a_refusal_comes_back_as_a_message_not_a_crash(receiver, status):
    receiver(status)
    draft = {**_client().post("/api/problem-reports/draft", json={}).json(), "description": "x"}
    response = _client().post("/api/problem-reports/send", json=draft)
    assert response.status_code == 502 and "report" in response.json()["detail"].lower()


def test_an_unreachable_server_is_a_502(monkeypatch):
    monkeypatch.setenv("ONW_REPORTS_URL", "http://127.0.0.1:9/api/problem-reports")
    draft = {**_client().post("/api/problem-reports/draft", json={}).json(), "description": "x"}
    response = _client().post("/api/problem-reports/send", json=draft)
    assert response.status_code == 502 and "reach" in response.json()["detail"]


def test_a_plain_http_remote_endpoint_is_never_used(monkeypatch):
    monkeypatch.setenv("ONW_REPORTS_URL", "http://reports.example.com/api/problem-reports")
    draft = {**_client().post("/api/problem-reports/draft", json={}).json(), "description": "x"}
    assert _client().post("/api/problem-reports/send", json=draft).status_code == 500


def test_the_desktop_shell_can_ask_for_the_names_to_mask():
    body = _client().get("/api/problem-reports/redaction-terms").json()
    assert body == {"accounts": ["Chase Checking"], "payees": ["Acme Hardware"]}
