"""Secret-shaped value redaction for logs and diagnostic step trails.

Tool inputs to ``update_model_config`` / ``set_provider_credentials`` carry an ``api_key``.
Writing those raw to ``app.log`` (surfaced by the LogViewer, ``GET /api/logs``, and the
``get_recent_logs`` tool) OR into the run-step timeline (the ``run_history`` DB table)
leaks the key back into model/attacker-readable context, defeating the write-only secret
store. ``redact_secrets`` masks by mapping KEY on the structured object, so a raw secret is
caught regardless of its value format.

Layer: leaf — stdlib only (``backend.core`` is a leaf per the import-linter contracts).
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Case-insensitive substrings that mark a mapping KEY as secret-bearing. Matched as a
# substring so api_key / access_token / client_secret / refresh_token / password all mask.
_SECRET_KEY_HINTS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "private_key",
    "access_key",
)

_MASK = "***"


def _is_secret_key(key: object) -> bool:
    return isinstance(key, str) and any(hint in key.lower() for hint in _SECRET_KEY_HINTS)


def redact_secrets(obj: Any) -> Any:
    """Return a copy of ``obj`` with the values of secret-shaped mapping keys masked.

    Recurses through dicts and lists (a secret nested inside either is still caught);
    scalars are returned unchanged. Pure and total — never raises."""
    if isinstance(obj, dict):
        return {k: (_MASK if _is_secret_key(k) else redact_secrets(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_secrets(x) for x in obj]
    return obj


def redact_summary(obj: Any, limit: int = 120) -> str:
    """A short, secret-masked string form of ``obj`` for a single log line. Never raises."""
    try:
        return str(redact_secrets(obj))[:limit]
    except Exception:  # noqa: BLE001 — a logging helper must never break its caller
        return "<unloggable>"


# Token SHAPES, for free text where there is no mapping key to go by (a log line). Order
# matters: the specific provider/Bearer forms run before the generic long-token sweep.
_TOKEN_SHAPES = re.compile(
    r"(?i:bearer)\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|\bsk-[A-Za-z0-9_-]{16,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\bxox[abposr]-[A-Za-z0-9-]{10,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|(?i:api[_-]?key|token|secret|password|passwd)[\"']?\s*[:=]\s*[\"']?[^\s\"',;&]{6,}"
    r"|(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])"
)


def redact_token_shapes(text: str, known: Iterable[str] = ()) -> str:
    """Mask secret-shaped substrings of free text: each exact ``known`` secret, ``Bearer``
    values, provider key prefixes (sk-, ghp_, xox...), ``key=value`` secrets and any long
    base64url-looking run (>= 32 chars, the shape of a generated API token). Never raises."""
    try:
        for secret in known:
            if secret:
                text = text.replace(secret, _MASK)
        return _TOKEN_SHAPES.sub(_MASK, text)
    except Exception:  # noqa: BLE001 — a logging helper must never break its caller
        return "<unloggable>"
