"""
Tests for IMAP mailbox quoting -- regression for the "[Gmail]/All Mail" space bug.

Python's imaplib does NOT quote mailbox names on its own. A folder name containing a
space (e.g. a user who configured "[Gmail]/All Mail" directly, or any custom folder
with a space in it) sent bare to conn.select() breaks IMAP command parsing server-side:

    EXAMINE [Gmail]/All Mail          -> BAD Could not parse command

_quote_mailbox() wraps space-containing names in an RFC 3501 quoted-string. These
tests verify:

1. _quote_mailbox rules (unit level):
   - "INBOX" stays bare (no quotes)
   - "[Gmail]/All Mail" becomes '"[Gmail]/All Mail"'
   - names with embedded double-quotes or backslashes are correctly escaped

2. The exact string recorded by conn.select() is the quoted form for space-containing
   names and bare for simple names — exercised via search_headers(), the live
   header-search entry point (fetch_messages, the original site of this fix and of an
   automatic Gmail All-Mail folder swap, was removed 2026-08-18 as dead code — nothing
   in this app calls it anymore; see backend/ingest/imap_client.py's module docstring).
   Since search_headers() always uses creds["folder"] exactly as configured (no
   automatic folder substitution), these tests set a space-containing folder directly
   rather than needing to trigger any discovery step.

3. Regression: a mock IMAP server that returns BAD for an unquoted space-name (but OK
   for the quoted form) succeeds after the fix.
"""

from __future__ import annotations

import imaplib
from unittest.mock import MagicMock, patch

import pytest

from backend.ingest.imap_client import _quote_mailbox, search_headers

# ---------------------------------------------------------------------------
# 1. Unit tests for _quote_mailbox
# ---------------------------------------------------------------------------


class TestQuoteMailbox:
    def test_inbox_stays_bare(self):
        assert _quote_mailbox("INBOX") == "INBOX"

    def test_plain_folder_stays_bare(self):
        # No spaces or special chars → returned verbatim.
        assert _quote_mailbox("[Gmail]") == "[Gmail]"
        assert _quote_mailbox("SENT") == "SENT"
        assert _quote_mailbox("Drafts") == "Drafts"

    def test_space_triggers_quoting(self):
        assert _quote_mailbox("[Gmail]/All Mail") == '"[Gmail]/All Mail"'

    def test_google_mail_variant(self):
        assert _quote_mailbox("[Google Mail]/All Mail") == '"[Google Mail]/All Mail"'

    def test_embedded_doublequote_escaped(self):
        # An embedded " must become \" inside the outer quotes.
        result = _quote_mailbox('My "Special" Folder')
        assert result == '"My \\"Special\\" Folder"'

    def test_embedded_backslash_escaped(self):
        result = _quote_mailbox("Folder\\Name")
        assert result == '"Folder\\\\Name"'

    def test_backslash_then_quote_both_escaped(self):
        # Backslash escape must happen first; then double-quote escape.
        result = _quote_mailbox('Path\\"Quoted"')
        # original: Path\"Quoted"
        # after \\ escape: Path\\"Quoted"
        # after " escape: Path\\\\"Quoted\\"
        assert result == '"Path\\\\\\"Quoted\\""'

    def test_control_char_triggers_quoting(self):
        # A tab (control char) in a folder name is unusual but must be quoted.
        result = _quote_mailbox("Folder\twith tab")
        assert result.startswith('"') and result.endswith('"')


# ---------------------------------------------------------------------------
# 2. conn.select() receives the correctly-quoted string
# ---------------------------------------------------------------------------


def _make_minimal_imap_mock() -> MagicMock:
    """
    Build a minimal imaplib mock that:
    - accepts login
    - accepts select (returns OK)
    - returns a plausible UIDVALIDITY
    - returns an empty UID search result
    - accepts logout
    """
    conn = MagicMock(spec=imaplib.IMAP4_SSL)
    conn.login.return_value = ("OK", [b"Logged in"])
    conn.select.return_value = ("OK", [b"0"])
    conn.response.return_value = ("UIDVALIDITY", [b"12345"])
    conn.uid.return_value = ("OK", [b""])
    conn.logout.return_value = ("OK", [b"Bye"])
    return conn


def _creds(folder: str = "INBOX", host: str = "imap.example.com") -> dict:
    return {
        "host": host,
        "port": 993,
        "user": "test@example.com",
        "password": "secret",
        "folder": folder,
    }


