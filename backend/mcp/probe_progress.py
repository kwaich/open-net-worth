"""Live progress for ONE in-flight interactive MCP probe — an in-process, ephemeral tail.

WHY THIS EXISTS. A cold ``npx -y <package>`` probe spends its first minute downloading a
package tree before the MCP server it is supposed to spawn even exists. Until this module,
the add flow showed a spinner and, on expiry, a bare "timed out after 300s" — with no way
for the user to tell "still downloading" from "this server is broken". The child writes its
download progress to stderr; ``stdio_client`` now drains that (it always had to — see the
deadlock note there) and publishes each line here, so the UI can show what the wait is
actually doing.

**Everything published here is SERVER-CHOSEN TEXT.** Progress output is no more trustworthy
than error output: every line goes through ``core.untrusted.sanitize_untrusted`` + a
per-line cap on the way IN (so nothing downstream has to remember to), which is what stops a
hostile server forging ``«user_msg:hex»`` — the one fence in this codebase that means the
operator is speaking.

Deliberately in-process, bounded and ephemeral, mirroring ``ai/live_sessions``' posture:

* **No watcher, no cost.** ``note`` on an untracked probe is a ContextVar read that returns
  ``None``. Autonomous tool dispatch never binds a channel at all.
* **Bounded everywhere.** A rolling window of the last ``MAX_LINES`` lines, each capped at
  ``MAX_LINE_CHARS``; at most ``_MAX_CHANNELS`` channels live at once, each evicted at
  ``_CHANNEL_TTL_SECONDS``. A server that streams forever cannot grow this.
* **Thread-safe.** The publisher is the stderr reader THREAD (not the request coroutine, and
  not the same thread the API reads from), so every mutation is under a lock — and the
  channel is captured by reference at spawn time rather than read from the ContextVar inside
  that thread, since a plain ``threading.Thread`` inherits no context.

The token is CLIENT-minted (the POST that starts the probe has not returned yet, so the
server has no way to hand one out first — the idempotency-key shape). It is validated to an
opaque charset and never used as anything but a dict key, and an invalid or absent token
simply means "no progress channel" — it can NEVER fail the probe, which is the actual job.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

from backend.core.untrusted import sanitize_untrusted

logger = logging.getLogger(__name__)

# The rolling window the UI reads. A tail, not a log: enough to show what a cold install is
# doing right now, never a transcript of it.
MAX_LINES = 12
MAX_LINE_CHARS = 200

# A client-minted token is an opaque key, never a path or a name — so an opaque charset and a
# hard length are all it has to satisfy. Anything else is dropped (no progress), not rejected.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# One human runs one add flow at a time; a handful of channels covers a second tab and a
# stale entry or two. Past this, the oldest is evicted rather than letting the map grow.
_MAX_CHANNELS = 8

# A channel outlives its probe by this much so a poll already in flight when the probe ends
# still reads a coherent answer instead of a hole. Comfortably past the longest probe budget
# (``client.PROBE_TIMEOUT_STDIO_SECONDS``) so a slow cold install is never swept mid-probe.
_CHANNEL_TTL_SECONDS = 900.0
_CLOSED_GRACE_SECONDS = 30.0


class ProbeChannel:
    """The bounded, sanitized tail of one probe, written by a reader thread, read by the API."""

    def __init__(self, token: str):
        self.token = token
        self._lines: deque[str] = deque(maxlen=MAX_LINES)
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._closed_at: Optional[float] = None

    def note(self, text: str) -> None:
        """Publish one line of the child's output. Never raises — progress is an accessory."""
        clean = _one_line(text)
        if not clean:
            return
        with self._lock:
            if self._closed_at is None:
                self._lines.append(clean)

    def close(self) -> None:
        with self._lock:
            if self._closed_at is None:
                self._closed_at = time.monotonic()

    def snapshot(self) -> dict:
        with self._lock:
            end = self._closed_at if self._closed_at is not None else time.monotonic()
            return {
                "running": self._closed_at is None,
                "elapsed": round(end - self._started, 1),
                "lines": list(self._lines),
            }

    def _expired(self, now: float) -> bool:
        if self._closed_at is not None:
            return now - self._closed_at > _CLOSED_GRACE_SECONDS
        return now - self._started > _CHANNEL_TTL_SECONDS


def _one_line(text: str) -> str:
    """One untrusted output line: defanged, whitespace-collapsed, capped.

    Sanitized at the INGRESS rather than at each reader, so no consumer — the API response,
    a log line, a future one — can forget to. ``sanitize_untrusted`` keeps ``\\n``/``\\t``
    (useful elsewhere, noise here), so whitespace is collapsed after it to keep a line a line.
    """
    if not isinstance(text, str):
        return ""
    return " ".join(sanitize_untrusted(text, max_len=MAX_LINE_CHARS).split())


_channels: dict[str, ProbeChannel] = {}
_channels_lock = threading.Lock()
_current: ContextVar[Optional[ProbeChannel]] = ContextVar("mcp_probe_channel", default=None)


def _sweep(now: float) -> None:
    """Drop expired channels (caller holds ``_channels_lock``)."""
    for token in [t for t, c in _channels.items() if c._expired(now)]:
        _channels.pop(token, None)


@contextmanager
def track(token: object) -> Iterator[Optional[ProbeChannel]]:
    """Bind a progress channel for the duration of one probe, then close it.

    Bound as a ContextVar rather than threaded through every transport signature, matching
    ``core.audit_context``: the entrypoint sets the scope and the machinery deep inside reads
    it. An unusable token yields ``None`` and the probe runs exactly as it always did.
    """
    channel = _open(token)
    reset = _current.set(channel)
    try:
        yield channel
    finally:
        _current.reset(reset)
        if channel is not None:
            channel.close()


def _open(token: object) -> Optional[ProbeChannel]:
    if not isinstance(token, str) or not _TOKEN_RE.match(token):
        return None
    channel = ProbeChannel(token)
    now = time.monotonic()
    with _channels_lock:
        _sweep(now)
        while len(_channels) >= _MAX_CHANNELS:
            _channels.pop(next(iter(_channels)))  # oldest inserted
        _channels[token] = channel
    return channel


def current_channel() -> Optional[ProbeChannel]:
    """The channel bound to THIS probe, if any.

    Read from the thread that owns the probe and captured by reference — a spawned reader
    thread inherits no ContextVar, so it must be handed the object, never call this itself.
    """
    return _current.get()


def snapshot(token: object) -> dict:
    """The public progress shape for one token — uniform even for an unknown one.

    An unknown token is not an error and not a 404: a poll that arrives a moment after the
    probe's channel was swept is the normal end of a probe, and the POST carries the real
    answer anyway. Leaking "that token existed" would be the only thing a 404 added.
    """
    if not isinstance(token, str):
        return {"running": False, "elapsed": 0.0, "lines": []}
    now = time.monotonic()
    with _channels_lock:
        _sweep(now)
        channel = _channels.get(token)
    if channel is None:
        return {"running": False, "elapsed": 0.0, "lines": []}
    return channel.snapshot()


def _reset_for_tests() -> None:
    with _channels_lock:
        _channels.clear()
