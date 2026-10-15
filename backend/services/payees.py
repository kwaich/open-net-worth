"""
payees_service — derive-only payee REPORTING (spend-by-payee analytics).

History: this shipped first as the cheap interim BEFORE the entity (docs/rfc/payee.md).
P4-PAYEE phase 1 (.claude/rfcs/phase4/payee-registry.md) then ADDED the first-class
entity — ``db/payee_store.py`` (``payees.json``), a ``payee_id`` on the transaction
resolved at the ``create_transaction`` choke point, and ``services/payee_registry.py``
for the entity logic. This module stays the SPEND-REPORT view (distinct payees with
aggregate spend, recomputed into base) and is unchanged by that work: ``_normalize_payee``
remains the single normalizer the registry, subscriptions, and this report all share.

What this delivers today, with zero data-model risk:
  * **spending-by-payee reporting** — distinct normalized payees with aggregate spend,
    transaction count, last-seen date, and the most-common category; and
  * the data behind the **"create rule from payee"** one-click in the Rules UI (the
    rules engine remains the "default category per payee" mechanism — no new write path).

This module is PURE math with injectable inputs (``txns``/``base``/``rates``) so it is
deterministic and network-free in tests. It reads only ``ledger`` (txns +
``txn_base_amount``); it imports nothing from ``api/`` (respects api/ai → services →
db → core). It is compute-only — there is no write path here.

Money rule: every amount is recomputed into the CURRENT base via
``ledger.txn_base_amount(t, base, rates)`` — never a stored ``base_amount`` (mirrors
budgets/cashflow/subscriptions). A base-currency change re-values ``total_base`` on read.

``_normalize_payee`` is the SINGLE source of truth for payee normalization in this
codebase. ``services.subscriptions`` imports and delegates to this function so both
features cluster descriptions identically.
"""

from __future__ import annotations

import re
from collections import Counter

from backend.db.transaction_store import txn_text
from backend.services import ledger as svc

# Kinds that carry a meaningful payee/merchant. Transfers/adjustments are inter-account
# plumbing, not a payee, so they are excluded (same spirit as the rules-engine invariant
# that transfers/adjustments never get an expense category).
_PAYEE_KINDS = {"expense", "income"}

# ── Card-processor / payment-network prefix noise ──────────────────────────────────
#
# Bank statement descriptions frequently prefix the merchant name with the name of the
# card-processing network or point-of-sale platform (Square, Toast, PayPal, Stripe, etc.).
# These prefixes add no signal about the actual merchant and actively harm grouping (two
# charges at the same coffee shop — one swiped via Square, one via their own terminal —
# would end up in different buckets without this step).
#
# Pattern: ``PROCESSOR_PREFIX * MERCHANT_NAME`` or ``PROCESSOR_PREFIX* MERCHANT_NAME``.
# The asterisk is the standard delimiter in US/UK bank feed normalizations; some use a
# space-only separator (PAYPAL, VENMO, AMZN MKTP).
#
# This list targets HIGH-FREQUENCY false-split prefixes.  Unknown prefixes are harmless
# — the generic punctuation step later strips the ``*`` anyway, so the name lands with
# the processor token included (slightly noisier but never wrong).
_PROCESSOR_PREFIXES = (
    r"sq\s*\*",  # Square POS
    r"tst\s*\*",  # Toast POS
    r"sp\s*\*",  # Sumup / generic
    r"pp\s*\*",  # PayPal abbreviated
    r"paypal\s*\*",  # PayPal full
    r"venmo\s*",  # Venmo (space-separated)
    r"amzn\s+mktp\s*\*",  # Amazon Marketplace
    r"amazon\s+mktp\s*\*",
    r"amzn\s*\*",  # Amazon abbreviated
    r"lvnm\s*\*",  # Livenet / misc
    r"dd\s*\*",  # DoorDash
    r"uber\s*\*",  # Uber (trip/eats prefix form)
    r"lyft\s*\*",  # Lyft
    r"grab\s*\*",  # Grab (SEA ride-hailing/delivery)
    r"fp\s*\*",  # Kestrel Eats (SEA/ME food delivery)
    r"gojek\s*\*",  # Gojek (SEA)
    r"wholefds\s*\*",  # Whole Foods via Amazon
    r"prime\s*\*",  # Amazon Prime services
    r"in\s+\*",  # "IN *<name>" (common US bank encoding for in-store)
)
# Compile once into a single alternation anchored at the string start.
_PROCESSOR_PREFIX_RE = re.compile(r"^(?:" + "|".join(_PROCESSOR_PREFIXES) + r")\s*", re.IGNORECASE)

# ── Known short-token variants → canonical expansion ───────────────────────────────
#
# A small, high-confidence alias table for abbreviations that appear in bank feeds and
# would otherwise form a distinct bucket from the expanded name.  Keyed on the
# WHOLE normalized token (after lowercase/digit-strip), mapped to its canonical
# replacement.  Absent a dedicated entity store (deferred to full entity model), keeping
# this list intentionally short prevents false merges.
_VARIANT_MAP: dict[str, str] = {
    "amzn": "amazon",
    "mcd": "mcdonalds",
    "mcds": "mcdonalds",
    "wm": "walmart",  # "WM SUPERCENTER" prefix in US feeds
    "wholefds": "whole foods",
}
# Match isolated tokens from the variant map (whole-word boundary on both sides).
_VARIANT_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in _VARIANT_MAP) + r")\b")


