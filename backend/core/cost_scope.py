"""Per-operation LLM cost attribution — a ContextVar collector under the global ledger.

``ai.cost_tracker.record`` is the ONE choke every provider funnels usage through; it also
feeds the collector active on the current context (if any). A service that wants to know
what one operation cost (a statement parse, an email ingest…) wraps it in
``collect_llm_cost()`` and reads the totals — without importing ``ai`` (Contract 1) and
without racing concurrent turns (ContextVar = per-task isolation).

    with collect_llm_cost() as spent:
        ...  # any number of seam completions
    print(spent["calls"], spent["cost_usd"])
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

_collector: ContextVar[Optional[dict]] = ContextVar("llm_cost_collector", default=None)


@contextmanager
def collect_llm_cost() -> Iterator[dict]:
    """Collect every LLM call recorded while the block runs on THIS context."""
    spent = {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}
    token = _collector.set(spent)
    try:
        yield spent
    finally:
        _collector.reset(token)


def add_llm_cost(cost_usd: float, input_tokens: int = 0, output_tokens: int = 0) -> None:
    """Feed the active collector, if any. Called by the cost ledger's record choke."""
    spent = _collector.get()
    if spent is None:
        return
    spent["calls"] += 1
    spent["cost_usd"] += float(cost_usd or 0.0)
    spent["input_tokens"] += int(input_tokens or 0)
    spent["output_tokens"] += int(output_tokens or 0)
