"""Notification-channel registry seam (RFC P2-SK2 §B.5).

A tiny leaf registry that lets ``services.notify`` fan a message out to channels
that live in higher tiers (the Telegram channel is in ``connectors/``, which
``services`` must not import — import-linter Contract 1). The inversion mirrors
``core.io.register_write_hook`` / ``core.tool_registry_seam``: each channel module
*registers itself* at import time, and ``services.notify`` iterates the registry —
so the edges are ``connectors → core`` and ``services → core`` (both downward),
never ``services → connectors``.

A channel fn takes a pre-formatted message string and returns a per-channel
result dict (e.g. ``{"sent": 3}``). Channel fns are **async** (the Telegram send
is a coroutine awaited inside the scheduler's running event loop); ``Notifier``
awaits them. Last registration under a name wins. This module imports nothing
internal so ``core`` stays a leaf.
"""

from __future__ import annotations

from typing import Awaitable, Callable

# name → async channel fn. An async fn takes a message string, returns a result dict.
Channel = Callable[[str], Awaitable[dict]]

_CHANNELS: dict[str, Channel] = {}


def register_channel(name: str, fn: Channel) -> None:
    """Register an async notification channel under ``name``. Last registration wins."""
    _CHANNELS[name] = fn


def registered_channels() -> dict[str, Channel]:
    """A snapshot copy of the currently-registered channels (name → async fn)."""
    return dict(_CHANNELS)


def unregister_channel(name: str) -> None:
    """Remove a channel (used by tests to keep registrations isolated)."""
    _CHANNELS.pop(name, None)
    _DOCUMENT_CHANNELS.pop(name, None)


# name → async fn(content, filename, caption) → result dict. A channel that can carry a FILE
# (a report artifact) registers here as well as above; one without file support simply doesn't.
DocumentChannel = Callable[[bytes, str, str], Awaitable[dict]]

_DOCUMENT_CHANNELS: dict[str, DocumentChannel] = {}


def register_document_channel(name: str, fn: DocumentChannel) -> None:
    """Register an async file-delivery fn for channel ``name``. Last registration wins."""
    _DOCUMENT_CHANNELS[name] = fn


def registered_document_channels() -> dict[str, DocumentChannel]:
    """A snapshot copy of the channels that can deliver a file."""
    return dict(_DOCUMENT_CHANNELS)
