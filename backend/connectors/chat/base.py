"""The bidirectional chat-channel seam — ``ChatChannelAdapter`` + ``InboundMessage``.

Formalizes what the Telegram path already did informally (RFC chat-channel-seam §2): a
chat channel turns a provider event into a normalized ``InboundMessage``, checks an
allowlist, runs ONE Lucius turn via ``connectors.processor.process_message``, and replies
through the adapter's own ``send_text`` / ``send_proposed_change``. Those two send methods
are EXACTLY the duck-typed interface ``process_message`` already requires, so an adapter
passes ``self`` straight in — zero new core machinery, one inbound path for every channel.

**Trust model (corrected — propose+in-channel-confirm default · operator-trust opt-in).**
Chat apps have TWO controls:

1. The per-sender ALLOWLIST (default-deny) — WHO may talk to Lucius at all.
2. The per-connector ``trust_level`` — HOW MUCH an allowlisted sender's turn may do.

A shared third-party workspace (Slack/Discord) is ``untrusted`` by default. That used to
narrow the inbound turn to a read + notify + PROPOSE ceiling, making a write off
shared-channel text structurally impossible. **That ceiling has been removed**
(owner-authorised): an ALLOWLISTED sender now gets the same tool offer as the web app and
can make Lucius write directly. ``untrusted`` still buys per-sender session isolation and a
per-sender rate limit; the ALLOWLIST is what gates access to the ledger. ``propose_*`` tools
and their in-channel confirm cards still exist and still bind to their proposer.

An ``operator`` channel (a private Telegram bot the operator controls, or a Slack/Discord
connector where the operator has explicitly flipped the **Operator trust** opt-in) is the
trusted operator — like the web UI — so it runs the FULL tool set, byte-identical to the
web app. ``trust_level`` ALSO governs the shared-workspace hardening: an ``untrusted``
channel gets per-sender session isolation + a per-sender rate limit; an ``operator`` channel
keys the session on the channel and skips the per-sender limit.

The per-connector **Operator trust** opt-in (a config flag on the Slack/Discord store, read
by the adapter's ``trust_level`` property, default OFF — mirroring the external-MCP "Trust
this server" toggle) is the ONLY way to elevate a shared channel to full tools. The safe
default is propose+in-channel-confirm; full tools requires this explicit human opt-in.

This module is a leaf of ``connectors.chat`` — it imports only stdlib + ``typing`` so it
can't create an import cycle with ``run`` / ``processor`` / the adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

# The two trust levels. ``untrusted`` (a shared Slack/Discord workspace) → per-sender session
# isolation + a per-sender rate limit. It no longer narrows the tool offer. ``operator`` (a
# private Telegram bot, or a Slack/Discord connector where the operator flipped the
# Operator-trust opt-in) → the FULL tool set, byte-identical to the web app, with
# channel-keyed session + no per-sender limit. An adapter computes its ``trust_level`` from
# config (default untrusted on shared channels), so the safe default is propose+confirm and
# full tools is an explicit human opt-in.
TrustLevel = str  # "operator" | "untrusted"
TRUST_OPERATOR: TrustLevel = "operator"
TRUST_UNTRUSTED: TrustLevel = "untrusted"

# Access policy — WHO may reach Lucius on this channel (a friendlier layer over the raw
# default-deny allowlist; mirrors OpenClaw's pairing model). DISTINCT from ``trust_level``
# (which is HOW MUCH an allowed turn may do); the two compose orthogonally.
#
#   * ``pairing``  — the friendly DEFAULT for a shared workspace (Slack/Discord/WhatsApp). An
#                    UNKNOWN sender's first message NEVER drives a real Lucius turn — it gets a
#                    short pairing-CODE reply ("ask the operator to approve code ABC123") and a
#                    pending request is recorded; the operator approves it in-app → the sender id
#                    is added to the allowlist and their next message proceeds normally. Same
#                    safety as default-deny (the operator gates every sender), no need to know /
#                    paste ids up front.
#   * ``allowlist`` — today's behavior: only senders in the manual ``allowed_sender_ids`` may
#                     talk; an unknown sender is silently dropped (no pairing). Telegram's
#                     operator-bot contract (empty allowlist = open) is preserved by keeping
#                     Telegram on ``allowlist``.
#   * ``open``     — anyone may talk (DEV ONLY). An explicit, loud opt-in — never a default.
#   * ``disabled`` — the channel is off at the policy layer; every inbound message is dropped
#                    (a kill-switch independent of the connector's ``enabled`` flag).
AccessPolicy = str  # "pairing" | "allowlist" | "open" | "disabled"
POLICY_PAIRING: AccessPolicy = "pairing"
POLICY_ALLOWLIST: AccessPolicy = "allowlist"
POLICY_OPEN: AccessPolicy = "open"
POLICY_DISABLED: AccessPolicy = "disabled"
ACCESS_POLICIES = (POLICY_PAIRING, POLICY_ALLOWLIST, POLICY_OPEN, POLICY_DISABLED)


@dataclass
class QuotedMessage:
    """An earlier message the inbound one is a REPLY to (Telegram's ``reply_to_message``).

    Carried because a reply is how a person points at something specific in a long
    conversation — "why?" under one alert means something different than "why?" under
    another — and without it the model only ever saw the bare follow-up text. ``from_bot``
    separates the two cases that matter: quoting OUR OWN earlier message ("why did you say
    that") from quoting the person's own ("change what I asked for").

    ``text`` is raw provider text and is treated as untrusted at render time: quoting a
    Lucius message quotes back whatever that message relayed, which may have originated at
    an external MCP server or a fetched page.
    """

    text: str
    from_bot: bool = False
    message_id: Optional[int] = None


@dataclass
class InboundMessage:
    """A provider message normalized to the shape ``process_message`` consumes.

    ``channel_id`` is where to reply (Slack channel id, Telegram chat id, …); ``sender_id``
    is who spoke (the allowlist key — may equal ``channel_id`` for DMs). ``attachment_refs``
    reuses the processor's multimodal shape — durable ``AttachmentStore`` ids, the ONLY
    attachment path since the cross-channel-attachments RFC Phase 2 (raw, un-persisted
    ``images`` bytes were removed from ``process_message`` — every adapter must persist via
    ``services.attachment_ingress.persist_inbound_file`` before constructing one of these,
    exactly as ``connectors/telegram/handlers.py``'s own photo/document handlers do). Never
    had a live producer here either (no ``InboundMessage(...)`` construction site ever set
    it). ``raw_meta`` holds provider bookkeeping (event id for dedup, thread ts, …).
    """

    channel_id: str
    sender_id: str
    text: str
    attachment_refs: Optional[list] = None
    raw_meta: dict = field(default_factory=dict)
    reply_to: Optional["QuotedMessage"] = None


@runtime_checkable
class ChatChannelAdapter(Protocol):
    """The contract every two-way chat adapter implements (RFC §2.1).

    ``send_text`` / ``send_proposed_change`` are the SAME duck-typed interface
    ``processor.process_message`` requires — so ``handle_inbound`` passes the adapter
    straight in as the ``connector``. ``verify`` is webhook-transport auth (HMAC signature /
    verify-token) and is a no-op for gateway/polling adapters. ``parse`` turns a raw provider
    event into 0..n ``InboundMessage`` (it filters bot echoes / non-message subtypes).

    ``trust_level`` (``operator`` vs ``untrusted``) is the per-connector posture: an
    ``untrusted`` shared workspace gets per-sender session isolation + a rate limit (it no
    longer narrows tools); an ``operator`` channel gets neither. An adapter usually
    computes ``trust_level`` from config (a private bot is ``operator``; a shared workspace is
    ``untrusted`` unless the operator flips the Operator-trust opt-in). The IN-CHANNEL confirm
    affordance is ALWAYS available regardless of trust level — the trust level only governs
    whether the turn may write directly (operator) or only propose (untrusted).
    """

    connector: str
    trust_level: TrustLevel

    def is_allowed(self, sender_id: str) -> bool:
        """Whether ``sender_id`` may talk to Lucius (per-provider allowlist)."""
        ...

    def access_policy(self) -> AccessPolicy:
        """The channel's access policy (``pairing``|``allowlist``|``open``|``disabled``).

        OPTIONAL — the seam reads it via ``getattr`` with an ``allowlist`` default, so an
        adapter that doesn't implement it keeps today's manual-allowlist behavior. Computed
        fresh from config (a UI edit applies immediately, like ``is_allowed``)."""
        ...

    def add_allowed_sender(self, sender_id: str) -> None:
        """Add ``sender_id`` to this channel's allowlist (the pairing-approval write path).

        OPTIONAL — only the approve API calls it (never the inbound seam); an adapter without
        it simply can't be paired. It is a config write, never an inbound-driven mutation."""
        ...

    async def send_text(self, channel_id: str, text: str) -> None:
        """Send a plain-text reply to ``channel_id``."""
        ...

    async def send_proposed_change(
        self, channel_id: str, change: dict, change_id: str, summary: str
    ) -> None:
        """Surface a proposed change to ``channel_id`` (inline keyboard / Block Kit). The
        adapter renders an in-channel confirm affordance (✓ Apply / ✗ Discard) so the user
        confirms in-channel — preferably in a DM for an untrusted shared channel so other
        members can't see or tap it."""
        ...
