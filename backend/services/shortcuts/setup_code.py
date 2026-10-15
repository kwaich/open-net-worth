"""The setup code: this instance's address and API token as ONE pasteable string.

A signed shortcut cannot carry per-instance values, so they travel separately and the
shortcut's single import question asks for this. It IS a credential — it contains the API
token — so it is only ever shown to a signed-in operator or behind a setup ticket.
"""

from __future__ import annotations

# Never appears in a URL we accept (``|`` must be percent-encoded) or in a
# ``secrets.token_urlsafe`` token, so the shortcut's Split Text yields exactly two parts.
SEPARATOR = "|"


def make(base_url: str, token: str) -> str:
    return f"{base_url.strip().rstrip('/')}{SEPARATOR}{token.strip()}"
