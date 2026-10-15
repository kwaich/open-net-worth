"""Strip credentials out of an error string before it is stored where a model may read it.

An HTTP client's exception text routinely quotes the URL it was calling, a header or a response
body, and a connector's URL is routinely the credential: SimpleFIN's access URL is
``https://user:pass@bridge/...``, an MCP server takes ``?api_key=…`` or carries its token in the
path (``/api/mcp/s/<TOKEN>/mcp``), a Discord webhook is ``/api/webhooks/<id>/<token>``.
``connectors.state.last_error`` is not a secret column (a model reads it through
``get_connector_status``), so ``ConnectorStore.merge_state`` runs every ``last_error`` /
``error`` through here at write time. Transports should still never put a credential-bearing
URL in a message (``redact_endpoint`` is the host-only form); this is the backstop.

Masked, nothing else — this is not a general secret scanner:

  * URL userinfo — ``scheme://user:pw@host`` becomes ``scheme://***@host`` (a password may
    itself contain ``/``);
  * ``Authorization: <scheme> <value>`` and any ``Bearer <token>``;
  * the value of a JSON-ish ``"name": "value"`` pair whose NAME mentions a credential word;
  * the value of a ``name=value`` pair whose NAME mentions ``token``, ``key``, ``secret``,
    ``password``, ``passwd``, ``pwd`` or ``access_url``, and of a query parameter named
    ``sig``/``signature``/``code``/``sas``/``auth``/``jwt``;
  * a long opaque URL path segment (20+ of ``[A-Za-z0-9_-]`` mixing letters and digits).

Pure and idempotent. Stdlib only (``core/`` is a leaf).
"""

from __future__ import annotations

import re

REDACTED = "***"

# ``scheme://`` then either userinfo up to the first ``/``, ``?`` or ``#`` (the greedy class
# also eats an ``@`` inside a password), or ``user:password`` whose password holds a ``/`` — the
# lookahead keeps ``host:8080/path`` (a port, not a password) from matching.
_USERINFO = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)"
    r"(?:[^/\s?#]*|[\w.\-%+]+:(?!\d{1,5}(?:[/?#\s]|$))[^\s?#]*)@"
)

_AUTHORIZATION = re.compile(
    r"(?P<head>\bauthorization[\"']?\s*[:=]\s*[\"']?)"
    r"(?:(?:bearer|basic|token)\s+)?[^\s\"',;]+",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\b(?P<scheme>bearer)\s+[A-Za-z0-9._~+/=\-]{6,}", re.IGNORECASE)

_CREDENTIAL_WORD = r"(?:token|key|secret|passw(?:or)?d|pwd|credential|authorization)"

# ``"api_key": "…"`` / ``'token': '…'`` — a quoted value after a colon.
_JSON_PAIR = re.compile(
    r"(?P<name_q>[\"']?)(?P<name>[\w.\-]*" + _CREDENTIAL_WORD + r"[\w.\-]*)(?P=name_q)"
    r"(?P<sep>\s*:\s*)(?P<q>[\"'])(?P<value>[^\"'\n]*)(?P=q)",
    re.IGNORECASE,
)

# A name=value pair, the name containing a credential word. The lookbehind stops the match
# starting mid-name; the value runs to the next delimiter a URL or a sentence would put there.
_SECRET_PAIR = re.compile(
    r"(?<![\w.\-])"
    r"(?P<name>[\w.\-]*(?:token|key|secret|passw(?:or)?d|pwd|access_?url)[\w.\-]*)"
    r"=(?P<value>[^&\s\"'<>)\]]+)",
    re.IGNORECASE,
)

# Query parameters whose NAME is exactly a signature/code (not caught by the word list above).
_QUERY_PAIR = re.compile(
    r"(?<=[?&])(?P<name>sig|signature|code|sas|auth|jwt)=(?P<value>[^&\s\"'<>)\]#]+)",
    re.IGNORECASE,
)

_OPAQUE_SEGMENT = re.compile(r"(?<=/)[A-Za-z0-9_\-]{20,}(?=[/?#\s\"')\]]|$)")


def _mask_opaque_segment(match: re.Match[str]) -> str:
    segment = match.group(0)
    has_letter = any(c.isalpha() for c in segment)
    has_digit = any(c.isdigit() for c in segment)
    return REDACTED if has_letter and has_digit else segment


def redact_error(text: str) -> str:
    """``text`` with credentials quoted inside it masked (see the module docstring)."""
    text = _USERINFO.sub(lambda m: f"{m.group('scheme')}{REDACTED}@", text)
    text = _AUTHORIZATION.sub(lambda m: f"{m.group('head')}{REDACTED}", text)
    text = _BEARER.sub(lambda m: f"{m.group('scheme')} {REDACTED}", text)
    text = _JSON_PAIR.sub(
        lambda m: (
            f"{m.group('name_q')}{m.group('name')}{m.group('name_q')}{m.group('sep')}"
            f"{m.group('q')}{REDACTED}{m.group('q')}"
        ),
        text,
    )
    text = _SECRET_PAIR.sub(lambda m: f"{m.group('name')}={REDACTED}", text)
    text = _QUERY_PAIR.sub(lambda m: f"{m.group('name')}={REDACTED}", text)
    return _OPAQUE_SEGMENT.sub(_mask_opaque_segment, text)
