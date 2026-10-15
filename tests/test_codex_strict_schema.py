"""Codex's structured output is STRICT — our schemas must be normalised for it.

Regression (found 2026-08-09, statement import on `gpt-5.6-sol`): every schema-bearing
codex app-server turn was rejected with

    invalid_json_schema: Invalid schema for response_format 'codex_output_schema':
    In context=(), 'additionalProperties' is required to be supplied and to be false.

100% of them, from the very first — not flakiness. It went unnoticed for so long because the
failure was swallowed twice: the app-server parse collapsed the payload to the literal word
"error", and the provider then silently rerouted the turn to `codex exec`, which takes no
schema and therefore always appeared to work. The visible symptom was a statement import that
quietly ignored the user's typed instructions.
"""

from __future__ import annotations

from backend.ai.models.codex_app_server import strictify_schema
from backend.ai.models.codex_cli_provider import _is_retryable
from backend.core.errors import ExternalServiceError


def _violations(node, path="()") -> list[str]:
    """Every place the strict dialect would reject `node`."""
    bad: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            if node.get("additionalProperties") is not False:
                bad.append(f"{path}: additionalProperties is not false")
            props = set((node.get("properties") or {}).keys())
            missing = props - set(node.get("required") or [])
            if missing:
                bad.append(f"{path}: {sorted(missing)} absent from required")
        for key, value in node.items():
            bad += _violations(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            bad += _violations(value, f"{path}[{i}]")
    return bad


def test_the_real_schema_that_was_rejected_now_passes() -> None:
    from backend.services.statements.lucius_pass import _DECISIONS_SCHEMA

    # The schema as authored genuinely violates the strict dialect — if this ever stops being
    # true the normaliser is being tested against nothing.
    assert _violations(_DECISIONS_SCHEMA)

    assert _violations(strictify_schema(_DECISIONS_SCHEMA)) == []


def test_nested_objects_are_normalised_at_every_depth() -> None:
    schema = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "meta": {
                            "type": "object",
                            "properties": {"note": {"type": "string"}},
                        },
                    },
                },
            },
        },
    }
    assert _violations(strictify_schema(schema)) == []


def test_branch_keywords_are_followed() -> None:
    """An object hiding inside anyOf must be normalised too, or the API rejects that one."""
    schema = {"anyOf": [{"type": "object", "properties": {"a": {"type": "string"}}}]}
    out = strictify_schema(schema)
    assert out["anyOf"][0]["additionalProperties"] is False
    assert out["anyOf"][0]["required"] == ["a"]


def test_the_input_is_not_mutated() -> None:
    """The caller's schema is a module-level constant shared with other providers — normalising
    it in place would silently change what every OTHER transport sends."""
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    strictify_schema(schema)
    assert "additionalProperties" not in schema
    assert "required" not in schema


def test_an_already_strict_schema_is_unchanged() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
        "additionalProperties": False,
    }
    assert strictify_schema(schema) == schema


def test_a_non_object_schema_survives() -> None:
    assert strictify_schema({"type": "string"}) == {"type": "string"}
    assert strictify_schema({}) == {}


def test_a_400_class_rejection_is_not_retried() -> None:
    """`invalid_json_schema` is identical on attempt 2 and 3 — retrying it only delays the
    real message and buries it under duplicate warnings."""
    assert _is_retryable(ExternalServiceError("app-server: invalid_json_schema ...")) is False
    assert _is_retryable(ExternalServiceError("type: invalid_request_error")) is False


def test_a_transient_failure_is_still_retried() -> None:
    assert _is_retryable(ExternalServiceError("connection reset by peer")) is True
    assert _is_retryable(ExternalServiceError("app-server exited mid-turn")) is True


def test_the_prompt_asks_for_every_key_the_strict_schema_requires() -> None:
    """Strict mode has no optional field, so `strictify_schema` puts every property in
    `required`. A prompt telling the model to OMIT those fields makes valid output
    impossible: the CLI retries and dies with `error_max_structured_output_retries`
    (regression repro, 2026-08-09).
    """
    from backend.services.import_common import RowResult
    from backend.services.statements.canonical import CanonicalRow
    from backend.services.statements.lucius_pass import _DECISION_ITEM_SCHEMA, _build_prompt

    rows = [CanonicalRow(date="2026-06-05", direction="OUT", amount=1.0, description="x")]
    prompt = _build_prompt(
        "note",
        rows,
        [RowResult(line=1, draft={"date": "2026-06-05"})],
        {"a": {"id": "a", "name": "A", "currency": "SGD"}},
        [],
        "a",
        [0],
    )

    assert "only if action=" not in prompt
    assert "EXACTLY the fields its own action needs" not in prompt
    for key in strictify_schema(_DECISION_ITEM_SCHEMA)["required"]:
        assert f'"{key}"' in prompt, f"{key} is required by the schema but absent from the prompt"


def test_the_envelope_the_prompt_names_matches_the_schema_on_every_round() -> None:
    """The re-ask round is where this bit: the prompt said `Return exactly {"decisions",
    "summary"}` while the strict schema also required `analysis`. Round 0 survived because
    analysis is the first property and the model wrote it anyway; the revision, told to return
    the decisions list, dropped it — and Claude Code retried to
    `error_max_structured_output_retries` (owner repro, 2026-08-09).
    """
    from backend.services.import_common import RowResult
    from backend.services.statements.canonical import CanonicalRow
    from backend.services.statements.lucius_pass import _DECISIONS_SCHEMA, _build_prompt

    rows = [CanonicalRow(date="2026-06-05", direction="OUT", amount=1.0, description="x")]
    results = [RowResult(line=1, draft={"date": "2026-06-05"})]
    accounts = {"a": {"id": "a", "name": "A", "currency": "SGD"}}
    required = strictify_schema(_DECISIONS_SCHEMA)["required"]

    for prior in (None, {"decisions": [], "issues": ["rows 1, 2 look like row 0"]}):
        prompt = _build_prompt("note", rows, results, accounts, [], "a", [0], prior=prior)
        for key in required:
            assert f'"{key}"' in prompt, f"{key} required by the schema, unnamed in the prompt"
