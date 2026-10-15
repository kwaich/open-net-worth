"""core.ids — the single entity-id minter (ADR 0011).

``new_id(prefix)`` -> ``f"{prefix}_{uuid4().hex[:12]}"``: a short, prefixed, token-cheap id.

- **48 random bits** — the first 12 hex chars of a uuid4. They sit entirely before uuid4's
  fixed version nibble (hex index 12), so all 48 bits are random — no wasted entropy.
- **Hex, no dashes.** Hex tokenizes ~2 chars/token for LLM-facing surfaces (vs a dashed uuid
  that shatters into ~23 tokens), and its alphabet (``0-9a-f``) carries no ambiguous
  characters — already human-safe, no need for base32.
- **Self-describing prefix.** ``txn_``/``acc_``/… tells a reader (and the model) the entity
  type, which cuts wrong-id tool calls.
- **Optional uniqueness check.** ``exists(candidate) -> bool`` drives a check-and-retry, so the
  effective collision rate is zero regardless of volume (48 bits alone is already ~1-in-5.6M at
  10k ids of a single type, namespaced per prefix).

Leaf: stdlib only (``uuid``, ``re``, ``typing``) — no internal imports, per the
``core`` is-a-leaf contract.
"""

from __future__ import annotations

import re
import uuid
from typing import Callable

# A prefix is a short, stable, self-describing tag: lowercase, starts with a letter.
_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]*$")

# A backstop against a buggy/always-true ``exists`` — never reached by real collisions
# (48 bits would need millions of live ids to retry even once).
_MAX_RETRIES = 8


def new_id(prefix: str, *, exists: Callable[[str], bool] | None = None) -> str:
    """Mint ``f"{prefix}_{12 hex}"``. ``prefix`` must be lowercase alnum starting with a
    letter. If ``exists`` is given, regenerate until it returns False (check-and-retry).
    Raises ``ValueError`` on a bad prefix; ``RuntimeError`` only if ``exists`` rejects every
    attempt (a misbehaving callback, never real collisions)."""
    if not _PREFIX_RE.match(prefix or ""):
        raise ValueError(
            f"id prefix must be lowercase alnum starting with a letter (e.g. 'txn'), got {prefix!r}"
        )
    for _ in range(_MAX_RETRIES):
        candidate = f"{prefix}_{uuid.uuid4().hex[:12]}"
        if exists is None or not exists(candidate):
            return candidate
    raise RuntimeError(
        f"could not mint a unique id for prefix {prefix!r} after {_MAX_RETRIES} tries"
    )
