"""``effort="none"`` must reach a CLI transport as its LOWEST rung, never as silence.

Scar (owner-reported, 2026-07-31): a chat-suggestions call — which asks for ``effort="none"``
and ``max_tokens=400`` to produce three short tappable strings — came back with 1952 output
tokens on ``claude_cli:haiku``.

Both CLI providers validated effort against a ladder that has no off rung, so ``"none"``
matched nothing and NO flag was passed. That is not neutral: it hands the run the CLI's own
default effort, which is higher than ``low``. The caller explicitly asking for no reasoning
therefore got MORE of it than one asking for `low` would have.

Every other provider honours the contract (``openai_provider`` sends
``reasoning_effort="minimal"`` or disables reasoning outright), so the CLIs were the odd
ones out.
"""

from __future__ import annotations

import pytest

from backend.ai.models.claude_cli_provider import ClaudeCliProvider, _cli_effort
from backend.ai.models.codex_cli_provider import CodexCliProvider


@pytest.fixture(autouse=True)
def _cli_binaries(monkeypatch):
    """``_argv`` resolves the binary on PATH; these tests read flags, not the install."""
    monkeypatch.setattr(ClaudeCliProvider, "_resolve_cli", lambda self: "claude")
    monkeypatch.setattr(CodexCliProvider, "_resolve_cli", lambda self: "codex")


class TestClaudeCliEffort:
    @pytest.mark.parametrize("value", ["none", "off", "NONE", "  Off  "])
    def test_no_reasoning_floors_to_the_lowest_rung(self, value):
        assert _cli_effort(value) == "low"

    @pytest.mark.parametrize("value", ["low", "medium", "high", "xhigh", "max"])
    def test_real_rungs_pass_through(self, value):
        assert _cli_effort(value) == value

    @pytest.mark.parametrize("value", ["", "   ", "banana", None])
    def test_no_opinion_passes_no_flag(self, value):
        """Blank/unknown means "no opinion", which is NOT the same as "no reasoning" — the
        CLI's own default is the right answer there."""
        assert _cli_effort(value) == ""


def test_claude_argv_carries_the_floored_effort():
    argv = ClaudeCliProvider()._argv("haiku", "sys", effort="none")
    assert "--effort" in argv
    assert argv[argv.index("--effort") + 1] == "low"


def test_claude_argv_omits_effort_when_unspecified():
    assert "--effort" not in ClaudeCliProvider()._argv("haiku", "sys", effort="")


class TestCodexCliEffort:
    def test_no_reasoning_floors_to_the_lowest_rung(self):
        from backend.ai.models.codex_cli_provider import _EFFORT_MAP

        assert _EFFORT_MAP["none"] == "low"
        assert _EFFORT_MAP["off"] == "low"

    def test_argv_carries_the_floored_effort(self):
        argv = CodexCliProvider()._argv("gpt-5.6-sol", effort="none")
        assert any('model_reasoning_effort="low"' in a for a in argv), argv

    def test_argv_omits_effort_when_unspecified(self):
        argv = CodexCliProvider()._argv("gpt-5.6-sol", effort="")
        assert not any("model_reasoning_effort" in a for a in argv), argv


def test_the_suggestions_caller_still_asks_for_no_reasoning():
    """Guards the other half of the contract: if this caller ever stops passing
    effort="none", the floor above silently stops applying to it."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "backend/api/chat_suggestions.py").read_text()
    assert 'effort="none"' in src
    assert "max_tokens=400" in src


# ── thinking is a SEPARATE knob from effort ──────────────────────────────────
#
# Measured on the real binary with the chat-suggestions system prompt:
#   --effort low                    → 5,579 output tokens, 53s   (204-char answer)
#   --effort low + MAX_THINKING=0   →   109 output tokens,  2.0s (207-char answer)
# The flag tunes work on the ANSWER; only the env var stops the thinking.


def test_no_thinking_is_detected_from_the_effort_contract():
    from backend.ai.models.claude_cli_provider import _no_thinking

    assert _no_thinking("none") is True
    assert _no_thinking("off") is True
    assert _no_thinking("  NONE ") is True
    # A real rung is a request to think LESS, not to stop.
    for level in ("low", "medium", "high", "xhigh", "max", "", "banana"):
        assert _no_thinking(level) is False, level


def test_child_env_disables_thinking_only_when_asked():
    from backend.ai.models.claude_cli_provider import _child_env

    assert _child_env("", no_thinking=True)["MAX_THINKING_TOKENS"] == "0"
    assert "MAX_THINKING_TOKENS" not in _child_env("", no_thinking=False)


def test_an_inherited_thinking_budget_never_leaks_into_the_child(monkeypatch):
    """The host's own env must not decide how much the CLI thinks on our turns."""
    from backend.ai.models.claude_cli_provider import _child_env

    monkeypatch.setenv("MAX_THINKING_TOKENS", "31999")
    assert "MAX_THINKING_TOKENS" not in _child_env("", no_thinking=False)
    assert _child_env("", no_thinking=True)["MAX_THINKING_TOKENS"] == "0"
