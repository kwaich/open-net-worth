"""POST /api/mentions/resolve — resolve-on-tap endpoint.

Resolves a list of mention refs ({type, ref, label}) to their current data
payloads, reusing ``dispatch.resolve_mentions`` verbatim (same dedup, same
MAX_MENTIONS cap, same P0-A hardened formatter path).

Design decisions (RFC 2026-06-25-lucius-generic-entity-registry §8.1):

- POST body (not path param): refs contain `:` (allocation_slice) and empty
  strings (net_worth), neither of which survives a {ref} path segment.
- ``_mention_detail_only`` is ALWAYS hardcoded to True on this endpoint (RFC §8.3
  / C4/C9): the resolve-on-tap path MUST NOT trigger ``_brief_context`` /
  ``_analyst_context`` — those make outbound Yahoo requests per tap and belong
  to the chat-turn resolution path only. The flag is NOT client-controllable
  here; a caller cannot force the network-touching path by omitting a field.
  The chat turn calls ``resolve_mentions()`` directly (no HTTP), so it correctly
  never touches this ContextVar.
- Rate-limited: 60 req / 60s per client IP (SlidingWindowLimiter from
  core.ratelimit). Sized for a tap-to-detail UI; see the _RESOLVE_MAX note —
  this endpoint blocks the brief/analyst splice so it does NO network I/O.
- Rate-limit key: ``request.client.host`` ONLY — mirrors ``auth._client_key``,
  the established codebase pattern. X-Forwarded-For is attacker-controlled and
  NOT read here (RFC finding: the rate limit IS security-load-bearing because it
  bounds the per-tap network amplification from the analyst/brief splice).
- Auth: covered by the app-wide ``require_auth`` dependency.
- Thin router: validate → set ContextVar → dispatch → response.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.core.ratelimit import SlidingWindowLimiter
from backend.services.mentions.dispatch import resolve_mentions
from backend.services.mentions.resolve import _mention_detail_only

router = APIRouter()
log = logging.getLogger(__name__)

# Per-IP rate limit for resolve-on-tap. This endpoint hardcodes
# _mention_detail_only=True, which BLOCKS the brief/analyst splice — so unlike the
# chat-turn path it performs NO outbound network I/O, only cheap in-memory reads +
# sanitization. The original 10/60s (mirroring the chat-channel connector) was sized for
# that network-amplification risk, which does not exist here, and was far too low for a
# tap-to-detail UI: a user browsing chips across tabs easily taps >10 entities/min, and
# each off-screen tap is a fresh resolve (the on-screen path reads the DOM, never the
# cache). 60/60s (≈1/s) keeps a DoS ceiling while letting normal browsing resolve fully.
_RESOLVE_MAX = 60
_RESOLVE_WINDOW = 60.0
_resolve_limiter = SlidingWindowLimiter(_RESOLVE_MAX, _RESOLVE_WINDOW)


class MentionRef(BaseModel):
    type: str
    ref: str
    label: str = ""


class ResolveRequest(BaseModel):
    refs: list[MentionRef] = Field(default_factory=list)


def _client_key(request: Request) -> str:
    """Rate-limit key: the client IP (falls back to a constant if absent).

    Uses ``request.client.host`` ONLY — mirrors ``auth._client_key``.
    X-Forwarded-For is NOT read: it is attacker-controllable and would let a
    caller rotate the header on each request to bypass the rate-limit window.
    """
    client = request.client
    return client.host if client is not None else "unknown"


@router.post("/api/mentions/resolve")
def post_mentions_resolve(body: ResolveRequest, request: Request) -> list[dict]:
    """Resolve mention refs to their current data payloads.

    Body: ``{refs: [{type, ref, label}]}``
    Response: ``[{type, ref, label, data: {...}} | {type, ref, label, error: str}]``

    - Dedup by ``(type, ref)``; capped at ``MAX_MENTIONS=12``.
    - Brief/analyst context (network-touching) is NEVER spliced on this path —
      ``_mention_detail_only`` is hardcoded to True (RFC §8.3). The chat turn
      calls ``resolve_mentions()`` directly (no HTTP) so it gets the full splice.
    - Unknown type → ``{error: "unknown_type"}``; missing entity → ``{error: "not_found"}``.
    - Rate-limited: 60/60s per client IP → 429 on excess.
    """
    key = _client_key(request)
    allowed, retry_after = _resolve_limiter.check(key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="resolve rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    _resolve_limiter.record(key)

    refs_raw = [{"type": r.type, "ref": r.ref, "label": r.label} for r in body.refs]

    # Always set mention_detail_only=True for the resolve-on-tap path.
    # The chat turn never reaches this endpoint; it calls resolve_mentions()
    # directly and the ContextVar stays at its default (False).
    token = _mention_detail_only.set(True)
    try:
        result = resolve_mentions(refs_raw)
    finally:
        _mention_detail_only.reset(token)

    return result
