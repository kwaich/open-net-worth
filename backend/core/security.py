"""Framework-free auth crypto (RFC P3-AUTH §3).

Pure, leaf-level primitives for instance authentication — password hashing,
signed expiring session tokens, constant-time token comparison. Stdlib only
(``hmac``, ``hashlib``, ``secrets``, ``base64``, ``json``, ``time``); imports
NOTHING upward, so this stays a ``core/`` leaf the dependency-rule allows every
layer to read.

Design notes:
- Password hashing is stdlib PBKDF2-HMAC-SHA256 (salted), encoded
  ``pbkdf2$<iters>$<b64salt>$<b64hash>`` — zero new dependencies.
- Session tokens are a hand-rolled compact HMAC-SHA256 envelope
  ``base64url(json(payload + exp)).base64url(sig)`` — single audience, single
  key, no JWT algorithm-agility footguns.
- Every verify function is constant-time on the secret comparison and returns a
  falsy/``None`` result on ANY malformed input rather than raising — callers
  (the FastAPI dependency) must never see an exception from a bad token.
- Annotations use ``typing.Optional`` (repo targets Python 3.9; runtime-evaluated
  PEP-604 unions have burned us before).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

# PBKDF2 work factor. Tuned high enough to slow an offline attack on a stolen
# hash while staying well under a human-perceptible login latency.
_PBKDF2_ITERATIONS = 600_000
_HASH_PREFIX = "pbkdf2"


# ── base64url helpers (no padding, URL-safe) ─────────────────────────────────


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


# ── Password hashing (PBKDF2-HMAC-SHA256) ────────────────────────────────────


# One floor for every way a password is set: Settings, the first-run step, `onw reset-password`.
MIN_PASSWORD_LENGTH = 8


def hash_password(pw: str, *, iterations: int = _PBKDF2_ITERATIONS) -> str:
    """Hash ``pw`` into a self-describing ``pbkdf2$iters$b64salt$b64hash`` string."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iterations)
    return f"{_HASH_PREFIX}${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(pw: str, stored: str) -> bool:
    """Verify ``pw`` against a stored ``pbkdf2$...`` hash. False on any malformed input."""
    if not pw or not stored:
        return False
    try:
        prefix, iters_s, salt_b64, hash_b64 = stored.split("$")
        if prefix != _HASH_PREFIX:
            return False
        iterations = int(iters_s)
        salt = _b64decode(salt_b64)
        expected = _b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


# ── Signed expiring session tokens (HMAC-SHA256) ─────────────────────────────


def sign_session(payload: dict, key: bytes, ttl: int) -> str:
    """Sign ``payload`` (plus an ``exp`` = now + ``ttl``) into a compact token.

    Format: ``base64url(json(payload+exp)).base64url(hmac_sha256(body, key))``.
    """
    body = dict(payload)
    body["exp"] = int(time.time()) + int(ttl)
    body_bytes = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(key, body_bytes, hashlib.sha256).digest()
    return f"{_b64encode(body_bytes)}.{_b64encode(sig)}"


def verify_session(token: str, key: bytes) -> Optional[dict]:
    """Return the payload dict iff the signature is valid AND not expired, else ``None``.

    Never raises — any malformed/tampered/expired token yields ``None``.
    """
    if not token or not key:
        return None
    try:
        body_b64, sig_b64 = token.split(".")
        body_bytes = _b64decode(body_b64)
        presented_sig = _b64decode(sig_b64)
    except (ValueError, TypeError):
        return None
    expected_sig = hmac.new(key, body_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(presented_sig, expected_sig):
        return None
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or time.time() >= exp:
        return None
    return payload


# ── API token (Bearer) ───────────────────────────────────────────────────────


def verify_api_token(presented: str, configured: str) -> bool:
    """Constant-time compare a presented Bearer token to the configured one.

    False if either side is blank (an unconfigured token must never match).
    """
    if not presented or not configured:
        return False
    return hmac.compare_digest(presented, configured)


def generate_token() -> str:
    """Generate a fresh URL-safe secret (session key / API token / first-run token)."""
    return secrets.token_urlsafe(32)
