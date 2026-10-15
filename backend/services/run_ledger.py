"""
run_ledger — run-record construction, streak-dedup, redaction, and failure notifications.

The single responsible module for:
  * Building and appending run records to RunHistoryStore (the ONLY write path).
  * Deciding whether to emit a failure alert (first-of-streak only) or a recovered note.
  * Redacting secrets from outbound notify text (sk-..., bearer tokens, URLs, emails).
  * Systemic grouping: if ≥2 entities fail within 5 min with the same root cause, emit
    ONE grouped alert instead of N individual ones.
  * Fan-out via services.notify.notify (the existing channel seam).

Layering: services → db (RunHistoryStore) + services → services (notify) + services →
core (errors). Never imports api or ai.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from backend.db.run_history_store import RunHistoryStore
from backend.services import notify as notify_svc

logger = logging.getLogger(__name__)

# Shared store — tests repoint _store to an isolated tmp_path instance.
_store = RunHistoryStore()

# Systemic-grouping window (seconds): entities failing within this window with the same
# redacted root cause emit ONE grouped alert instead of N individual ones.
_SYSTEMIC_WINDOW_SECONDS = 300

# Hard ceiling on text fed to _redact() and on exc strings stored in run records
# (prevents ReDoS on pathologically long error strings).
_REDACT_INPUT_LIMIT = 4000


# ── Redaction ─────────────────────────────────────────────────────────────────

# Patterns to redact before ANY text leaves via notify. The full error is still stored
# locally (auth-gated, checkpoint-EXCLUDED); only the outbound message body is redacted.
_REDACT_PATTERNS: list[tuple[re.Pattern, str]] = [
    # GitHub fine-grained PATs: github_pat_ + 20+ alnum/underscore chars
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}", re.IGNORECASE), "[api-key]"),
    # Stripe live/restricted keys: sk_live_ or rk_live_ + 8+ alnum chars
    (re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{8,}", re.IGNORECASE), "[api-key]"),
    # Slack tokens: xoxb/xoxa/xoxp/xoxr/xoxs/xapp + dash + alnum/dash chars
    (re.compile(r"\b(?:xox[abprs]|xapp)-[A-Za-z0-9\-]{8,}", re.IGNORECASE), "[api-key]"),
    # API key shapes: sk-/sk_..., gsk-..., ghp_/gho_/ghs_/ghr_/ghu_ (GitHub), xai-..., ant-..., claude-...
    (
        re.compile(
            r"\b(?:sk|gsk|ghp|gho|ghs|ghr|ghu|xai|ant|claude)[-_][A-Za-z0-9_\-]{6,}",
            re.IGNORECASE,
        ),
        "[api-key]",
    ),
    # AWS access keys: AKIA/ASIA prefix + 16 uppercase alnum (IAM key shape)
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "[aws-key]"),
    # Google API keys: AIza prefix + 35 alnum/dash
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b"), "[google-key]"),
    # GitLab personal access tokens: glpat- prefix
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}", re.IGNORECASE), "[gitlab-token]"),
    # Bearer / Authorization header values
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE), "Bearer [token]"),
    # key= / token= / secret= / password= / passwd= / pwd= query-string or form values
    (
        re.compile(
            r"(?:key|token|secret|api_key|apikey|password|passwd|pwd)=[A-Za-z0-9._\-]{8,}",
            re.IGNORECASE,
        ),
        "[credential]=***",
    ),
    # Basic-auth URLs with user:pass — including IP host forms (scheme://user:pass@host/...)
    (
        re.compile(r"https?://[A-Za-z0-9._\-]+:[A-Za-z0-9._\-@!$&'()*+,;=%]{4,}@[^\s\"']+"),
        "[url-with-credentials]",
    ),
    # Full URLs → host only  (e.g. https://api.openai.com/v1/... → https://api.openai.com)
    (re.compile(r"https?://([^\s/\"']+)[^\s\"']*"), r"https://\1"),
    # Email addresses — bounded quantifiers to prevent ReDoS:
    #   local part: 1..64 chars; domain labels: 1..63 chars; TLD: 2..24 alpha chars
    (
        re.compile(r"[A-Za-z0-9._%+\-]{1,64}@(?:[A-Za-z0-9\-]{1,63}\.){1,8}[A-Za-z]{2,24}"),
        "[email]",
    ),
]


def _redact(text: str) -> str:
    """Strip secret-shaped strings from ``text``. Applied before any outbound notify.

    Input is truncated to _REDACT_INPUT_LIMIT chars before regex work to prevent ReDoS
    on pathologically long error strings.
    """
    if not text:
        return text
    # Hard truncation before any regex — prevents quadratic backtracking on huge inputs.
    result = text[:_REDACT_INPUT_LIMIT]
    for pattern, replacement in _REDACT_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


# ── Status derivation ─────────────────────────────────────────────────────────


def _parse_status(raw_status: str | None) -> str:
    """Derive 'ok' | 'error' | 'skipped' from the last_status string prefix."""
    s = (raw_status or "").strip()
    if s == "ok" or s.startswith("ok"):
        return "ok"
    if s.startswith("skipped"):
        return "skipped"
    if s.startswith("error"):
        return "error"
    return "ok"


# ── Systemic-grouping suppression ─────────────────────────────────────────────

# In-memory per-window suppression key: maps redacted_error → first_error_iso when we
# emitted a grouped alert. Entries older than the window are pruned at the top of
# _maybe_group_notify so this dict doesn't grow unboundedly.
_group_suppress: dict[str, str] = {}


def _prune_group_suppress(window_iso: str) -> None:
    """Remove _group_suppress entries whose timestamp is older than window_iso."""
    expired = [k for k, v in _group_suppress.items() if v < window_iso]
    for k in expired:
        del _group_suppress[k]


def _peer_names(matching: list[dict]) -> list[str]:
    """Extract human-readable names from peer streak records, falling back to entity_id."""
    names = []
    for p in matching[:3]:
        name = p.get("entity_name") or p.get("entity_id") or "?"
        names.append(name)
    return names


async def _maybe_group_notify(
    entity_kind: str,
    entity_id: str,
    entity_name: str,
    first_error_at: str,
    last_error_redacted: str,
) -> bool:
    """Check peer streak records for a systemic failure. If ≥2 entities (including this
    one) share the same redacted root cause within the window, emit ONE grouped alert and
    return True (caller skips the per-entity alert). Returns False if no grouping applies.

    Kept ≤60 lines: one helper, no correlation engine. If grouping is uncertain, returns
    False so the per-entity alert always fires — never suppress a genuine first failure."""
    if not last_error_redacted:
        return False

    window_iso = _window_start_iso(_SYSTEMIC_WINDOW_SECONDS)
    suppress_key = last_error_redacted[:80]  # stable fingerprint, bounded

    _prune_group_suppress(window_iso)

    # Already emitted a grouped alert for this root cause within the window?
    suppressed_since = _group_suppress.get(suppress_key)
    if suppressed_since and suppressed_since >= window_iso:
        return True  # suppress; grouped alert already sent

    peers = _store.recent_failing(window_iso)
    matching = [
        p
        for p in peers
        if p.get("last_error_redacted", "")[:80] == suppress_key
        and not (p.get("entity_id") == entity_id and p.get("kind") == "__streak__")
    ]
    if len(matching) < 1:  # need at least 1 peer (this entity + 1 = 2 total)
        return False

    peer_names = _peer_names(matching)
    names = [entity_name] + peer_names
    names_str = ", ".join(n for n in names if n)
    msg = f"Lucius: {suppress_key} is failing {1 + len(matching)} automation(s) ({names_str})."
    try:
        await notify_svc.notify(msg)
    except Exception as exc:  # pragma: no cover — best-effort
        logger.warning("run_ledger: grouped notify failed: %s", exc)
    _group_suppress[suppress_key] = first_error_at
    return True


def _window_start_iso(seconds: int) -> str:
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    start = now - timedelta(seconds=seconds)
    return start.isoformat()


# ── Core record function ───────────────────────────────────────────────────────


async def record_run(
    *,
    entity_kind: str,
    entity_id: str,
    entity_name: str,
    started_at: str,
    status: str,
    summary: str,
    trigger: str,
    error: str | None = None,
    finding_id: str | None = None,
    steps: list | None = None,
    cost_usd: float | None = None,
) -> dict:
    """Build, append, and (best-effort) notify for one run record.

    Returns the stored run dict. Never raises: errors are logger.warning'd so a
    run-ledger hiccup never breaks run_agent or _execute_task."""
    run = _store.append(
        {
            "kind": entity_kind,
            "entity_id": entity_id,
            "entity_name": entity_name,
            "started_at": started_at,
            "status": status,
            "error": error,
            "summary": summary,
            "trigger": trigger,
            "finding_id": finding_id,
            "steps": steps or [],
            "cost_usd": cost_usd,
        }
    )

    try:
        await _handle_streak_and_notify(
            entity_kind=entity_kind,
            entity_id=entity_id,
            entity_name=entity_name,
            started_at=started_at,
            status=status,
            error=error,
        )
    except Exception as exc:
        logger.warning(
            "run_ledger: streak/notify failed for %s/%s: %s", entity_kind, entity_id, exc
        )

    return run


async def _handle_error_streak(
    entity_kind: str,
    entity_id: str,
    entity_name: str,
    started_at: str,
    streak: dict | None,
    redacted: str,
) -> None:
    """Handle the error branch: notify on first-of-streak, increment on repeat."""
    was_failing = streak.get("failing", False) if streak else False
    if not was_failing:
        grouped = await _maybe_group_notify(
            entity_kind, entity_id, entity_name, started_at, redacted
        )
        if not grouped:
            await _emit_failure_alert(entity_name, started_at, redacted)
        _store.set_streak_state(
            entity_kind,
            entity_id,
            {
                "entity_name": entity_name,
                "failing": True,
                "fail_count": 1,
                "first_error_at": started_at,
                "last_error_redacted": redacted,
            },
        )
    else:
        fail_count = int(streak.get("fail_count") or 0) + 1
        _store.set_streak_state(
            entity_kind,
            entity_id,
            {
                "entity_name": entity_name,
                "failing": True,
                "fail_count": fail_count,
                "last_error_redacted": redacted,
            },
        )


async def _handle_streak_and_notify(
    *,
    entity_kind: str,
    entity_id: str,
    entity_name: str,
    started_at: str,
    status: str,
    error: str | None,
) -> None:
    """Update the streak companion and emit a notify when warranted.

    Rules:
    - first failure of a streak → ONE alert (suppressed if systemic grouping fires).
    - repeat failure (already failing) → no alert, increment fail_count.
    - first success after a failure streak → 'recovered' note, reset streak.
    - skipped → no streak change, no alert.
    """
    if status == "skipped":
        return

    streak = _store.streak_state(entity_kind, entity_id)
    was_failing = streak.get("failing", False) if streak else False

    if status == "error":
        redacted = _redact(error or "error")
        await _handle_error_streak(
            entity_kind, entity_id, entity_name, started_at, streak, redacted
        )
    elif status == "ok":
        if was_failing:
            await _emit_recovered_note(entity_name)
        _store.set_streak_state(
            entity_kind,
            entity_id,
            {
                "entity_name": entity_name,
                "failing": False,
                "fail_count": 0,
                "first_error_at": None,
                "last_error_redacted": None,
            },
        )


async def _emit_failure_alert(name: str, started_at: str, redacted_error: str) -> None:
    try:
        ts = datetime.fromisoformat(started_at).strftime("%H:%M")
    except Exception:
        ts = started_at[:16]
    one_line = " ".join(redacted_error.split())[:120]
    msg = f"Lucius: {name} failed at {ts}. Reason: {one_line}. Check Settings if this looks like a credentials issue."
    try:
        await notify_svc.notify(msg)
    except Exception as exc:
        logger.warning("run_ledger: failure alert notify failed: %s", exc)


async def _emit_recovered_note(name: str) -> None:
    msg = f"Lucius: {name} is running normally again."
    try:
        await notify_svc.notify(msg)
    except Exception as exc:
        logger.warning("run_ledger: recovered notify failed: %s", exc)


# ── Trigger-title enrichment ──────────────────────────────────────────────────


def _enrich_trigger_title(runs: list[dict]) -> list[dict]:
    """Add trigger_title to each run record at read time (never stored).

    For trigger == "automation:<id>": the Automation name (None if deleted).
    For any other trigger: None.

    Loaded lazily to avoid a module-level import cycle
    (run_ledger ← scheduling would be circular; scheduling already imports run_ledger).
    """
    if not runs:
        return runs

    automation_ids: set[str] = set()
    for r in runs:
        trigger = r.get("trigger") or ""
        if trigger.startswith("automation:"):
            automation_ids.add(trigger[len("automation:") :])

    automation_names: dict[str, str | None] = {}
    if automation_ids:
        from backend.db.automation_store import AutomationStore

        store = AutomationStore()
        for automation_id in automation_ids:
            automation = store.find_by_id(automation_id)
            automation_names[automation_id] = automation.get("name") if automation else None

    enriched = []
    for r in runs:
        trigger = r.get("trigger") or ""
        if trigger.startswith("automation:"):
            automation_id = trigger[len("automation:") :]
            title = automation_names.get(automation_id)
        else:
            title = None
        enriched.append({**r, "trigger_title": title})
    return enriched


# ── Read helpers ──────────────────────────────────────────────────────────────


def list_runs(entity_kind: str, entity_id: str, limit: int = 50) -> list[dict]:
    """Return run records for one entity, newest-first, capped at limit.

    Each record is enriched with the Automation name for ``automation:<id>`` triggers."""
    runs = _store.for_entity(entity_kind, entity_id, limit=min(limit, 50))
    return _enrich_trigger_title(runs)
