"""Which parked Codex turn a host round continues — the claim half of ``codex_live_turn``.

A round may continue a parked turn only when its messages are exactly "that turn's calls, then
all of their results (plus text the host merged in after them), then only plain user text", and
the thread was started with this round's tools, instructions, model and effort. Anything else
closes the parked turn and the round starts a fresh thread from the transcript.
ADR ``2026-10-05-codex-turns-stay-open-across-tool-rounds``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .codex_live_turn import LiveTurn, _parked

log = logging.getLogger(__name__)


def _is_results(message: dict) -> bool:
    """Tool results, optionally with text after them. The host's provider projection
    (``skill_runtime.provider_messages``) MERGES consecutive user messages, so a skill body
    activated by one of these calls arrives as text blocks in the same message."""
    content = message.get("content")
    return (
        message.get("role") == "user"
        and isinstance(content, list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
        and all(isinstance(b, dict) and b.get("type") in ("tool_result", "text") for b in content)
    )


def _plain_text(message: dict) -> Optional[str]:
    """A user message that is text and nothing else, as one string; None otherwise."""
    if message.get("role") != "user":
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(
        isinstance(b, dict) and b.get("type") == "text" for b in content
    ):
        return "\n".join(b.get("text") or "" for b in content)
    return None


def result_items(block: dict) -> list[dict]:
    """A host ``tool_result`` block as ``DynamicToolCallOutputContentItem`` s."""
    content = block.get("content")
    if isinstance(content, str) or content is None:
        return [{"type": "inputText", "text": content or ""}]
    items: list[dict] = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            items.append({"type": "inputText", "text": part.get("text") or ""})
        elif part.get("type") == "image":
            source = part.get("source") or {}
            if source.get("type") in (None, "base64") and source.get("data"):
                media = source.get("media_type") or "image/png"
                items.append(
                    {"type": "inputImage", "imageUrl": f"data:{media};base64,{source['data']}"}
                )
    return items or [{"type": "inputText", "text": ""}]


def claim(messages: list[dict], key: dict):
    """The parked turn this round continues, with the answers and steer text to send it —
    or None, and the round starts a fresh thread.

    A claim needs: the newest assistant message's tool calls are exactly one parked turn's
    calls; the next message is their results, all of them (plus any text merged in after them); only plain user text follows; and
    the thread was started with this round's tools, instructions, model and effort. A turn
    that matches the calls but fails the rest is closed — that conversation moved on."""
    last = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "assistant"),
        None,
    )
    if last is None or not isinstance(messages[last].get("content"), list):
        return None
    ids = [
        b.get("id")
        for b in messages[last]["content"]
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    turns = {id(_parked[i]): _parked[i] for i in ids if i in _parked}
    if not ids or not turns:
        return None
    live = next(iter(turns.values()))
    reason = _claim_refusal(messages[last + 1 :], ids, live, key, len(turns))
    if reason:
        log.info("codex_live_turn: not continuing the parked turn (%s) — fresh thread", reason)
        for turn in turns.values():
            turn.close()
        return None
    reply = messages[last + 1]["content"]
    results = {b["tool_use_id"]: b for b in reply if b.get("type") == "tool_result"}
    answers = [(i, result_items(results[i]), bool(results[i].get("is_error"))) for i in ids]
    steer = [b.get("text") or "" for b in reply if b.get("type") == "text"]
    steer += [_plain_text(m) or "" for m in messages[last + 2 :]]
    return live, answers, [s for s in steer if s.strip()]


def _claim_refusal(
    tail: list[dict], ids: list[str], live: LiveTurn, key: dict, n_turns: int
) -> str:
    if n_turns != 1 or set(ids) != set(live.bound):
        return "the calls are not one parked turn's"
    if live.closed or live.loop is not asyncio.get_running_loop():
        return "the parked turn is gone"
    if not tail or not _is_results(tail[0]):
        return "the next message is not the calls' results"
    if {b.get("tool_use_id") for b in tail[0]["content"] if b.get("type") == "tool_result"} != set(
        ids
    ):
        return "a result is missing (a call awaiting approval?)"
    if any(_plain_text(m) is None for m in tail[1:]):
        return "something other than plain user text follows the results"
    changed = [part for part in key if live.key.get(part) != key[part]]
    if changed:
        return "the " + " and ".join(changed) + " changed"
    return ""
