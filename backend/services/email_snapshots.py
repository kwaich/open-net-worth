"""Email provenance snapshots (EM1) — persists the email content the
email-transaction-logger agent actually read at fetch time, so a transaction booked from
email can be cross-checked against its source (docs/qa/acceptance/email-source-snapshot.md).

Keyed by the SHORT internal id (``core.email_ids.internal_email_id`` of the raw Message-ID) —
the SAME id the model is shown and books as ``external_ref`` (ADR 2026-07-20, refined). The raw
Message-ID rides along as ``raw_message_id`` for the drawer/debuggability but is never the key and
is never shown to the model. So the ledger's ``external_ref`` (an internal id) looks the snapshot
up directly — no canonicalization dance at read time.

Three write paths, all best-effort (never allowed to fail their caller):
  - ``stash_snapshots`` — called from ``fetch_emails`` (full body) AND ``search_messages``
    (headers only — that row's ``body_text`` starts ``""``). Every fetched/searched email is
    stashed UNREFERENCED; per-row failures are swallowed (one bad row must not sink the batch).
  - ``stash_or_update`` — called from ``get_messages`` after a successful full-body fetch, to
    fill in ``body_text`` on a row ``search_messages`` created earlier (or insert a fresh one).
  - ``promote_for_email`` — called from the ledger write choke (``create_transaction`` /
    ``bulk_create_transactions``) right after a row books successfully. Flips the matching
    snapshot to ``referenced=True`` so it survives the unreferenced-row prune below.

Doubles as ``get_messages``' locator: a row's existing ``raw_message_id`` (see ``locate``) is
how ``get_messages`` turns an internal id back into something ``fetch_by_message_ids`` can
``UID SEARCH HEADER Message-ID`` for — the model itself never sees or handles it.

No re-verify / re-fetch tool, no attachment handling, no retention GC for promoted rows
(scope is deliberately cut — see the EM1 task spec)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.core.email_ids import internal_email_id
from backend.core.untrusted import sanitize_untrusted
from backend.db.email_snapshot_store import EmailSnapshotStore

logger = logging.getLogger(__name__)

# Module-level singleton (mirrors services/audit.py's `_txns = TransactionStore()`) — a
# fresh-per-call EmailSnapshotStore() would bypass a test's (or any future caller's)
# instance-level `_path` monkeypatch, since JsonStore isolation always targets a specific
# INSTANCE. (The transaction READ in snapshot_for_transaction() below reuses
# services.ledger's own `transactions` singleton instead of minting a second one — see there.)
_snapshots = EmailSnapshotStore()

# Bound on UNREFERENCED (never-booked) snapshots kept at stash time. An inbox scan can fetch
# far more mail than ever gets booked as a transaction, and an unreferenced row has no lasting
# evidentiary value once superseded by fresher fetches. A promoted (referenced=True) row is
# exempt — it backs a real booking and is kept forever (no GC in v1, artifacts precedent).
_UNREFERENCED_KEEP = 200

# The stored RAW Message-ID is attacker-influenced (a raw email header, RFC 5322 gives no hard
# length cap). The store KEY is now the fixed-width internal id, so this only caps the display-only
# raw field to keep the JSON file from bloating on a pathological header.
_MAX_RAW_ID_LEN = 512


def _legacy_canon(raw: str) -> str:
    """The PRE-FIX snapshot key: strip whitespace + one surrounding ``<``/``>`` pair (the old
    ``_canon_message_id`` form). Used ONLY for a cheap O(1) legacy lookup at stash time — a snapshot
    stashed before the internal-id scheme was keyed by this."""
    s = (raw or "").strip()
    if len(s) >= 2 and s[0] == "<" and s[-1] == ">":
        s = s[1:-1]
    return s


def _find_snapshot(internal_id: str, raw: str | None = None) -> dict | None:
    """Look up a stashed snapshot by the short internal id.

    Fast path (always): an exact ``get(internal_id)`` — every post-fix row is keyed by it.

    When the caller ALSO has the raw Message-ID (the STASH path — the common, per-fetched-email hot
    path), a second O(1) ``get(_legacy_canon(raw))`` catches a PRE-FIX row keyed by the old canonical
    Message-ID. If both miss, the email is genuinely new → return None WITHOUT the O(n) scan below
    (finding 3: the old unconditional rehash scan re-SHA-256'd every stored row on every new email,
    growing unboundedly since referenced rows are never pruned).

    Only the promote/drawer path (``raw is None`` — it has just the booked internal id) falls through
    to the O(n) rehash scan to resolve a pre-fix row keyed by its raw Message-ID. That path runs once
    per booked email / drawer open, not per fetched email, so its cost is bounded in practice."""
    row = _snapshots.get(internal_id)
    if row is not None:
        return row
    if raw is not None:
        return _snapshots.get(_legacy_canon(raw))
    for candidate in _snapshots.all():
        cand_raw = candidate.get("raw_message_id") or candidate.get("message_id") or ""
        if cand_raw and internal_email_id(cand_raw) == internal_id:
            return candidate
    return None


def stash_snapshots(emails: list[dict]) -> int:
    """Persist a snapshot of each fetched/searched email AS THE MODEL SAW IT, unreferenced,
    keyed by the short internal id (``internal_email_id`` of the raw Message-ID) — the same id
    the model books.

    ``emails`` is the raw ``imap_client.fetch_by_message_ids``/``search_headers`` shape:
    ``{uid, message_id, from, subject, date, date_time, body_text?}`` — where ``message_id`` is
    the RAW header (or the content-fallback identifier) and ``body_text`` is OPTIONAL: a
    ``search_headers`` result omits it entirely (header-only), and a missing key stores as
    ``""`` — the same "not yet hydrated" state ``get_messages`` fills in later via
    ``stash_or_update``. Sanitizes free-text fields (control-char strip, kept \\n/\\t) before
    writing — EXCEPT ``received_at``, which is already header-derived text in a strict format by
    the time it reaches here. Best-effort per row — one bad row is logged and skipped, never
    raised. Returns the number of rows actually stashed (excludes no-op re-stashes)."""
    now = datetime.now(timezone.utc).isoformat()
    stashed = 0
    for email in emails:
        try:
            raw = (email.get("message_id") or "").strip()
            if not raw:
                continue
            internal_id = internal_email_id(raw)
            # Pass ``raw`` so the dedup check stays O(1) (internal-id + legacy-canon lookups) and a
            # genuinely-new email never pays the O(n) rehash scan (finding 3).
            if _find_snapshot(internal_id, raw=raw) is not None:
                continue  # already stashed (possibly under a pre-fix raw key) — no-op
            row = {
                "message_id": internal_id,  # the store KEY = the short internal id the model books
                "raw_message_id": raw[:_MAX_RAW_ID_LEN],  # display/debug only; never the key
                "subject": sanitize_untrusted(email.get("subject") or ""),
                "from_addr": sanitize_untrusted(email.get("from") or ""),
                "received_at": email.get("date_time") or email.get("date") or "",
                "body_text": sanitize_untrusted(email.get("body_text") or ""),
                "referenced": False,
                "stashed_at": now,
            }
            _snapshots.stash(row)
            stashed += 1
        except Exception:
            logger.warning("email_snapshots: failed to stash one row", exc_info=True)
    _snapshots.prune_unreferenced(_UNREFERENCED_KEEP)
    return stashed


def locate(message_id: str) -> dict | None:
    """The raw stored row for ``message_id``, or None — used by ``get_messages`` to translate an
    internal id back into the ``raw_message_id`` it needs to relocate the message via
    ``fetch_by_message_ids``. NOT a provenance API — a caller that wants the drawer/audit view
    should go through ``snapshots_for_transaction`` instead."""
    if not message_id:
        return None
    return _find_snapshot(message_id)


def stash_or_update(message_id: str, fields: dict) -> None:
    """Insert a fresh snapshot for ``message_id``, or merge ``fields`` onto an existing one —
    used by ``get_messages`` after a successful full-body fetch, so it works whether or not a
    prior ``search_messages`` call already created a lightweight (body-less) row for this id.

    Merge, never replace (``EmailSnapshotStore.update_by_id``'s own contract): an existing row's
    ``referenced``/``stashed_at`` are left untouched. ``fields`` should NOT include either key —
    this function owns them on the insert path and a caller-supplied value would only matter
    there, where it is silently ignored in favor of the correct default (unreferenced, now)."""
    now = datetime.now(timezone.utc).isoformat()
    existing = _snapshots.get(message_id)
    if existing is None:
        _snapshots.stash(
            {**fields, "message_id": message_id, "referenced": False, "stashed_at": now}
        )
        return
    _snapshots.update_by_id(message_id, fields)


def promote(internal_id: str) -> bool:
    """Flip a stashed snapshot to referenced=True. Idempotent. Tolerant of a pre-fix stored row
    keyed by its raw Message-ID (see ``_find_snapshot``) — promotes the row under whatever key it's
    ACTUALLY stored as."""
    if not internal_id:
        return False
    row = _find_snapshot(internal_id)
    if row is None:
        return False
    return _snapshots.promote(row["message_id"])


def promote_for_email(source: str | None, ref: str | None) -> None:
    """Promote the snapshot for one booked ``{"source": "email", "ref": <internal id>}``
    provenance pair. Silently no-ops on a non-email / empty / unmatched ref — this is called
    from the ledger write choke (once per pair the row carries) and must never surface an
    error to a transaction write."""
    if source != "email" or not ref:
        return
    promote(ref)


def emitter_for(source: str | None, ref: str | None) -> str | None:
    """The sender address behind one booked ``{"source": "email", "ref": <internal id>}`` pair,
    or None when it cannot be established (non-email source, unstashed ref, pruned snapshot).

    Resolved SERVER-SIDE from what we actually received, never taken from a caller: the model
    supplies ``external_refs`` and must not be able to assert who sent an email, because the
    emitter is what ``matching._same_source_event_veto`` trusts to decide whether two distinct
    msgids are two distinct payments.

    Best-effort by contract — this is called from the ledger write choke and a lookup failure
    must degrade to "unresolved" (which vetoes, the safe direction), never fail the write."""
    if source != "email" or not ref:
        return None
    try:
        row = _find_snapshot(ref)
    except Exception:
        logger.warning("emitter_for: snapshot lookup failed for %r", ref, exc_info=True)
        return None
    if row is None:
        return None
    return (row.get("from_addr") or "").strip() or None


def snapshots_for_transaction(txn_id: str) -> list[dict] | None:
    """Every source-email snapshot behind a booked transaction — one entry per ``external_refs``
    pair with ``source == "email"`` (a merge can absorb several source emails onto one row), or
    None when the transaction doesn't exist or carries no email provenance at all — one 404
    semantic covers both at the API layer.

    Each entry is either the stored snapshot row itself, or — when the snapshot was never
    stashed / has since been pruned — an explicit ``{"message_id": <ref>, "unavailable": True}``
    marker: missing evidence stays VISIBLE in the drawer, never silently dropped (owner
    decision, multi-source-email drawer). Entries with a snapshot sort oldest-received-first;
    unavailable markers keep their provenance-list order at the end (no received_at to sort by).
    """
    # Local import: reuse services.ledger's OWN `transactions` singleton (the same instance
    # every other read path in the app uses) rather than minting a second TransactionStore()
    # — module-level would cycle (ledger's promote hook already imports this module locally).
    from backend.services.ledger import transactions as _txns

    txn = _txns.find_by_id(txn_id)
    if txn is None:
        return None
    email_refs: list[str] = []
    for entry in txn.get("external_refs") or []:
        if not isinstance(entry, dict) or entry.get("source") != "email":
            continue
        ref = str(entry.get("ref") or "").strip()
        if ref and ref not in email_refs:
            email_refs.append(ref)
    if not email_refs:
        return None
    found: list[dict] = []
    missing: list[dict] = []
    for internal_id in email_refs:
        row = _find_snapshot(internal_id)
        if row is None:
            missing.append({"message_id": internal_id, "unavailable": True})
            continue
        # Self-heal: a row found HERE is proven referenced by a real, already-booked transaction
        # — if it somehow missed its promote at write time, flip it now so it survives the
        # unreferenced prune. Best-effort: a failed flag write must NEVER fail the read.
        if not row.get("referenced"):
            try:
                _snapshots.promote(row["message_id"])
            except Exception:
                logger.warning(
                    "email_snapshots: self-heal promote failed for %s", row.get("message_id")
                )
        found.append(row)
    found.sort(key=lambda r: str(r.get("received_at") or "\uffff"))
    return found + missing
