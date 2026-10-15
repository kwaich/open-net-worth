"""Display-only shortening of instrument names — the single source of truth.

Yahoo's ``longName`` appends security-type boilerplate ("… Class A Common Stock",
"… American Depositary Shares") that makes a name long without adding meaning. We
keep the full legal name STORED (instruments.json, the live-price name cache) and
strip the tail only when *serving* a name for display. Centralising it here means
every surface (portfolio rows, watchlist, asset detail, chat, MCP) shows the same
clean name instead of each frontend view re-deriving it — the JS counterpart in
``frontend/src/components/intel/assetDisplay.ts`` was retired in favour of this.

Match ONLY known security-type tails (never a bare dot) so a real company name like
"U.S. Bancorp" or "J.B. Hunt" is never butchered. Kept 1:1 with the cases in
``tests/test_instrument_display.py``.
"""

from __future__ import annotations

import re

# Trailing security-type boilerplate. A separator (space/comma/dash) must precede a
# known tail token, so dotted initials never trigger a strip.
_SECURITY_TAIL = re.compile(
    r"[\s,–-]+(?:"
    r"Class [A-Z]\b|Common Stock|Ordinary Shares?|Preferred (?:Stock|Shares?)|"
    r"(?:American )?Depositary (?:Shares?|Receipts?)|Capital Stock|Common Units|"
    r"Subordinate Voting Shares?|Warrants?|Sponsored ADR"
    r")\b.*$",
    re.IGNORECASE,
)


def display_name(name: str) -> str:
    """Strip the security-type suffix from an instrument's display name.

    Falls back to the original if a strip would empty it (e.g. a name that is
    *only* a tail token like "Common Stock")."""
    if not name:
        return name
    trimmed = _SECURITY_TAIL.sub("", name).strip()
    return trimmed or name
