"""Can Lucius's tools still reach the model through this CLI? — and the boot-time check that asks.

A headless CLI offers the model its own tool world. Ours are taught through the system prompt
(``cli_tool_protocol``), so when a CLI update starts routing tool use somewhere else, nothing
errors: the model just answers "I can't call that tool". That happened on 2026-10-03 (Codex
``code_mode_host``). :func:`probe_tool_reachability` is the one definition of "reachable" — a
synthetic tool whose result carries a nonce the model cannot guess — shared by the opt-in live
suite (``tests/cli_live``) and :func:`check_on_startup`, which runs it once per CLI version when
that CLI is the configured model and posts a notification if it fails.
Doc: ``docs/ai/CLI_REGRESSION.md``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from backend.ai.cli_regression import cli_version, version_changed
from backend.ai.models.base import AIProvider

logger = logging.getLogger(__name__)

CLI_TRANSPORTS = ("codex_cli", "claude_cli")
_LABELS = {"codex_cli": "Codex", "claude_cli": "Claude Code"}

PROBE_TOOL_NAME = "get_secret_number"
PROBE_TOOL = {
    "name": PROBE_TOOL_NAME,
    "description": "Returns the user's secret number. Takes no arguments.",
    "input_schema": {"type": "object", "properties": {}},
}
# Shaped like the real thing: an identity and "your only way to read the book is the tools". A
# bare "you are a test assistant" is not a regression, it is an unrealistic prompt — gpt-6 models
# answer it with "that tool isn't available" even on a healthy install.
PROBE_SYSTEM = (
    "You are Lucius, the assistant inside a personal finance app. Your only way to read the "
    "user's book is the tools listed below."
)
PROBE_QUESTION = "What is my secret number?"

_ROUND_TIMEOUT_SECONDS = 180.0
_MAX_ROUNDS = 3


@dataclass
class ProbeResult:
    """``status``: ``ok``; ``unreachable`` (the model answered without using the tool, or lost
    the nonce on the way back — the failure this exists to catch); ``error`` (the CLI itself
    failed: logged out, offline, rate limited — says nothing about tools)."""

    status: str
    detail: str = ""
    nonce: str = ""
    answer: str = ""
    thinking: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


async def _round(provider: AIProvider, messages: list[dict], model: str, effort: str):
    text: list[str] = []
    thinking: list[str] = []
    calls: list[dict] = []

    async def drain() -> None:
        async for event in provider.stream_chat(
            messages, [PROBE_TOOL], PROBE_SYSTEM, model, effort
        ):
            if event.get("type") == "text_delta":
                text.append(event.get("text") or "")
            elif event.get("type") == "thinking_delta":
                thinking.append(event.get("text") or "")
            elif event.get("type") == "tool_use":
                calls.append(event)

    await asyncio.wait_for(drain(), timeout=_ROUND_TIMEOUT_SECONDS)
    return "".join(text).strip(), "".join(thinking), calls


async def probe_tool_reachability(
    provider: AIProvider, model: str, *, effort: str = "low", round_trip: bool = True
) -> ProbeResult:
    """Offer ``get_secret_number`` and check the model calls it (and, with ``round_trip``, that
    the nonce we hand back is what its final answer reports).

    The host loop is real: a model that asks for the tool again after seeing the result (some
    do, now and then) is answered again, up to ``_MAX_ROUNDS`` — that is model behaviour, not a
    broken transport.
    """
    nonce = f"onw-{uuid.uuid4().hex[:12]}"
    messages: list[dict] = [{"role": "user", "content": PROBE_QUESTION}]
    thinking = ""
    try:
        for _ in range(_MAX_ROUNDS):
            text, more_thinking, calls = await _round(provider, messages, model, effort)
            thinking += more_thinking
            call = next((c for c in calls if c.get("name") == PROBE_TOOL_NAME), None)
            if call is None:
                break
            if not round_trip:
                return ProbeResult("ok", nonce=nonce, thinking=thinking)
            messages += [
                {"role": "assistant", "content": [{**call, "type": "tool_use"}]},
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": call["id"], "content": nonce}
                    ],
                },
            ]
    except Exception as exc:  # noqa: BLE001 — a CLI failure of any kind is "error", never "unreachable"
        return ProbeResult("error", f"{type(exc).__name__}: {exc}"[:300], nonce)
    if len(messages) == 1:
        detail = f"no tool call; model said: {text[:300]!r}"
        return ProbeResult("unreachable", detail, nonce, text, thinking)
    if nonce not in text:
        detail = f"nonce missing from answer: {text[:300]!r}"
        return ProbeResult("unreachable", detail, nonce, text, thinking)
    return ProbeResult("ok", nonce=nonce, answer=text, thinking=thinking)


# ── boot-time self-check ──────────────────────────────────────────────────────


def _notify_broken(transport: str, version: str, detail: str) -> None:
    from backend.db import NotificationLogStore

    label = _LABELS[transport]
    NotificationLogStore().append(
        {
            "type": "notification",
            "severity": "warning",
            "title": f"{label} {version} changed how tools work",
            "summary": (
                f"{label} {version} changed how tools work — Lucius can't use your book's tools "
                "with it. Switch model in Settings or update Open Net Worth."
            ),
            "trigger": "cli_self_check",
        }
    )
    logger.warning("cli self-check: %s %s cannot reach Lucius's tools — %s", label, version, detail)


async def check_on_startup() -> None:
    """Probe the configured CLI once per installed version; notify if tools no longer reach it.

    Fail-safe by construction: it runs in a background task, never raises, and a probe that
    fails for an ``error`` reason (offline, logged out) records nothing, so the next boot tries
    again instead of announcing a CLI problem that was really a network one.
    """
    try:
        from backend.ai.assistant import resolve_model_context
        from backend.ai.models.cli_default_model import cli_default_model
        from backend.db.cli_probe_state_store import CliProbeStateStore
        from backend.db.model_config_store import cli_binary_path

        ctx = resolve_model_context()
        if ctx.transport not in CLI_TRANSPORTS:
            return
        binary = cli_binary_path(ctx.transport, ctx.base_url)
        version = cli_version(binary) if binary else ""
        store = CliProbeStateStore()
        if not version_changed(store.last_probed_version(ctx.transport), version):
            return
        model = ctx.model or cli_default_model(ctx.transport)
        result = await probe_tool_reachability(ctx.provider, model, round_trip=False)
        if result.status == "error":
            logger.info(
                "cli self-check: probe of %s %s inconclusive — %s",
                ctx.transport,
                version,
                result.detail,
            )
            return
        store.record(ctx.transport, version, result.ok)
        if not result.ok:
            _notify_broken(ctx.transport, version, result.detail)
        else:
            logger.info("cli self-check: %s %s reaches Lucius's tools", ctx.transport, version)
    except Exception:  # noqa: BLE001 — a background nicety must never take the app down
        logger.exception("cli self-check failed (non-fatal)")