def _normalize_payee(description: str) -> str:
    """Deterministic payee normalization for grouping.

    Steps applied in order:
      1. Lowercase + strip surrounding whitespace.
      2. Strip card-processor prefix noise (``SQ *``, ``TST*``, ``PAYPAL *``, etc.)
         so "SQ *Blue Bottle" and "Blue Bottle Coffee" map to the same key.
      3. Drop ``#tag`` chips embedded in the description.
      4. Drop ISO-style date tokens (``2026-05-01``, ``05/01/26``).
      5. Apply the known-variant alias table (``amzn`` → ``amazon``, etc.).
      6. Strip remaining punctuation to spaces, collapse whitespace.

    Numbers (digits) are intentionally preserved. Long digit runs (phone numbers,
    account numbers, PayNow recipients) ARE the payee identity for transfer
    descriptions and must not be stripped. The trade-off: merchant descriptions
    that include order/reference IDs (e.g. "AMZN MKTP *123456789") produce a
    per-order payee entity. Use the Rules engine for merchant categorization and
    the Payees panel to merge any fragmented entities.

    This is the SINGLE source of truth for payee normalization in this codebase.
    Pure/deterministic — no LLM, no network.
    """
    s = (description or "").lower().strip()
    if not s:
        return ""
    # Step 2: strip leading card-processor prefix (e.g. "sq *", "paypal *").
    s = _PROCESSOR_PREFIX_RE.sub("", s).strip()
    if not s:
        return ""
    # Step 3: drop hashtag chips (the ledger renders tags as #chips inside descriptions).
    s = re.sub(r"#\w+", " ", s)
    # Step 4: drop ISO-style date tokens (e.g. "2026-05-01", "05/01/26").
    s = re.sub(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", " ", s)
    s = re.sub(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b", " ", s)
    # Step 5: apply known-variant alias substitutions.
    s = _VARIANT_RE.sub(lambda m: _VARIANT_MAP[m.group(1)], s)
    # Step 6: strip punctuation to spaces, then collapse whitespace.
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _payee_txns(txns: list[dict]) -> list[dict]:
    """Non-deleted expense/income transactions only (the kinds with a real payee)."""
    return [t for t in txns if not t.get("deleted") and t.get("kind") in _PAYEE_KINDS]


def list_payees(
    *,
    txns: list[dict] | None = None,
    base: str | None = None,
    rates: dict | None = None,
) -> dict:
    """Derive distinct payees from the transaction ledger by normalizing ``description``.

    PURE — inject ``txns``/``base``/``rates`` for deterministic tests; otherwise read
    live (incl. the FX cache). Groups non-deleted expense/income txns by the normalized
    payee key and returns, per payee:
        payee           — display name (the most-recent raw description in the group)
        payee_key       — the normalized grouping key (what a "create rule" prefills)
        txn_count       — number of transactions in the group
        total_base      — Σ txn_base_amount in the CURRENT base (recomputed, never stored)
        last_seen       — most-recent transaction date (ISO 'YYYY-MM-DD')
        sample_category — most-common non-empty category in the group ("" if none)

    Sorted by ``total_base`` descending (biggest payees first). Empty book → {payees: []};
    never raises on a fresh instance.
    """
    base = (base or svc.base_currency()).upper()
    if rates is None:
        from backend.services.fx_cache import load_fx_cache

        rates = load_fx_cache()
    if txns is None:
        txns = svc.transactions.all()

    # Accumulate per-key stats in one pass.
    groups: dict[str, dict] = {}
    for t in _payee_txns(txns):
        key = _normalize_payee(txn_text(t))
        if not key:
            continue
        g = groups.get(key)
        if g is None:
            g = {
                "payee_key": key,
                "txn_count": 0,
                "total_base": 0.0,
                "last_seen": "",
                "_display": key,
                "_categories": Counter(),
            }
            groups[key] = g
        g["txn_count"] += 1
        g["total_base"] += svc.txn_base_amount(t, base, rates)
        d = (t.get("date") or "")[:10]
        if d and d >= g["last_seen"]:
            # Tie/most-recent: carry the raw description as the display name.
            g["last_seen"] = d
            g["_display"] = (txn_text(t) or key).strip() or key
        cat = t.get("category") or ""
        if cat:
            g["_categories"][cat] += 1

    rows: list[dict] = []
    for g in groups.values():
        cats = g.pop("_categories")
        sample_category = cats.most_common(1)[0][0] if cats else ""
        rows.append(
            {
                "payee": g.pop("_display"),
                "payee_key": g["payee_key"],
                "txn_count": g["txn_count"],
                "total_base": round(g["total_base"], 2),
                "last_seen": g["last_seen"],
                "sample_category": sample_category,
            }
        )

    rows.sort(key=lambda r: r["total_base"], reverse=True)
    return {"payees": rows, "base_currency": base, "count": len(rows)}
