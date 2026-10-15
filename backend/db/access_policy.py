"""Shared access-policy helpers for the chat-channel config stores (chat-pairing).

The bidirectional chat connector (Telegram) carries an ``access_policy`` field — ``pairing |
allowlist | open | disabled`` (mirrors OpenClaw). The valid set + the normalize-on-write helper
live here — kept as a shared module rather than inlined on the one remaining store, so a future
chat connector agrees on the spelling/default with no per-store divergence — without the db tier
importing the ``connectors`` layer where the policy is consumed (``connectors.chat.base``
re-declares the same constants for the seam).

Data-tier leaf: stdlib only.
"""

from __future__ import annotations

# The four policy modes (kept in sync with connectors.chat.base.ACCESS_POLICIES).
POLICY_PAIRING = "pairing"
POLICY_ALLOWLIST = "allowlist"
POLICY_OPEN = "open"
POLICY_DISABLED = "disabled"
ACCESS_POLICIES = (POLICY_PAIRING, POLICY_ALLOWLIST, POLICY_OPEN, POLICY_DISABLED)


def normalize_access_policy(value, default: str) -> str:
    """Coerce ``value`` to a known policy, falling back to ``default`` for an unknown/blank one.

    Used by every chat-config store's ``update()`` so a bad/typo'd policy can never be persisted
    (it degrades to the store's safe default rather than silently disabling or opening the
    channel)."""
    v = str(value or "").strip().lower()
    return v if v in ACCESS_POLICIES else default
