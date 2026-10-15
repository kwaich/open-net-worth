"""Model pricing for the cost estimate.

Reads the repo's OWN vendored price map (``backend/ai/models/model_prices.json``,
refreshed by ``make refresh-prices``) so the harness can never drift from what the app
itself bills against. Falls back to a small hardcoded table only if that lookup fails.

Rates are USD per MILLION tokens.
"""

from __future__ import annotations

import json
from pathlib import Path

_PRICES_JSON = Path(__file__).resolve().parents[2] / "ai" / "models" / "model_prices.json"

# Fallback only — used when the vendored map has no entry for the model.
_FALLBACK: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
}

_DEFAULT = (3.00, 15.00)


def price_for(model: str) -> tuple[float, float]:
    """Return (input_usd_per_mtok, output_usd_per_mtok) for ``model``."""
    bare = (model or "").split(":")[-1].strip()

    try:
        table = json.loads(_PRICES_JSON.read_text(encoding="utf-8"))
    except Exception:
        table = {}

    for key in (bare, f"anthropic.{bare}", f"anthropic/{bare}"):
        entry = table.get(key)
        if isinstance(entry, dict) and entry.get("input_cost_per_token"):
            return (
                float(entry["input_cost_per_token"]) * 1e6,
                float(entry.get("output_cost_per_token") or 0.0) * 1e6,
            )

    return _FALLBACK.get(bare, _DEFAULT)
