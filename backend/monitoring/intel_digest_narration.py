"""
intel_digest_narration — one-line LLM narration for the digest (interactive-only).

Wraps the deterministic intel_digest.build_digest() payload in a short one-line LLM
summary using the batch seam (max_tokens ~120). Interactive-only: headless callers must
force narrate=False (the API endpoint and the tool enforce this boundary).

Lives in monitoring/ (may import ai/llm_seam) — services/intel_digest.py must NOT import
this module (would be a services→monitoring edge, illegal under the dependency rule).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_NARRATION_MAX_TOKENS = 120
_NARRATION_SYSTEM = (
    "You are a concise financial briefing assistant. "
    "Given a portfolio digest, write ONE sentence summarising the key market backdrop and "
    "top portfolio driver for the day. Be factual and grounded in the numbers provided. "
    "Never fabricate figures. Maximum 25 words."
)


async def narrate(digest: dict) -> str | None:
    """Return a short one-sentence LLM narration for the digest, or None on failure.

    Async so it can be safely awaited inside APScheduler's running event loop
    (calling run_until_complete on an already-running loop raises RuntimeError).
    Best-effort — a failure returns None, never raises.
    Cost is logged best-effort.
    """
    try:
        from backend.core.llm_seam import LLMUnavailable, complete

        base = digest.get("base_currency", "")
        period = digest.get("period", "1M")
        backdrop = digest.get("market_backdrop") or {}
        bench = backdrop.get("benchmark")
        drivers = digest.get("return_drivers") or []
        movers = digest.get("top_movers") or []

        bench_line = ""
        if bench and bench.get("twr") and bench.get("data_quality", {}).get("sufficient"):
            pr = round((bench["twr"].get("period_return") or 0) * 100, 1)
            sym = bench.get("symbol") or "benchmark"
            bench_line = f"{sym} {period}: {'+' if pr >= 0 else ''}{pr}%"

        top_driver = ""
        if drivers:
            d = drivers[0]
            if not d.get("degraded") and d.get("contribution_base") is not None:
                cb = d["contribution_base"]
                sign = "+" if cb >= 0 else ""
                top_driver = f"{d.get('label', '')} {sign}{base} {round(cb):,}"

        top_mover_line = ""
        if movers:
            m = movers[0]
            sign = "+" if float(m.get("day_change_pct") or 0) >= 0 else ""
            top_mover_line = (
                f"{m.get('label', '')} {sign}{round(float(m.get('day_change_pct', 0)), 1)}%"
            )

        context_parts = [p for p in [bench_line, top_driver, top_mover_line] if p]
        if not context_parts:
            return None

        prompt = (
            f"Portfolio base currency: {base}. Period: {period}.\n"
            + "\n".join(context_parts)
            + "\n\nWrite ONE sentence (max 25 words) summarising what matters today."
        )

        result = await complete(
            prompt,
            system=_NARRATION_SYSTEM,
            max_tokens=_NARRATION_MAX_TOKENS,
            source="intel_digest",
            effort="none",  # one-sentence digest line — reasoning is pure cost
        )
        narration = (result or "").strip()
        if not narration:
            return None

        # NOTE: no cost record is written here. ``complete()`` above already books the
        # narration's real cost under source="intel_digest" (real model, real tokens, real
        # cost). A second record here (removed) wrote a phantom $0 row with a fake
        # model="narration" and a hardcoded 120-token cap — inventing a bogus model in the
        # "By model" breakdown and double-counting the intel_digest turn/tokens.
        return narration

    except LLMUnavailable:
        return None
    except Exception:
        logger.debug("intel_digest_narration: narrate failed", exc_info=True)
        return None
