"""Real-CLI regression suite — see ``docs/ai/CLI_REGRESSION.md``. Skipped unless ONW_CLI_LIVE=1.

Everything goes through OUR provider classes (``CodexCliProvider`` / ``ClaudeCliProvider``), so a
pass means the transport the app ships still works against the CLI that is installed now:

* tool reachability — a synthetic ``get_secret_number`` tool; the nonce we return from it must
  come back in the model's final answer;
* no leakage — Codex's managed ``CODEX_HOME`` keeps connected apps / code mode off and the
  app-server lists no MCP servers or apps; Claude Code's ``system/init`` reports no tools or MCP
  servers under our exact flags;
* strict structured output — a real ``complete`` with the statement match-policy schema;
* reasoning text — distinct reasoning parts are not fused together.
"""

from __future__ import annotations

import asyncio
import json
import re
import warnings

import pytest

from backend.ai import cli_regression as reg
from backend.ai.cli_probe import probe_tool_reachability
from backend.ai.models.claude_cli_provider import ClaudeCliProvider
from backend.ai.models.codex_app_server import client_for
from backend.ai.models.codex_cli_provider import CodexCliProvider
from backend.core.llm_seam import _response_format_for, parse_json
from backend.services.statements import match_policy
from tests.cli_live.conftest import binary_or_skip, check_snapshot, models_for

_TURN_TIMEOUT = 240
# What a Codex thread may contain when the host owns every tool: the user's message, the
# model's reasoning and its reply. Anything else (an mcpToolCall, a commandExecution, a
# dynamicToolCall…) means the model reached a tool of Codex's own.
_PLAIN_ITEMS = {"userMessage", "agentMessage", "reasoning"}
_FUSED_BOLD = re.compile(r"\*\*\*\*")


def _assert_ok(result):
    assert result.ok, f"tool probe {result.status}: {result.detail}"


async def _structured(provider, model: str) -> dict:
    raw = await asyncio.wait_for(
        provider.complete(
            "Notes: dates may be up to 3 days apart; amounts must match exactly.",
            system=match_policy._POLICY_SYSTEM,
            model=model,
            max_tokens=900,
            source="cli_live",
            response_format=_response_format_for(match_policy._POLICY_SCHEMA),
            effort="low",
        ),
        timeout=_TURN_TIMEOUT,
    )
    parsed = parse_json(raw)
    assert isinstance(parsed, dict), f"not a JSON object: {raw[:300]!r}"
    assert match_policy._validate_policy(parsed) is None, f"validator rejected {parsed}"
    assert set(match_policy._POLICY_SCHEMA["required"]) <= set(parsed), f"missing keys: {parsed}"
    return parsed


# ── Codex ─────────────────────────────────────────────────────────────────────


def test_codex_features_under_our_managed_config():
    binary = binary_or_skip("codex")
    features = reg.codex_features(binary)
    assert reg.required_off_violations(features) == []
    check_snapshot(
        "codex-features",
        {"cli_version": reg.cli_version(binary), "features": features},
        lambda base, now: reg.diff_features(base["features"], now["features"]),
    )


async def test_codex_app_server_lists_no_mcp_servers_or_apps(codex_frames):
    binary_or_skip("codex")
    client = client_for("codex")
    await client.ensure_started()
    assert (await client._request("mcpServerStatus/list", {}))["data"] == []
    assert (await client._request("app/list", {}))["data"] == []


@pytest.mark.parametrize("model", models_for("codex_cli"))
async def test_codex_tools_reach_the_model_and_nothing_else_does(model, codex_frames):
    binary_or_skip("codex")
    result = await probe_tool_reachability(CodexCliProvider(), model, effort="medium")
    _assert_ok(result)

    items = {
        (f.get("params") or {}).get("item", {}).get("type")
        for f in codex_frames
        if f.get("method") == "item/started"
    }
    assert items <= _PLAIN_ITEMS, f"the model reached a native Codex tool: {items - _PLAIN_ITEMS}"
    assert "codex_apps" not in json.dumps(codex_frames)
    started = next(f for f in codex_frames if "thread" in (f.get("result") or {}))["result"]
    assert started["instructionSources"] == [], "Codex loaded instruction files into the thread"

    _assert_reasoning_parts_separated(result.thinking, codex_frames)


def _assert_reasoning_parts_separated(thinking: str, frames: list[dict]) -> None:
    parts = {
        (f["method"], f["params"].get("itemId"), f["params"].get("summaryIndex"))
        for f in frames
        if str(f.get("method", "")).startswith("item/reasoning/")
        and (f.get("params") or {}).get("delta")
    }
    assert not _FUSED_BOLD.search(thinking), f"reasoning parts fused: {thinking[:300]!r}"
    if len(parts) < 2:
        warnings.warn(f"only {len(parts)} reasoning part(s) streamed — separation not exercised")
    else:
        assert thinking.count("\n\n") >= len(parts) - 1, "reasoning parts not newline-separated"


@pytest.mark.parametrize("model", models_for("codex_cli"))
async def test_codex_strict_structured_output(model, codex_frames):
    binary_or_skip("codex")
    await _structured(CodexCliProvider(), model)


# ── Claude Code ───────────────────────────────────────────────────────────────


async def test_claude_init_reports_no_tools_or_mcp_servers():
    binary = binary_or_skip("claude")
    view = reg.claude_init_view(await reg.capture_claude_init(ClaudeCliProvider()))
    assert view["tools"] == [] and view["mcp_servers"] == [], f"Claude Code exposes {view}"
    check_snapshot(
        "claude-init",
        {"cli_version": reg.cli_version(binary), **view},
        reg.diff_claude_init,
    )


@pytest.mark.parametrize("model", models_for("claude_cli"))
async def test_claude_tools_reach_the_model(model):
    binary_or_skip("claude")
    result = await probe_tool_reachability(ClaudeCliProvider(), model, effort="medium")
    _assert_ok(result)
    assert not _FUSED_BOLD.search(result.thinking), "reasoning parts fused"


@pytest.mark.parametrize("model", models_for("claude_cli"))
async def test_claude_strict_structured_output(model):
    binary_or_skip("claude")
    await _structured(ClaudeCliProvider(), model)
