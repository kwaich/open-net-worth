"""Every structured-output schema we send must survive OpenAI strict mode.

Codex forwards `outputSchema` as `response_format: codex_output_schema` (strict). Strict mode
rejected the Automation drafting schema with `invalid_json_schema … schema must have a 'type'
key` because a `{"const": …}` carried no type. `strictify_schema` closes objects and requires
every property on the way out; what it cannot invent is a missing `type`, so this checks the
schema AS SENT (after strictify) against the rules strict mode enforces.
"""

import pytest

from backend.ai.models.codex_app_server import strictify_schema
from backend.monitoring import ai_opinion, brief_narration, intel_agent
from backend.services.statements import lucius_pass, match_policy

_COMBINATORS = ("anyOf", "oneOf", "allOf")


def strict_violations(schema, path="$") -> list[str]:
    """Strict-mode problems in `schema`, each prefixed by where it was found."""
    if not isinstance(schema, dict):
        return [f"{path}: subschema is not an object"]
    problems: list[str] = []
    if not any(k in schema for k in ("type", "$ref", *_COMBINATORS)):
        problems.append(f"{path}: schema must have a 'type' key")
    if schema.get("type") == "object" or "properties" in schema:
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is not False:
            problems.append(f"{path}: object must set additionalProperties false")
        if sorted(schema.get("required", [])) != sorted(props):
            problems.append(f"{path}: every property must be required")
        for name, sub in props.items():
            problems += strict_violations(sub, f"{path}.{name}")
    if isinstance(schema.get("items"), dict):
        problems += strict_violations(schema["items"], f"{path}[]")
    for key in _COMBINATORS:
        for i, sub in enumerate(schema.get(key, [])):
            problems += strict_violations(sub, f"{path}.{key}[{i}]")
    for key in ("$defs", "definitions"):
        for name, sub in schema.get(key, {}).items():
            problems += strict_violations(sub, f"{path}.{key}.{name}")
    return problems


_SCHEMAS = {
    "statement_decisions": lucius_pass._DECISIONS_SCHEMA,
    "statement_verdict": lucius_pass._VERDICT_SCHEMA,
    "match_policy": match_policy._POLICY_SCHEMA,
    "intel_stories": intel_agent._OUTPUT_SCHEMA.schema,
    **{f"pulse_brief_{level}": brief_narration._schema(level) for level in ai_opinion.LEVELS},
}


@pytest.mark.parametrize("name", sorted(_SCHEMAS))
def test_schema_sent_to_codex_satisfies_strict_mode(name):
    assert strict_violations(strictify_schema(_SCHEMAS[name])) == []


def test_the_checker_catches_the_original_defect():
    bad = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"kind": {"const": "manual"}},
        "required": ["kind"],
    }
    assert strict_violations(bad) == ["$.kind: schema must have a 'type' key"]
    assert strict_violations({"type": "object", "properties": {"a": {"type": "string"}}}) != []
