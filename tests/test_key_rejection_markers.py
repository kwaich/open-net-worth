"""A provider that rejects a key without saying 401.

Google answers a bogus Gemini key with HTTP 400 and "Please pass a valid API key". The
validator classified anything that was not 401/403 as ``unreachable``, and first run lets
``unreachable`` through on purpose — a local Ollama that happens to be off must not trap
someone on a step with no skip. So any string was accepted as a Gemini key, and the failure
surfaced much later, when Lucius could not answer.
"""

from __future__ import annotations

import pytest

from backend.api.models import _looks_like_a_key_rejection


@pytest.mark.parametrize(
    "body",
    [
        '{"error":{"code":400,"message":"Please pass a valid API key","status":"INVALID_ARGUMENT"}}',
        '{"error":{"message":"API key not valid. Please pass a valid API key."}}',
        '{"error":{"status":"API_KEY_INVALID"}}',
        '{"error":{"message":"Incorrect API key provided"}}',
        '{"error":{"message":"Invalid API key"}}',
        "Unauthorized",
    ],
)
def test_a_key_rejection_is_recognised_however_it_is_worded(body: str):
    assert _looks_like_a_key_rejection(body) is True


@pytest.mark.parametrize(
    "body",
    [
        '{"error":{"message":"Service temporarily unavailable"}}',
        '{"error":{"message":"Rate limit exceeded"}}',
        '{"error":{"message":"model not found"}}',
        "",
    ],
)
def test_an_outage_is_never_reported_as_a_bad_key(body: str):
    """The costly direction. Calling a temporary failure "your key is wrong" sends someone
    hunting for a new key they do not need, so the markers stay narrow on purpose."""
    assert _looks_like_a_key_rejection(body) is False


class _Resp:
    """Minimal stand-in for an httpx.Response."""

    def __init__(self, payload, text=""):
        self._payload = payload
        self.text = text or ""

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"error": {"message": "Please pass a valid API key"}}, "Please pass a valid API key"),
        ({"error": {"status": "API_KEY_INVALID"}}, "API_KEY_INVALID"),
        ({"error": "flat string error"}, "flat string error"),
        ({"detail": "not found"}, "not found"),
        ({"message": "over quota"}, "over quota"),
    ],
)
def test_the_providers_own_words_are_what_reach_the_operator(payload, expected):
    """ "Provider returned HTTP 400" tells someone nothing they can act on. Every provider
    nests its message somewhere different, so the common shapes are tried in turn."""
    from backend.api.models import _provider_error_text

    assert _provider_error_text(_Resp(payload)) == expected


@pytest.mark.parametrize(
    "resp",
    [
        _Resp(None, "<html>502 Bad Gateway</html>"),
        _Resp({"unexpected": {"nested": "shape"}}),
        _Resp(["not", "a", "dict"]),
        _Resp({}),
    ],
)
def test_an_unrecognised_body_yields_nothing_so_the_caller_can_say_it_plainly(resp):
    """An unknown body is not a message — it is HTML, a stack trace, or a wall of JSON.
    Pasting that into a setup screen is worse than a plain sentence."""
    from backend.api.models import _provider_error_text

    assert _provider_error_text(resp) == ""


def test_a_long_body_is_capped_rather_than_pasted_into_the_ui():
    from backend.api.models import _provider_error_text

    assert len(_provider_error_text(_Resp({"error": {"message": "x" * 5000}}))) == 200


@pytest.mark.parametrize(
    "key,fragment",
    [
        ("sk-ant-real—key", "characters a credential cannot carry"),
        ("sk-“smart-quoted”", "characters a credential cannot carry"),
        ("sk-line\nbreak", "line break"),
        ("sk-carriage\rreturn", "line break"),
    ],
)
def test_a_key_that_cannot_be_sent_is_rejected_with_the_reason(key, fragment):
    """An HTTP header is latin-1 at best, so httpx raises before the request leaves the
    machine. That surfaced as a generic "could not check that provider" and sent people
    hunting for an outage — the real cause was a paste that picked up the wrong clipboard.
    Seen for real on 2026-08-24: a key containing an em dash."""
    from backend.api.models import _unusable_key_reason

    assert fragment in _unusable_key_reason(key)


@pytest.mark.parametrize("key", ["", "sk-ant-api03-plain-ascii", "sk-or-v1-0123456789"])
def test_a_sendable_key_is_left_alone(key):
    from backend.api.models import _unusable_key_reason

    assert _unusable_key_reason(key) == ""


class TestExplainFailure:
    """What reaches the setup screen is OUR sentence naming the provider, not the provider's
    machine-facing string ("invalid x-api-key", "API_KEY_INVALID") pasted verbatim."""

    def test_a_rejected_key_names_the_provider_plainly(self):
        from backend.api.models import _explain_failure

        assert _explain_failure("Anthropic", 401, '{"error":{"message":"invalid x-api-key"}}') == (
            "That key was rejected by Anthropic."
        )

    def test_a_401_is_a_rejection_even_with_an_unrecognised_body(self):
        from backend.api.models import _explain_failure

        assert _explain_failure("Foo", 401, "") == "That key was rejected by Foo."

    def test_rate_limiting_is_distinguished_from_a_bad_key(self):
        from backend.api.models import _explain_failure

        msg = _explain_failure("OpenRouter", 429, '{"error":{"message":"Rate limit exceeded"}}')
        assert "rate-limiting" in msg
        assert "rejected" not in msg

    def test_an_outage_is_distinguished_from_a_bad_key(self):
        from backend.api.models import _explain_failure

        msg = _explain_failure("Gemini", 503, "overloaded, try again later")
        assert "not responding" in msg
        assert "rejected" not in msg

    def test_an_unrecognised_failure_gets_a_plain_fallback(self):
        from backend.api.models import _explain_failure

        assert _explain_failure("Custom", 418, "I'm a teapot") == "Could not reach Custom."


def test_a_public_models_endpoint_does_not_prove_the_key_is_good():
    """OpenRouter's /models answers 200 with the full catalogue for ANY key — a random string
    validated cleanly and only failed on the first real chat request. The catalogue names a
    second endpoint that actually requires the credential, and validation must use it."""
    from backend.db.model_config_store import PROVIDER_CATALOG

    entry = next(e for e in PROVIDER_CATALOG if e["key"] == "openrouter")
    assert entry.get("auth_probe_path"), "openrouter must declare an endpoint that checks auth"
