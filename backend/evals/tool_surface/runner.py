"""The runner: one task × one arm × one repetition, scored on the resulting data.

Per run it rebuilds the fixture from scratch (so runs cannot contaminate each other),
snapshots the book, drives a tool-calling loop against the arm, snapshots again, and
hands the diff to the task's check.

Metrics recorded per run, all of them things the decision actually depends on:

  intended / unintended / value_ok   outcome axes (see scorer.py)
  op_selection_correct               did it reach the ops a competent run needs
  round_trips                        model turns — the latency and cost driver
  tool_calls / discovery_calls       work vs. overhead
  input_tokens / output_tokens       measured from the API (estimated in mock mode)
  wall_seconds
  tool_errors / recovered            did a tool error get fixed within the run
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.evals.tool_surface import (
    bootstrap,  # noqa: F401
    fixture,
    scorer,
)
from backend.evals.tool_surface.arms import build_arm
from backend.evals.tool_surface.model_client import AnthropicClient, MockClient, ModelClient
from backend.evals.tool_surface.tasks import Task

MAX_ROUND_TRIPS = 12

SHARED_SYSTEM_PROMPT = (
    "You are a personal finance assistant embedded in the user's private wealth app. "
    "You manage their ledger, budget and investments by calling tools.\n\n"
    "Rules:\n"
    "- Today's date is {today}. The user's base currency is {base}.\n"
    "- Never invent an account, category or instrument that does not exist. If the user "
    "names something you cannot find, say so and ask rather than substituting something else.\n"
    "- If a request is missing something you need in order to write an accurate record "
    "(most often which account it belongs to), ask instead of guessing.\n"
    "- Prefer reading current state before writing, so your write is based on real values.\n"
    "- When the user asks a question, answer it with the specific number, not a description.\n"
)


@dataclass
class CostRecord:
    """Everything needed to price one run, in the units the provider bills in."""

    api_calls: int = 0
    uncached_input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    estimated: bool = False

    @property
    def total_input_tokens(self) -> int:
        return self.uncached_input_tokens + self.cache_write_tokens + self.cache_read_tokens


@dataclass
class RunRecord:
    task_id: str
    task_category: str
    arm: str
    arm_label: str
    model: str
    cost: CostRecord
    repetition: int
    intended: bool
    unintended: list[str]
    value_ok: bool | None
    reasons: list[str]
    op_selection_correct: bool
    ops_used: list[str]
    round_trips: int
    tool_calls: int
    discovery_calls: int
    tool_errors: int
    recovered_errors: int
    input_tokens: int
    output_tokens: int
    tokens_estimated: bool
    wall_seconds: float
    final_text: str
    error: str | None = None
    trace: list[dict] = field(default_factory=list)

    #: True when ``value_ok`` gates success (read tasks) — see scorer.CheckResult.
    value_gates_success: bool = False

    @property
    def success(self) -> bool:
        """A run succeeds only if the right thing landed, nothing else did, and — for a
        read — the figure it reported matches what the service computes."""
        if not (self.intended and not self.unintended and self.error is None):
            return False
        if self.value_gates_success and self.value_ok is False:
            return False
        return True

    @property
    def wrong_value(self) -> bool:
        """A read whose stated figure disagrees with the service-computed value."""
        return self.value_ok is False


def run_one(
    task: Task,
    spec: Any,
    repetition: int,
    client_factory: Any,
    decoys: list[str] | None = None,
    verbose: bool = False,
) -> RunRecord:
    arm_key = spec.key
    started = time.time()
    ref = fixture.build()
    arm = build_arm(spec, decoys=decoys)
    client: ModelClient = client_factory(spec, task.id, ref)

    before = scorer.take_snapshot(fixture.BUDGET_MONTH)

    system = (
        SHARED_SYSTEM_PROMPT.format(today=fixture.TODAY, base=fixture.BASE_CURRENCY)
        + "\n"
        + arm.system_suffix()
    )
    messages: list[dict] = [{"role": "user", "content": task.prompt}]

    round_trips = 0
    tool_calls = 0
    discovery_calls = 0
    tool_errors = 0
    recovered = 0
    cost = CostRecord()
    final_text = ""
    failure: str | None = None
    trace: list[dict] = []
    ops_used: list[str] = []
    pending_error_ops: set[str] = set()

    try:
        while round_trips < MAX_ROUND_TRIPS:
            round_trips += 1
            response = client.run_turn(system, messages, arm.tools())
            cost.api_calls += 1
            cost.uncached_input_tokens += response.input_tokens
            cost.output_tokens += response.output_tokens
            cost.cache_write_tokens += response.cache_write_tokens
            cost.cache_write_5m_tokens += response.cache_write_5m_tokens
            cost.cache_write_1h_tokens += response.cache_write_1h_tokens
            cost.cache_read_tokens += response.cache_read_tokens
            cost.estimated = cost.estimated or response.estimated

            if response.text:
                final_text = response.text

            if not response.tool_calls:
                break

            assistant_content: list[dict] = []
            if response.text:
                assistant_content.append({"type": "text", "text": response.text})
            for call in response.tool_calls:
                assistant_content.append(
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
                )
            messages.append({"role": "assistant", "content": assistant_content})

            results: list[dict] = []
            for call in response.tool_calls:
                tool_calls += 1
                result_text, dispatch = arm.dispatch(call.name, call.input)
                if dispatch.is_discovery:
                    discovery_calls += 1
                if dispatch.op:
                    ops_used.append(dispatch.op)
                if not dispatch.ok:
                    tool_errors += 1
                    pending_error_ops.add(dispatch.op or dispatch.tool)
                elif dispatch.op and dispatch.op in pending_error_ops:
                    recovered += 1
                    pending_error_ops.discard(dispatch.op)

                trace.append(
                    {
                        "tool": dispatch.tool,
                        "op": dispatch.op,
                        "ok": dispatch.ok,
                        "error": dispatch.error,
                        "input": call.input,
                    }
                )
                if verbose:
                    status = "ok" if dispatch.ok else f"ERR {dispatch.error}"
                    print(f"      [{arm_key}] {dispatch.tool} -> {status}")

                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": _apply_result_budget(result_text, spec.result_budget),
                        "is_error": not dispatch.ok,
                    }
                )
            messages.append({"role": "user", "content": results})
    except Exception as exc:  # noqa: BLE001 - a broken run is data, not a crash
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        close = getattr(arm, "close", None)
        if callable(close):
            close()

    cost.usd = compute_cost_usd(spec.model, cost)

    after = scorer.take_snapshot(fixture.BUDGET_MONTH)
    delta = scorer.diff(before, after)

    try:
        check = task.check(delta, ref, final_text)
    except Exception as exc:  # noqa: BLE001 - a broken check must be visible, not fatal
        check = scorer.CheckResult(
            intended=False, reasons=[f"check raised {type(exc).__name__}: {exc}"]
        )

    unintended = scorer.unintended_changes(delta, check)
    selection_ok = _selection_ok(task, ops_used)

    return RunRecord(
        task_id=task.id,
        task_category=task.category,
        arm=arm_key,
        arm_label=spec.label(),
        model=spec.model,
        cost=cost,
        repetition=repetition,
        intended=check.intended,
        unintended=unintended,
        value_ok=check.value_ok,
        value_gates_success=check.value_gates_success,
        reasons=check.reasons,
        op_selection_correct=selection_ok,
        ops_used=sorted(set(ops_used)),
        round_trips=round_trips,
        tool_calls=tool_calls,
        discovery_calls=discovery_calls,
        tool_errors=tool_errors,
        recovered_errors=recovered,
        input_tokens=cost.total_input_tokens,
        output_tokens=cost.output_tokens,
        tokens_estimated=cost.estimated,
        wall_seconds=round(time.time() - started, 2),
        final_text=final_text,
        error=failure,
        trace=trace,
    )


_TRUNCATION_MARKER = '... [truncated by the harness result_budget; the full result was longer]"}'


def _apply_result_budget(text: str, budget: int) -> str:
    """Truncate a tool result to ``budget`` characters (0 = unlimited).

    The truncation is ANNOUNCED. That is deliberate: the failure mode this lever is
    meant to expose is the model answering confidently from a partial result, and an
    announced cut is the charitable version — if correctness still drops with the marker
    present, it will drop harder with a silent one.
    """
    if budget <= 0 or len(text) <= budget:
        return text
    return text[:budget] + _TRUNCATION_MARKER


def compute_cost_usd(model: str, cost: "CostRecord") -> float:
    """Price a run through the app's OWN pricing module.

    Deliberately calls ``backend.ai.models.pricing`` rather than reimplementing the
    rates, so when another lane corrects a discount constant these numbers correct
    themselves on the next run with no change here.
    """
    try:
        from backend.ai.models.pricing import cost_usd_anthropic

        return round(
            cost_usd_anthropic(
                model,
                input_tokens=cost.uncached_input_tokens,
                output_tokens=cost.output_tokens,
                cache_creation_tokens=cost.cache_write_tokens,
                cache_read_tokens=cost.cache_read_tokens,
                cache_creation_5m_tokens=cost.cache_write_5m_tokens,
                cache_creation_1h_tokens=cost.cache_write_1h_tokens,
            ),
            6,
        )
    except Exception:
        from backend.evals.tool_surface.pricing import price_for

        rate_in, rate_out = price_for(model)
        return round(
            (cost.total_input_tokens / 1e6) * rate_in + (cost.output_tokens / 1e6) * rate_out, 6
        )


def _selection_ok(task: Task, ops_used: list[str]) -> bool:
    """Did the run reach the capabilities a competent run needs?

    For a near-miss the correct selection is to call NO write op — reads are fine and
    expected (checking whether the account exists is exactly right).
    """
    used = set(ops_used)
    if task.is_near_miss:
        from backend.evals.tool_surface import ops as ops_module

        return not any((ops_module.get_op(o) or None) and ops_module.get_op(o).writes for o in used)
    return task.expected_ops.issubset(used)


def make_mock_factory(persona: str):
    def factory(spec: Any, task_id: str, ref: dict) -> ModelClient:
        return MockClient(persona=persona, arm_key=spec.surface, task_id=task_id, ref=ref)

    return factory


def make_anthropic_factory():
    """One real client per MODEL, so a model-tier arm swaps the client transparently."""
    cache: dict[str, ModelClient] = {}

    def factory(spec: Any, task_id: str, ref: dict) -> ModelClient:
        if spec.model not in cache:
            cache[spec.model] = AnthropicClient(model=spec.model)
        return cache[spec.model]

    return factory


def records_to_json(records: list[RunRecord]) -> str:
    return json.dumps([asdict(r) for r in records], indent=2, default=str)
