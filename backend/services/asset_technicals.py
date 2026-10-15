"""Analysis-tab Technicals: indicator rows + hash-gated AI paragraph.

Indicator math lives in ``indicators``; this module loads a daily series, builds
rows/overlays, and optionally narrates them via ``core.llm_seam`` (services must
not import ``ai``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Literal

from backend.db.asset_brief_store import AssetBriefStore
from backend.db.instrument_store import InstrumentStore
from backend.services import indicators
from backend.services.asset_detail import price_series

_log = logging.getLogger(__name__)

_TECH_TIMEFRAMES = ("1M", "6M", "1Y", "5Y")
_DAILY_BASIS = "1Y"  # rows + AI always from daily 1Y bars
PROMPT_VERSION = "v3-grounded"
_LEAN_BUY = 0.25
_LEAN_SELL = -0.25
# Digits / signed decimals / percents the model is allowed to invent around.
_NUM_TOKEN = re.compile(r"[+-]?\d+(?:\.\d+)?%?")

Lean = Literal["leans_buy", "leans_sell", "mixed"]


def lean_from_scores(rows: list[dict]) -> Lean:
    """Average bipolar scores → leans_buy / leans_sell / mixed."""
    scores: list[float] = []
    for r in rows:
        try:
            scores.append(float(r["score"]))
        except (TypeError, ValueError, KeyError):
            continue
    if not scores:
        return "mixed"
    mean = sum(scores) / len(scores)
    if mean >= _LEAN_BUY:
        return "leans_buy"
    if mean <= _LEAN_SELL:
        return "leans_sell"
    return "mixed"


def lean_phrase(lean: Lean) -> str:
    return {"leans_buy": "leans buy", "leans_sell": "leans sell", "mixed": "mixed"}[lean]


def lean_label(lean: Lean) -> str:
    return {"leans_buy": "Leans buy", "leans_sell": "Leans sell", "mixed": "Mixed"}[lean]


def _bundle_hash(rows: list[dict]) -> str:
    """Stable hash of scores (rounded) + prompt version so voice changes re-fire."""
    payload = {
        "v": PROMPT_VERSION,
        "rows": [(r["id"], round(float(r["score"]), 2), r.get("raw_display")) for r in rows],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _closes_from_points(points: list[dict]) -> tuple[list[float], list[str]]:
    closes: list[float] = []
    dates: list[str] = []
    for p in points:
        price = p.get("close") if p.get("close") is not None else p.get("price")
        if price is None:
            continue
        try:
            closes.append(float(price))
        except (TypeError, ValueError):
            continue
        dates.append(str(p.get("date") or ""))
    return closes, dates


def compute_technicals(instrument_id: str, timeframe: str = "1Y") -> dict[str, Any]:
    """Sync snapshot: rows + overlays + cached narrative (if hash still matches).

    Does NOT call the LLM — use ``ensure_technicals_narrative`` for that. The chart
    timeframe may differ from the daily basis used for rows (always 1Y daily).
    """
    tf = timeframe if timeframe in _TECH_TIMEFRAMES else _DAILY_BASIS
    empty_lean: Lean = "mixed"
    if not InstrumentStore().find_by_id(instrument_id):
        return {
            "currency": "USD",
            "timeframe": tf,
            "rows": [],
            "overlays": {},
            "points": [],
            "narrative": None,
            "bundle_hash": "",
            "lean": empty_lean,
        }

    # Rows always from daily 1Y (SMA200 needs ~200 bars). Chart overlays are rebuilt
    # from the displayed timeframe's closes so MACD/RSI/Bollinger dates align with price.
    basis = price_series(instrument_id, _DAILY_BASIS)
    closes, dates = _closes_from_points(basis.get("points") or [])
    rows, _basis_overlays = indicators.build_rows(closes, dates)

    chart = price_series(instrument_id, tf) if tf != _DAILY_BASIS else basis
    chart_points = chart.get("points") or []
    if tf == _DAILY_BASIS:
        overlays = _basis_overlays
    else:
        c_closes, c_dates = _closes_from_points(chart_points)
        _, overlays = indicators.build_rows(c_closes, c_dates)

    lean = lean_from_scores(rows) if rows else empty_lean
    bhash = _bundle_hash(rows) if rows else ""
    narrative = None
    if bhash:
        # DB-backed (AssetBriefStore), same row as key_issues/portfolio_narrative — survives
        # a restart, unlike the old in-process dict. Hash-gated, not TTL'd: identical scores
        # mean nothing new to say regardless of elapsed time.
        brief = AssetBriefStore().get(instrument_id) or {}
        if brief.get("technicals_bundle_hash") == bhash:
            narrative = brief.get("technicals_narrative")

    return {
        "currency": chart.get("currency") or basis.get("currency") or "USD",
        "timeframe": tf,
        "rows": rows,
        "overlays": overlays,
        "points": [
            {
                "date": p.get("date"),
                "price": p.get("close") if p.get("close") is not None else p.get("price"),
            }
            for p in chart_points
            if (p.get("close") is not None or p.get("price") is not None)
        ],
        "narrative": narrative,
        "bundle_hash": bhash,
        "lean": lean,
        "ohlc_available": bool(chart.get("ohlc_available")),
    }


async def ensure_technicals_narrative(instrument_id: str, snapshot: dict | None = None) -> dict:
    """Return a technicals snapshot, synthesizing a paragraph when the hash moved.

    Grounding: the model sees only the six rows plus a deterministic lean hint.
    On failure or advice-shaped contradiction, ``narrative`` stays None / prior.
    """
    snap = snapshot or compute_technicals(instrument_id)
    rows = snap.get("rows") or []
    bhash = snap.get("bundle_hash") or ""
    if not rows or not bhash:
        return snap
    if snap.get("narrative"):
        return snap

    paragraph = await _synthesize(rows)
    if paragraph:
        store = AssetBriefStore()
        prior = store.get(instrument_id) or {}
        store.upsert_brief(
            {
                **prior,
                "instrument_id": instrument_id,
                "technicals_bundle_hash": bhash,
                "technicals_narrative": paragraph,
            }
        )
        snap = {**snap, "narrative": paragraph}
    return snap


def _rows_contradict_paragraph(rows: list[dict], text: str) -> bool:
    """Refuse personal-advice phrasing; allow 'leans buy/sell' / 'mixed'."""
    del rows  # signature kept for call-site compatibility
    lower = text.lower()
    banned = (
        "you should buy",
        "you should sell",
        "recommend buying",
        "recommend selling",
        "recommendation is buy",
        "recommendation is sell",
        "rating is buy",
        "rating is sell",
        "consensus is buy",
        "consensus is sell",
    )
    return any(b in lower for b in banned)


# Window lengths the model may mention in plain glosses ("50-day average").
_GLOSS_PERIODS = ("14", "20", "50", "200")


def _allowed_figure_tokens(rows: list[dict]) -> set[str]:
    """Normalize every raw_display into comparable numeric tokens."""
    allowed: set[str] = {_norm_num_token(p) for p in _GLOSS_PERIODS}
    for r in rows:
        disp = str(r.get("raw_display") or "")
        for m in _NUM_TOKEN.finditer(disp):
            allowed.add(_norm_num_token(m.group(0)))
        # Also allow the rounded score the model may echo (±0.xx).
        try:
            sc = float(r["score"])
            allowed.add(_norm_num_token(f"{sc:+.2f}"))
            allowed.add(_norm_num_token(f"{sc:.2f}"))
        except (TypeError, ValueError, KeyError):
            pass
    return allowed


def _norm_num_token(tok: str) -> str:
    t = tok.strip().replace(",", "")
    pct = t.endswith("%")
    if pct:
        t = t[:-1]
    try:
        v = float(t)
    except ValueError:
        return tok.lower()
    # Collapse +0 / 0 / 0.0 forms; keep one decimal place when fractional.
    if v == int(v) and abs(v) < 1e9:
        core = str(int(v))
    else:
        core = f"{v:.4f}".rstrip("0").rstrip(".")
    if pct:
        core += "%"
    # Sign-insensitive match: "+0.99" and "0.99" both normalize without leading +.
    if core.startswith("+"):
        core = core[1:]
    return core.lower()


def _paragraph_invents_figures(rows: list[dict], text: str) -> bool:
    """True when the paragraph cites a number not present in allowed raw displays."""
    allowed = _allowed_figure_tokens(rows)
    if not allowed:
        return False
    for m in _NUM_TOKEN.finditer(text):
        tok = m.group(0)
        # Ignore lone small integers that are sentence scaffolding (e.g. "2–4").
        if re.fullmatch(r"[+-]?\d{1,2}", tok):
            continue
        if _norm_num_token(tok) not in allowed:
            return True
    return False


async def _synthesize(rows: list[dict]) -> str | None:
    from backend.core import llm_seam

    lean = lean_from_scores(rows)
    phrase = lean_phrase(lean)
    lines = [
        f"- {r['label']}: score={r['score']:+.2f} (sell=-1…buy=+1), raw={r['raw_display']}"
        for r in rows
    ]
    allowed = ", ".join(f"{r['label']}={r['raw_display']}" for r in rows if r.get("raw_display"))
    prompt = (
        "You narrate the technical indicator ROWS below for a wealth-manager UI.\n"
        "Rules:\n"
        "- 2–4 short sentences, plain English.\n"
        f'- Open or close with this lean (required): "On balance this {phrase}."\n'
        "- Cite 2–4 concrete numbers ONLY from the Allowed figures list below — "
        "copy those strings exactly; do not invent or recalculate.\n"
        '- Prefer everyday glosses: e.g. "price is 0.9% below its 50-day average" '
        'instead of bare jargon; "relative strength (RSI)", "MACD histogram", '
        '"Bollinger band position".\n'
        "- Say which readings favor buying vs selling when they disagree.\n"
        '- Do NOT say "you should buy/sell" or give personal portfolio advice.\n'
        "- Do NOT mention news, earnings, fundamentals, or the reader's position/lots.\n"
        '- Return JSON: {"paragraph": "..."}\n\n'
        f"Required lean: {phrase}\n"
        f"Allowed figures (copy exactly): {allowed}\n"
        "Rows:\n" + "\n".join(lines)
    )
    schema = {
        "type": "object",
        "properties": {"paragraph": {"type": "string"}},
        "required": ["paragraph"],
        "additionalProperties": False,
    }
    try:
        result = await llm_seam.complete_json(
            prompt,
            system=(
                "You explain mechanical technical indicator scores in plain English. "
                "State the required lean (buy / sell / mixed) as what the readings imply, "
                "not as personal advice. Only cite numbers from the Allowed figures list."
            ),
            max_tokens=400,
            source="technicals",
            schema=schema,
        )
    except Exception:
        _log.warning("technicals: narrative synthesis failed", exc_info=True)
        return None
    if not isinstance(result, dict):
        return None
    paragraph = (result.get("paragraph") or "").strip()
    if not paragraph or len(paragraph) > 800:
        return None
    if _rows_contradict_paragraph(rows, paragraph):
        return None
    if _paragraph_invents_figures(rows, paragraph):
        _log.info("technicals: narrative rejected — invented figures")
        return None
    return paragraph
