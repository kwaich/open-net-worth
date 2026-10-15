"""Regression tests for the tool-input secret leak (P0-3 / SEC1).

Before this fix, the dispatch loop wrote a tool call's raw ``tu["input"]`` to TWO sinks:
  1. the ``tool_call`` INFO log line (``str(tu["input"])[:120]``) → ``app.log`` → LogViewer /
     ``GET /api/logs`` / the ``get_recent_logs`` tool → back into model context;
  2. the run-step timeline (``_append_step({... "input": tu["input"] ...})``) → ``run_history.json``
     → read back by the headless-allowed ``get_agent_runs`` tool.
Tools like ``update_model_config`` / ``set_provider_credentials`` carry an ``api_key`` in their
input, so a key typed into chat leaked verbatim into both. ``backend.core.redact`` masks
secret-shaped KEYS before the value reaches either sink: ``redact_summary`` (string, for the log
line) and ``redact_secrets`` (structure, for the persisted run step).
"""

from __future__ import annotations

from backend.core.redact import redact_secrets, redact_summary

# ── redact_summary: the log-line form ──────────────────────────────────────────


def test_summary_redacts_api_key_but_keeps_the_rest() -> None:
    out = redact_summary({"provider": "anthropic", "api_key": "sk-ant-SUPERSECRET-123"})
    assert "sk-ant-SUPERSECRET-123" not in out  # the secret must never reach the log
    assert "***" in out
    assert "anthropic" in out  # non-secret fields stay for debuggability


def test_summary_matches_the_real_model_config_tool_input_shape() -> None:
    out = redact_summary(
        {"provider": "openai", "model": "gpt-5", "base_url": "", "api_key": "sk-live-DEADBEEF"}
    )
    assert "sk-live-DEADBEEF" not in out
    assert "openai" in out and "gpt-5" in out


def test_summary_non_dict_passes_through_without_crashing() -> None:
    assert redact_summary("just a string") == "just a string"


def test_summary_truncates_to_limit() -> None:
    assert len(redact_summary({"note": "x" * 500})) <= 120


def test_summary_never_raises_on_odd_input() -> None:
    class Weird:
        def __str__(self) -> str:
            return "weird"

    assert isinstance(redact_summary({"k": Weird()}), str)


# ── redact_secrets: the structured form persisted into the run step ─────────────


def test_secrets_masks_value_and_preserves_shape() -> None:
    out = redact_secrets({"provider": "openai", "api_key": "sk-x"})
    assert out == {"provider": "openai", "api_key": "***"}  # structure kept, only value masked


def test_secrets_token_and_secret_variants_including_nested_dict() -> None:
    out = redact_secrets(
        {
            "access_token": "tok_abc123",
            "client_secret": "cs_zzz",
            "nested": {"refresh_token": "rt_999", "base_url": "https://api.example.com"},
        }
    )
    assert out["access_token"] == "***"
    assert out["client_secret"] == "***"
    assert out["nested"]["refresh_token"] == "***"
    assert out["nested"]["base_url"] == "https://api.example.com"  # non-secret preserved


def test_secrets_masks_a_secret_nested_inside_a_list_of_dicts() -> None:
    # The branch the reviewer flagged as untested: a secret hiding in a list of dicts.
    out = redact_secrets({"items": [{"api_key": "sk-leak"}, {"note": "ok"}]})
    assert out["items"][0]["api_key"] == "***"
    assert out["items"][1]["note"] == "ok"
    # And nothing anywhere in the serialized form exposes the raw secret.
    assert "sk-leak" not in str(out)


def test_secrets_preserves_identifier_keys_that_are_not_secrets() -> None:
    # idempotency_key / account_id / payee_key are identifiers, not secrets — stay visible.
    out = redact_secrets({"idempotency_key": "idem-1", "account_id": "acc_1", "amount": 4.5})
    assert out == {"idempotency_key": "idem-1", "account_id": "acc_1", "amount": 4.5}
    assert "***" not in str(out)
