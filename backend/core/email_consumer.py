"""Per-run EMAIL CONSUMER scope — the per-consumer IMAP-cursor seam.

The ``fetch_emails`` RECEIVE block advances a stored UID cursor so repeated calls return
only mail newer than the last call. With a single GLOBAL cursor, two different consumers
clobber each other: when an email-logger Automation fetches and bumps the cursor, a separate
trade-logger Automation that runs later starts PAST those UIDs and silently misses its own mail
(and vice versa). The fix is a PER-CONSUMER cursor — each consumer advances its own cursor
independently, so no consumer skips an email another consumer moved past, and each keeps
its own idempotency ("only mail I haven't seen").

The consumer KEY must be deterministic, not model-supplied (the LLM cannot be trusted to
pass a stable key every call). So ``ai.automation_runner_adapter`` SETS the active consumer
(``"automation:<automation_id>"``) on this ContextVar before the headless runner await, and the
``fetch_emails`` tool READS it at dispatch to pick which cursor to read/advance. When no
consumer scope is active — an interactive Lucius turn or the manual ``/api/email/fetch``
endpoint — it resolves to the shared ``DEFAULT_CONSUMER`` cursor (human-driven; preserves
the pre-existing single-cursor behaviour for that path).

It lives in ``core`` (a leaf — stdlib only) for the SAME reason ``core.auto_apply_scope``
does: it is a seam shared across the layer boundary. ``services.automations`` SETS it without
importing ``ai`` (import-linter Contract 1/3); the assistant (``ai``) READS it.

Propagation mirrors ``core.auto_apply_scope``: the adapter ``await``s the runner directly,
which ``await``s the in-process ``assistant.run`` turn directly — no fresh
``contextvars.Context`` on that path — so a ContextVar set in the adapter is visible
inside the turn. The default is ``None``, so a stock interactive turn (which never sets it)
resolves to ``DEFAULT_CONSUMER`` and behaves exactly as before.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

# The shared cursor used by interactive chat + the manual Fetch-now endpoint (no agent
# context). Also the migration target for the legacy flat ``uidvalidity``/``last_uid``.
DEFAULT_CONSUMER = "default"

_email_consumer: ContextVar[Optional[str]] = ContextVar("email_consumer", default=None)


def set_email_consumer(key: str) -> None:
    """Bind the active email-consumer key for the current run (set by ``run_agent`` before
    the runner await). Use ``"agent:<agent_id>"`` so each agent owns an independent cursor."""
    _email_consumer.set(key)


def get_email_consumer() -> str:
    """The active consumer key, or ``DEFAULT_CONSUMER`` when no consumer scope is active
    (interactive chat / manual Fetch-now)."""
    key = _email_consumer.get()
    return key if key else DEFAULT_CONSUMER


def clear_email_consumer() -> None:
    """Reset the consumer scope (``run_agent`` finally / turn end)."""
    _email_consumer.set(None)