class TestSelectReceivesQuotedName:
    """The exact string passed to conn.select() is the RFC 3501 quoted-string form."""

    def _run(self, folder: str, host: str = "imap.example.com") -> str:
        """Run search_headers and return the first positional arg given to conn.select()."""
        conn = _make_minimal_imap_mock()
        with patch("imaplib.IMAP4_SSL", return_value=conn):
            search_headers(_creds(folder, host), date_since="2026-01-01")
        # conn.select is called as conn.select(mailbox, readonly=True)
        assert conn.select.called, "conn.select was never called"
        return conn.select.call_args[0][0]  # first positional argument

    def test_inbox_passed_bare(self):
        arg = self._run("INBOX")
        assert arg == "INBOX"

    def test_sent_passed_bare(self):
        arg = self._run("Sent")
        assert arg == "Sent"

    def test_space_folder_passed_quoted(self):
        """The load-bearing assertion: [Gmail]/All Mail must arrive as a quoted-string."""
        arg = self._run("[Gmail]/All Mail", host="imap.gmail.com")
        # The exact wire string the IMAP server would receive as the mailbox argument.
        assert arg == '"[Gmail]/All Mail"', (
            f"Expected '\"[Gmail]/All Mail\"' but conn.select() got {arg!r}. "
            "This would have sent an unquoted space to the server, causing BAD parse."
        )

    def test_google_mail_folder_passed_quoted(self):
        arg = self._run("[Google Mail]/All Mail", host="imap.googlemail.com")
        assert arg == '"[Google Mail]/All Mail"'

    def test_user_configured_space_folder(self):
        """A non-Gmail user who configured a folder with a space also gets quoted."""
        arg = self._run("My Folder")
        assert arg == '"My Folder"'


# ---------------------------------------------------------------------------
# 3. Regression: a mock server that rejects unquoted space names succeeds
# ---------------------------------------------------------------------------


class _SpaceAwareImap:
    """
    Minimal fake imaplib.IMAP4_SSL that enforces the quoting rule:
    - select() with an unquoted space → returns BAD
    - select() with a properly quoted name → returns OK
    - everything else: bare OK / empty UID search
    """

    def __init__(self, *args, **kwargs):
        self._selected = None

    def login(self, user, password):
        return ("OK", [b"Logged in"])

    def select(self, mailbox, readonly=False):
        # Reject if the argument contains a space but is not quoted.
        if " " in mailbox and not (mailbox.startswith('"') and mailbox.endswith('"')):
            return ("BAD", [b"Could not parse command"])
        self._selected = mailbox
        return ("OK", [b"0"])

    def response(self, name):
        return (name, [b"99999"])

    def uid(self, command, *args):
        return ("OK", [b""])

    def logout(self):
        return ("OK", [b"Bye"])


def test_regression_bad_for_unquoted_passes_when_quoted():
    """
    Simulate the exact production failure: a server that returns BAD for an
    unquoted "[Gmail]/All Mail" (space) but OK for the quoted form.

    Before the fix, an unquoted select() would raise RuntimeError("could not select
    folder…") because conn.select() returned BAD. After the fix it succeeds. The user
    has configured "[Gmail]/All Mail" as their folder directly — no discovery step
    involved, matching how search_headers() always uses the folder exactly as given.
    """
    creds = {
        "host": "imap.gmail.com",
        "port": 993,
        "user": "user@gmail.com",
        "password": "pw",
        "folder": "[Gmail]/All Mail",
    }

    with patch("imaplib.IMAP4_SSL", new=_SpaceAwareImap):
        # Must not raise — the quoted form should be accepted by the mock server.
        result = search_headers(creds, date_since="2026-01-01")

    assert result["messages"] == []
    assert result["folder"] == "[Gmail]/All Mail"


def test_regression_unquoted_would_fail():
    """
    Confirm the regression: if quoting were absent the mock returns BAD and
    search_headers raises. This is what was happening in production.
    """

    class _UnquotedImap(_SpaceAwareImap):
        """Wrap select() to strip our quoting so we can simulate the old code."""

        def select(self, mailbox, readonly=False):
            # Simulate the OLD behaviour: strip the outer quotes before sending.
            raw = mailbox
            if raw.startswith('"') and raw.endswith('"'):
                raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            return super().select(raw, readonly=readonly)

    creds = {
        "host": "imap.gmail.com",
        "port": 993,
        "user": "user@gmail.com",
        "password": "pw",
        "folder": "[Gmail]/All Mail",
    }
    with patch("imaplib.IMAP4_SSL", new=_UnquotedImap):
        with pytest.raises(RuntimeError, match="could not select folder"):
            search_headers(creds, date_since="2026-01-01")
