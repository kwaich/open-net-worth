# 2026-07-09 — JsonStore fails closed on a corrupt file (F6)

**Status:** accepted (implemented, code-reviewed APPROVE-WITH-NITS, verified on current HEAD) · fixes the industrial audit's scariest data-integrity finding
**Where:** `backend/db/json_store.py` (`_load_raw`, `_quarantine_corrupt`, `__init__`, `_save_raw`), `backend/core/io.py` (`_atomic_write_bytes`), `backend/core/errors.py` (`DataIntegrityError`), `tests/test_json_store_durability.py`

## Context

`JsonStore._load_raw` caught **all** exceptions on parse and returned empty (`[]`/`{}`), making a present-but-corrupt file indistinguishable from an absent one. The next `insert`/`upsert` then loaded that "empty", appended one row, and persisted it — **overwriting the whole store with a one-row file. Silent, permanent data loss** on any corruption (a torn write, disk error, bit rot). `_save_raw` compounded it: on any `atomic_write_text` failure it fell back to a plain, non-atomic `write_text`, which on disk-full (ENOSPC — the likeliest cause) truncates the real file. And `_atomic_write_bytes` did `write + os.replace` with no fsync, so a "committed" write was atomic against a crash but not durable against power loss.

## Decision

**A `JsonStore` fails CLOSED on a corrupt file by default** — it never silently treats corruption as empty.

- `_load_raw` distinguishes **absent** (legitimate first-run → returns empty) from **present-but-unparseable** (corruption). On corruption, for a fail-closed store it **quarantines** the bytes (copies — never moves — to a `<path>.corrupt-<ts>` sibling, once per file; the original is left untouched so a retry keeps raising and no stray write can land on it) and raises the new `DataIntegrityError` (`core/errors.py`, code `data_integrity`, HTTP 500).
- New constructor param **`fail_closed_on_corrupt: bool = True`** — the **safe default**. A genuinely degradable config/cache/runtime store may pass `False` to keep the old "corrupt → defaults" behavior (an opt-out, deliberately chosen per store, not a blanket).
- `_save_raw` **no longer falls back to a non-atomic write** — a failed durable write propagates loudly rather than truncating the real file.
- `_atomic_write_bytes` now **fsyncs** the temp file before the rename and best-effort fsyncs the parent directory after, cleaning up the temp file if the write fails.

Empirically, **no existing store needed the opt-out** at ship time (the two the audit named — `AppConfigStore`, `PairingStore` — turned out to be custom classes, not `JsonStore` subclasses; the full suite is green with the fail-closed default everywhere).

## Consequences

- Corruption is now a loud, recoverable failure (`DataIntegrityError` + quarantined bytes) instead of silent data loss — the intended invariant for a system holding a real financial ledger.
- **Behavior change, ~60 `JsonStore` subclasses**: reads/writes on a corrupt store now RAISE. This is strictly safer than the prior silent wipe, but it means write failures (e.g. ENOSPC) now propagate to service callers that previously never saw one. Follow-up (F6b, board): a deliberate durable-vs-disposable audit of the runtime/cache stores (`PendingAnalysisStore`, `RunHistoryStore`, `BatchLedgerStore`, `SignalsStore`, `IntelSignalsStore`, …) to opt the genuinely disposable ones out of fail-closed so a corrupt cache self-heals rather than hard-failing a background job. The money-truth stores (transactions, lots, positions, accounts, `txn_audit`) stay fail-closed.
- Known limits (follow-ups): GET-only routers don't yet map `OpenNetWorthError` → `to_http_status` (a corrupt store on a read surfaces as a bare 500); `DataIntegrityError`'s message embeds the absolute server path; on macOS `os.fsync` isn't `F_FULLFSYNC` (inherited from the pre-existing `watchlist.py` technique).

## Agent provenance

F6 in the hardening loop (`docs/launch/BOARD.md`), Foundation flow. Foundation locked on 2026-06-28 for *math* correctness; this closes a *storage-durability* gap that lock never covered — surfaced by the 2026-07-06 industrial audit (`docs/launch/PLATFORM_MASTER_PLAN.md` P0-2). Implemented by a senior-engineer in an isolated worktree with fail-first tests; code-review gate APPROVE-WITH-NITS (the quarantine-once dedupe and the two io nits above were folded in from that review).
