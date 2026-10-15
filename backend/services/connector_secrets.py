"""Connector secret REVEAL — the OPERATOR-ONLY read path for a stored credential.

The operator typed these keys in; they may read them back. Without this, rotating a key,
moving an instance, or simply checking which of two accounts a token belongs to would be
impossible from the UI: every view answers ``has_<field>`` and the value is recoverable only
by opening the database.

## This module exists so ``services/connectors.py`` never has to grow a secret

The alternative — widening ``ConnectorView`` with the values, gated by a flag — was rejected.
``ConnectorView`` is consumed by Lucius's connector tools and by every list/detail payload that
lands in a browser cache, a proxy log and a screenshot. A field that is *usually* stripped is
one forgotten call site away from a leak, and the leak this codebase already had
(``list_mcp_servers`` shipping a DSN inside ``endpoint``, ADR
``2026-08-13-what-a-model-may-see-of-a-connector``) came from exactly that shape: a projection
built for one audience, reused by another.

So the split is STRUCTURAL, not conditional. ``services/connectors.py`` stays secret-free and is
the only connector module the AI layer reads. This module returns exactly one value per call, is
reachable only from ``api/connector_secrets.py``, and is imported by nothing under ``backend/ai/``
or ``backend/mcp/`` — pinned by ``tests/test_connector_secrets_model_isolation.py``.

## The boundary

**The OPERATOR may read a stored secret. The MODEL may not.** They are different consumers of
the same data and the app authenticates only one of them. A browser carrying the operator's
session is the operator; a tool result is a string an LLM will read and can be talked into
forwarding to any outbound tool. Nothing here is model-reachable: there is no ``@tool`` wrapper,
and adding one would be the bug.

## What is revealable, and what deliberately is not

Exactly the ``revealable`` secret fields of the connector's kind in ``connector_catalog`` — the
ones the operator TYPES INTO A FORM. Machine-minted session artifacts (Plaid's ``access_token``,
Yodlee's session tokens, Enable Banking's ``session_id``, OAuth tokens) are not: no form shows
them, nobody can copy one anywhere useful, and each is refreshed by the sync path anyway. Hidden
secrets (Telegram's chat map) are not even listed. Reveal is "show me the key I pasted", not a
database browser.

**Nothing here reads ``os.environ``**: connectors are configured in their rows alone.

Storage is unchanged: values live in ``connector_secrets`` (``core.sensitive.SECRET_TABLES``),
checkpoint-excluded, non-restorable, stripped from a downloaded archive by
``services.backup.write_sanitised_copy``. This is a read path, not a change to how secrets are
kept.

Dependency rule: imports ``db``/``core`` and its sibling ``connector_catalog`` — never
``api``/``ai``/``mcp``.
"""

from __future__ import annotations

from typing import Tuple

from backend.core.errors import NotFoundError
from backend.db.connector_store import ConnectorStore
from backend.services.connector_catalog import ConnectorKind, builtins, kind_of


def _known_kind(connector_id: str) -> ConnectorKind:
    """The kind of an EXISTING connector: a catalog built-in, or an open-ended one (an MCP
    server) with a row. 404 for anything else."""
    kind = kind_of(connector_id)
    if kind is not None:
        exists = (
            ConnectorStore().get(connector_id) is not None
            if kind.open_ended
            else any(b.id == connector_id for b in builtins())
        )
        if exists:
            return kind
    raise NotFoundError(f"unknown connector: {connector_id}", connector_id=connector_id)


def revealable_fields(connector_id: str) -> Tuple[str, ...]:
    """Which of ``connector_id``'s stored fields the operator may read back.

    Raises ``NotFoundError`` (→ 404) for a connector that does not exist. An existing
    connector with no credential of its own returns an empty tuple rather than raising —
    "nothing to reveal" is a different answer from "no such connector".
    """
    return _known_kind(connector_id).revealable_fields


def reveal_secret(connector_id: str, field: str) -> str:
    """The stored plaintext value of ONE field of ONE connector.

    Raises ``NotFoundError`` (→ 404) for an unknown connector, a field that is not
    revealable, or a field with nothing saved. All three are 404 on purpose: an
    authenticated operator learns which fields exist from the form they are looking at, and
    a uniform status keeps the endpoint from being a probe for what is configured.
    """
    fields = revealable_fields(connector_id)  # 404s an unknown connector
    if field not in fields:
        allowed = ", ".join(fields) or "none"
        raise NotFoundError(
            f"'{field}' is not a revealable field of connector '{connector_id}' "
            f"(revealable: {allowed})",
            connector_id=connector_id,
            field=field,
        )
    value = _text(ConnectorStore().secrets(connector_id).get(field))
    if not value:
        raise NotFoundError(
            f"no saved '{field}' for connector '{connector_id}'",
            connector_id=connector_id,
            field=field,
        )
    return value


def _text(value: object) -> str:
    """A stored secret as the text its form's box accepts. A blank/absent value reads as
    "nothing saved".

    An MCP server's ``env`` is the WHOLE overlay as ``KEY=value`` lines, sorted so a reveal is
    stable across calls — the box is compared against what the user then types to decide
    whether the save replaces the map, and an unstable order would make an untouched reveal look
    edited. Its ``args`` are the space-joined line the form's box takes."""
    if isinstance(value, dict):
        return "\n".join(f"{k}={v}" for k, v in sorted(value.items()) if str(k).strip())
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value or "").strip()
