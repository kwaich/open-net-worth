"""Is a human supervising THIS turn while it executes?

One fact, one consumer, on purpose.

There was a flag like this before (``ai.tools._skill_scope.is_unattended``) and it was
deleted on 2026-08-10 because it had grown a second job: as well as recording "nobody is
watching", it had become a tool-AUTHORIZATION ceiling. Every tool call was checked against
the unattended caller's pre-existing offer, and an Automation's offer is empty until the
owner grants something — so a routine automation run was refused on every single tool it
tried, reported the refusals as a "configuration issue", and marked all its work failed.

Killing that flag fixed the ceiling and also destroyed the fact, which is what
``run_script``'s third gate depends on: an instance may allow code execution in interactive
chat while refusing it in a job nobody is sitting in front of. With the flag gone that gate
silently became dead code, so every Automation inherited the interactive permission.

Hence this module, deliberately narrow:

* It answers ONE question — is anyone there right now?
* It has ONE production reader (``ai.tools.scripts``), pinned by a test. If a second reader
  appears, that is the ceiling growing back, and the test is there to say so.
* It is NOT "can we ask?". A durable Automation card can be tapped hours later, so
  ``_confirm_gate.can_render_card`` answers True for automations — correct for its question,
  wrong for this one. The two come apart exactly where it matters.
* It is NOT the cost surface. That names which budget to bill and carries no promise about
  humans; reading supervision out of it would break the moment a surface is added or renamed.

Lives in ``core`` (a leaf — stdlib only) for the same reason ``core.audit_context`` does:
entrypoints in ``api``/``ai`` SET it, a tool in ``ai`` READS it, and nothing has to import
across a layer boundary to do either.

Propagation matches the other ContextVar seams here: every entrypoint that sets this awaits
the turn directly, with no fresh ``contextvars.Context`` in between, so the value is visible
at tool dispatch. Set it in a ``try`` and reset in the matching ``finally`` — a leaked True
would refuse legitimate interactive runs, and a leaked False is a supervision claim nobody
made.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

# Default False: an ordinary interactive turn HAS a human, and that is the common case. A
# caller with nobody watching is the exception and says so explicitly.
_unattended: ContextVar[bool] = ContextVar("turn_is_unattended", default=False)


def set_unattended(value: bool = True) -> Token:
    """Record whether this turn runs with nobody watching. Returns the reset token."""
    return _unattended.set(bool(value))


def reset_unattended(token: Token) -> None:
    """Restore the previous value — call in the ``finally`` of whoever set it."""
    _unattended.reset(token)


def is_unattended() -> bool:
    """True when no human is present for the duration of this turn.

    Read this ONLY to decide whether an action is safe to perform unsupervised. It is not a
    permission list and must never be used to decide WHICH tools a caller may reach — that
    conflation is what made the previous flag refuse every tool on every Automation.
    """
    return _unattended.get()
