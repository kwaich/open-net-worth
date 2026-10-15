"""Refresh the vendored model price map from LiteLLM's public data.

Downloads ``model_prices_and_context_window.json`` from the LiteLLM repo, keeps only
text-generation rows and the pricing fields we use, and writes a compact, diff-friendly
``backend/ai/models/model_prices.json`` (one model per line, sorted).

We borrow the DATA, not the library: this is a MAINTENANCE script run by hand
(``make refresh-prices``), NOT imported at runtime, so there is no ``litellm``
dependency and no network call in the request path. The URL is a fixed, trusted
constant (not user input), so the SSRF guard does not apply.

Usage:  python scripts/refresh_model_prices.py   (or: make refresh-prices)
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/{ref}/model_prices_and_context_window.json"
)
# A release pins one LiteLLM commit (ONW_LITELLM_REF) so every platform's build ships identical
# prices; by hand it follows main. Only a git ref is accepted — the host and path stay fixed.
_REF = os.environ.get("ONW_LITELLM_REF", "").strip() or "main"
if not re.fullmatch(r"[0-9A-Za-z._/-]{1,80}", _REF) or ".." in _REF:
    raise SystemExit(f"invalid ONW_LITELLM_REF: {_REF!r}")
# Only text-generation rows are useful for chat cost; drop image/audio/embedding/etc.
_TEXT_MODES = {"chat", "completion", "responses"}
# The pricing + capability fields we keep (compact). cache_read is retained for
# future use; the capability pair feeds the routing engine's candidate checks.
_KEEP = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
    "litellm_provider",
    "supports_function_calling",
    "max_input_tokens",
)
_OUT = Path(__file__).resolve().parent.parent / "backend" / "ai" / "models" / "model_prices.json"


def _filter(raw: dict) -> dict:
    out: dict = {}
    for name, entry in raw.items():
        if name == "sample_spec" or not isinstance(entry, dict):
            continue
        if entry.get("mode") not in _TEXT_MODES:
            continue
        if not isinstance(entry.get("input_cost_per_token"), (int, float)):
            continue
        out[name] = {k: entry[k] for k in _KEEP if k in entry}
    return out


def _dump(data: dict) -> str:
    # One model per line → readable git diffs, compact values.
    lines = [
        f"  {json.dumps(k)}: {json.dumps(v, separators=(',', ':'))}"
        for k, v in sorted(data.items())
    ]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def main() -> int:
    with urllib.request.urlopen(_URL.format(ref=_REF), timeout=60) as resp:  # noqa: S310 - fixed trusted URL
        raw = json.load(resp)
    out = _filter(raw)
    if not out:
        print("refresh aborted: upstream returned no usable text-model rows", file=sys.stderr)
        return 1
    _OUT.write_text(_dump(out), encoding="utf-8")
    print(f"wrote {len(out)} text-model prices -> {_OUT} ({_OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
