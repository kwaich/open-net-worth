"""Model clients: the real Anthropic API, and a scripted mock for zero-cost proof runs.

The mock is not a formality. It is how the harness and the scorer are proven to work
end to end before a cent is spent, and — more importantly — how the SCORER is proven to
DISCRIMINATE. A scorer that only ever sees correct behaviour is untested: it might be
passing everything. So the mock ships two personas:

* ``perfect`` — does the right thing on every task. Expect ~100% intended.
* ``sloppy``  — commits the exact mistakes these tasks are designed to catch (books the
  FX charge at the re-converted amount, treats the absolute ``assign`` as a delta,
  double-books the duplicate statement row, guesses an account on the under-specified
  request, mints a Citibank account, oversells the position). Expect a LOW score, with
  the failure reasons naming the specific bug.

If ``sloppy`` scores well, the scorer is broken and no real run should be trusted.

Mock scripts are written ONCE in canonical op terms and translated per arm, so the same
intent exercises arm A's ``add_transaction``, arm B's ``txns{action:"add"}`` and arm C's
``apply{op:"txn.add"}`` — which also proves each arm's dispatch translation.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.evals.tool_surface import ops as ops_module


def _resolve_api_key() -> str | None:
    """``ANTHROPIC_API_KEY`` env var if exported, else the operator's own stored key.

    A real run needs a key, but ``bootstrap`` deliberately redirects DATA_DIR to a
    throwaway root that has never had provider credentials written to it — so the env
    var is the ONLY source unless we also check the operator's real, un-isolated
    ``~/.open-net-worth`` tree. This mirrors the Makefile's ``QA_KEY`` fallback
    (same root cause: a caller forgets to export the var, the run silently can't
    authenticate) — that pattern already exists for the QA harnesses; this closes the
    same trap for this harness. Read in-process only: never written to a file, never
    logged, never printed, never returned anywhere but this one call site.
    """
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return env_key
    try:
        config_path = Path.home() / ".open-net-worth" / "data" / "model_config.json"
        if not config_path.exists():
            return None
        config = json.loads(config_path.read_text(encoding="utf-8"))
        key = config.get("providers", {}).get("anthropic", {}).get("api_key")
        return key or None
    except Exception:  # noqa: BLE001 - best-effort fallback, never fail the caller
        return None


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class TurnResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    #: UNCACHED input tokens only — Anthropic reports the cached portions separately.
    input_tokens: int = 0
    output_tokens: int = 0
    #: Tokens WRITTEN to the prompt cache this call (billed at 1.25x/2.0x input).
    cache_write_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    #: Tokens SERVED FROM the prompt cache this call (billed at 0.10x input).
    cache_read_tokens: int = 0
    stop_reason: str = "end_turn"
    estimated: bool = False

    @property
    def billed_input_tokens(self) -> int:
        """Every input token the provider processed, cached or not."""
        return self.input_tokens + self.cache_write_tokens + self.cache_read_tokens


class ModelClient:
    """Interface the runner codes against."""

    name = "base"

    def run_turn(
        self, system: str, messages: list[dict], tools: list[dict]
    ) -> TurnResponse:  # pragma: no cover - abstract
        raise NotImplementedError


# ── Real Anthropic client ────────────────────────────────────────────────────


class AnthropicClient(ModelClient):
    def __init__(self, model: str, max_tokens: int = 4096) -> None:
        import anthropic

        api_key = _resolve_api_key()
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set, and no stored key was found at "
                "~/.open-net-worth/data/model_config.json"
            )
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.name = f"anthropic:{model}"

    def run_turn(self, system: str, messages: list[dict], tools: list[dict]) -> TurnResponse:
        # Prompt caching is ON, mirroring the production provider: cache_control on the
        # LAST tool (which caches the whole tools block) and on the system block. Without
        # it the cache-read/cache-write split — the thing the cost model turns on — would
        # be structurally zero and the measurement meaningless.
        cached_tools = _with_cache_control(tools)
        cached_system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=cached_system,
                    messages=messages,
                    tools=cached_tools,
                )
                break
            except Exception as exc:  # noqa: BLE001 - retry transient API failures
                last_error = exc
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
        else:  # pragma: no cover
            raise last_error  # type: ignore[misc]

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input or {})))

        usage = response.usage
        write_5m, write_1h = _cache_write_breakdown(usage)
        return TurnResponse(
            text="\n".join(text_parts).strip(),
            tool_calls=calls,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_write_5m_tokens=write_5m,
            cache_write_1h_tokens=write_1h,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            stop_reason=response.stop_reason or "end_turn",
        )


def _with_cache_control(tools: list[dict]) -> list[dict]:
    """Stamp cache_control on the LAST tool, which caches the whole tools block."""
    if not tools:
        return tools
    stamped = [dict(t) for t in tools]
    stamped[-1]["cache_control"] = {"type": "ephemeral"}
    return stamped


def _cache_write_breakdown(usage: Any) -> tuple[int, int]:
    """Per-TTL cache-write split when the SDK exposes it, else (0, 0).

    Guarded with getattr because the breakdown needs a newer SDK; when it is missing the
    pricing call falls back to the flat 1.25x legacy path, exactly as the production
    provider does.
    """
    detail = getattr(usage, "cache_creation", None)
    if detail is None:
        return 0, 0
    return (
        getattr(detail, "ephemeral_5m_input_tokens", 0) or 0,
        getattr(detail, "ephemeral_1h_input_tokens", 0) or 0,
    )


# ── Mock client ──────────────────────────────────────────────────────────────


@dataclass
class Step:
    """One scripted action, expressed canonically (op name + params)."""

    op: str | None  # None => say something and stop
    params: dict = field(default_factory=dict)
    #: params resolved lazily from the fixture reference + prior tool results
    resolver: str | None = None


class MockClient(ModelClient):
    """Replays a per-task script through whichever arm is under test."""

    def __init__(self, persona: str, arm_key: str, task_id: str, ref: dict) -> None:
        from backend.evals.tool_surface import mock_scripts

        self.persona = persona
        self.arm_key = arm_key
        self.task_id = task_id
        self.ref = ref
        self.name = f"mock:{persona}"
        self._plan = mock_scripts.plan_for(persona, task_id, ref)
        self._index = 0
        self._results: list[Any] = []
        self._described: set[str] = set()
        self._calls = 0
        self._cache_signature: tuple[str, str] | None = None

    def run_turn(self, system: str, messages: list[dict], tools: list[dict]) -> TurnResponse:
        self._absorb_results(messages)
        offered = {t["name"] for t in tools}

        while self._index < len(self._plan):
            step = self._plan[self._index]
            self._index += 1
            if step.op is None:
                text = self._render_text(step.params.get("text", ""))
                return self._respond(
                    system, messages, tools, text=text, out_tokens=_estimate_text(text)
                )

            call, consumed = self._translate(step, offered)
            if call is None:
                continue
            if not consumed:
                # A discovery round-trip (find_tools / describe) PRECEDES the real call
                # rather than replacing it — rewind so the step is issued next turn.
                self._index -= 1
            return self._respond(
                system,
                messages,
                tools,
                calls=[call],
                out_tokens=_estimate_text(json.dumps(call.input)),
                stop_reason="tool_use",
            )

        return self._respond(system, messages, tools, text="Done.", out_tokens=4)

    def _respond(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        *,
        text: str = "",
        calls: list[ToolCall] | None = None,
        out_tokens: int = 0,
        stop_reason: str = "end_turn",
    ) -> TurnResponse:
        """Build a mock TurnResponse with a REALISTIC cache split.

        The stable prefix (tools + system) is written to cache on the first call of a
        run and read back on every later call; the growing message tail is uncached.
        That is the same shape the real provider produces, so the cost maths, the
        cache-split reporting and the per-arm comparison are all exercised end to end by
        the mock — with estimated token counts, which every report labels as such.
        """
        self._calls += 1
        tools_blob = json.dumps(tools, default=str)
        prefix = _estimate_text(system) + _estimate_text(tools_blob)
        tail = _estimate_text(json.dumps(messages, default=str))

        # A CHANGED tools block invalidates the cached prefix — the provider must write
        # it again rather than reading it back. This matters for arm A specifically:
        # find_tools (and the auto-load gate) grow the offered tool list mid-run, so
        # modelling every later call as a cheap cache read would understate arm A's real
        # cost on exactly the discovery tasks. Getting the direction of that error wrong
        # would flatter the arm the harness exists to scrutinise.
        signature = (system, tools_blob)
        invalidated = self._cache_signature is not None and signature != self._cache_signature
        self._cache_signature = signature

        if self._calls == 1 or invalidated:
            cache_write, cache_read = prefix, 0
        else:
            cache_write, cache_read = 0, prefix

        return TurnResponse(
            text=text,
            tool_calls=calls or [],
            input_tokens=tail,
            output_tokens=out_tokens,
            cache_write_tokens=cache_write,
            cache_write_5m_tokens=cache_write,
            cache_read_tokens=cache_read,
            stop_reason=stop_reason,
            estimated=True,
        )

    # -- arm translation ----------------------------------------------------

    def _translate(self, step: Step, offered: set[str]) -> tuple[ToolCall | None, bool]:
        """Return (call, consumed). ``consumed=False`` means this was a discovery
        round-trip that must be followed by the real call on the next turn."""
        op = ops_module.get_op(step.op or "")
        if op is None:
            # An intentionally bogus op (sloppy persona) — send it as-is so the arm's
            # unknown-tool handling is exercised.
            return ToolCall(id=self._next_id(), name=step.op or "?", input=dict(step.params)), True

        params = self._resolve(step)

        if self.arm_key in ("A", "A-real"):
            tool_name = op.tool_name
            if tool_name not in offered:
                # Discovery round-trip, exactly as the real surface requires.
                return (
                    ToolCall(
                        id=self._next_id(),
                        name="find_tools",
                        input={"query": op.tool_name.replace("_", " ")},
                    ),
                    False,
                )
            return ToolCall(id=self._next_id(), name=tool_name, input=params), True

        if self.arm_key == "B":
            return (
                ToolCall(id=self._next_id(), name=op.domain, input={"action": op.action, **params}),
                True,
            )

        if self.arm_key == "C":
            if op.name not in self._described:
                self._described.add(op.name)
                return (
                    ToolCall(id=self._next_id(), name="describe", input={"path": f"ops/{op.name}"}),
                    False,
                )
            return (
                ToolCall(
                    id=self._next_id(), name="apply", input={"op": op.name, "items": [params]}
                ),
                True,
            )

        return None, True  # pragma: no cover

    # -- helpers ------------------------------------------------------------

    def _resolve(self, step: Step) -> dict:
        from backend.evals.tool_surface import mock_scripts

        if step.resolver:
            return mock_scripts.resolve(step.resolver, self.ref, self._results, step.params)
        return dict(step.params)

    def _absorb_results(self, messages: list[dict]) -> None:
        self._results = []
        for message in messages:
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    try:
                        self._results.append(json.loads(block.get("content") or "{}"))
                    except Exception:
                        self._results.append({})

    def _render_text(self, template: str) -> str:
        from backend.evals.tool_surface import mock_scripts

        return mock_scripts.render_text(template, self.ref, self._results)

    def _next_id(self) -> str:
        return f"mock_{self.task_id}_{self._index}"


# ── Token estimation (mock only) ─────────────────────────────────────────────


def _estimate(system: str, messages: list[dict], tools: list[dict]) -> int:
    blob = system + json.dumps(messages, default=str) + json.dumps(tools, default=str)
    return _estimate_text(blob)


def _estimate_text(text: str) -> int:
    """~4 chars per token. Flagged ``estimated=True`` so reports never present these
    as measured usage."""
    return max(1, len(text or "") // 4)
