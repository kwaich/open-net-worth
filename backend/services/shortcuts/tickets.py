"""Short-lived tickets for handing phone setup to a device with no session.

The problem this solves: the phone setup page shows this instance's setup code
(address + API token), so it must be authenticated — but the device fetching it is a
*different* device from the one that is signed in. A phone scanning a QR code off
the browser has no session cookie, so a cookie-gated URL would simply 401.

Putting the API token in the URL instead would work and is what most self-hosted
projects do. It also writes a long-lived credential into a QR image, the server's
access log, and anything else that records URLs. A ticket avoids that: the signed-in
browser mints one, the phone redeems it, and it expires on its own a few minutes
later.

In-memory on purpose. Tickets are worthless after ``TTL_SECONDS``, so surviving a
restart buys nothing, and a restart invalidating them is the safe direction.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, replace
from typing import Dict, Optional

TTL_SECONDS = 600

# Bounded so a script hammering /prepare cannot grow this without limit. Well
# above any real use — one operator setting up a phone or two.
MAX_OUTSTANDING = 64


@dataclass(frozen=True)
class Ticket:
    base_url: str
    expires_at: float
    # The API token this ticket was minted under. A setup page shows the CURRENT token, so a
    # ticket must die with the token it was issued for — whichever path rotated it.
    token_fp: str
    # The device that first opened the setup page (a cookie value). Later views must present
    # it, so a photographed QR or a forwarded link stops showing the token after first use.
    device: Optional[str] = None


_tickets: Dict[str, Ticket] = {}


def _fingerprint(api_token: str) -> str:
    return hashlib.sha256(api_token.encode()).hexdigest()


def _prune(now: float) -> None:
    for token in [t for t, ticket in _tickets.items() if ticket.expires_at <= now]:
        _tickets.pop(token, None)


def mint(base_url: str, api_token: str, *, now: Optional[float] = None) -> str:
    """Issue a ticket for one phone setup at ``base_url``. Returns the opaque token."""
    current = time.time() if now is None else now
    _prune(current)
    if len(_tickets) >= MAX_OUTSTANDING:
        oldest = min(_tickets, key=lambda t: _tickets[t].expires_at)
        _tickets.pop(oldest, None)

    token = secrets.token_urlsafe(24)
    _tickets[token] = Ticket(
        base_url=base_url, expires_at=current + TTL_SECONDS, token_fp=_fingerprint(api_token)
    )
    return token


def redeem(token: str, api_token: str, *, now: Optional[float] = None) -> Optional[Ticket]:
    """Return the ticket for ``token``, or ``None`` if unknown, expired or minted under a
    token that has since been rotated.

    Deliberately NOT single-use: iOS may fetch a URL more than once (a HEAD or a
    redirect hop), and a ticket that burns on the first touch would fail in a way
    the user cannot diagnose. The short TTL and :func:`claim` bound exposure.
    """
    current = time.time() if now is None else now
    _prune(current)
    ticket = _tickets.get(token or "")
    if ticket is None or ticket.expires_at <= current:
        return None
    if not hmac.compare_digest(ticket.token_fp, _fingerprint(api_token)):
        _tickets.pop(token, None)
        return None
    return ticket


def claim(token: str, device: Optional[str]) -> Optional[str]:
    """Bind ``token`` to the first device that views it; return that device's id, or ``None``
    when ``device`` is not the one it is bound to. Call only after :func:`redeem` succeeded."""
    ticket = _tickets.get(token or "")
    if ticket is None:
        return None
    if ticket.device is None:
        bound = device or secrets.token_urlsafe(24)
        _tickets[token] = replace(ticket, device=bound)
        return bound
    if device and hmac.compare_digest(ticket.device, device):
        return device
    return None


def revoke_all() -> None:
    """Drop every outstanding ticket (tests; a token rotation)."""
    _tickets.clear()


def outstanding() -> int:
    _prune(time.time())
    return len(_tickets)
