"""One place that decides whether untrusted XML is safe to hand to stdlib ElementTree.

stdlib ``xml.etree`` is NOT hardened against malicious XML. Both attacks that matter —
entity-expansion ("billion laughs") and XXE external-entity file read — REQUIRE a DTD, so
refusing any document that declares one blocks the whole class without adding a dependency.
That is the reasoning ``services/feeds.py`` and ``services/aggregation/ibkr.py`` already
implement, and it is sound.

**Why this module exists rather than a fourth copy of that logic.** The rule was hand-written
per call site, and per-site rules get applied per-site: CAMT — the one XML format a user
UPLOADS, arriving as a bank statement from wherever they got it — was parsed with no guard at
all, while an RSS feed had one. An independent scan (2026-08-28) flagged exactly those two
files. Extracting the check makes "did this parser get the guard?" answerable by reading one
import instead of auditing every call site, and gives the next XML format one obvious thing to
call.

**Bounded prologue, walked token by token, not a whole-document scan.** A DTD is only legal BEFORE the root element,
so only the prologue needs checking. Scanning the whole document would false-positive on
content that merely mentions ``<!DOCTYPE`` inside a text node — a real risk for a feed item or
a statement memo quoting XML. A document whose root element is not within ``PROLOGUE_LIMIT`` is
treated as hostile: no legitimate file has 64 KB of prologue.

This is deliberately NOT ``defusedxml``. That would be the better answer if the dependency were
already present; it is not, and adding one to close a class already closed by four lines is a
poor trade. If ``defusedxml`` ever arrives for another reason, this module should be replaced
by it rather than kept alongside.
"""

from __future__ import annotations

import re

# A DTD sits in the prologue; 64 KB is far more than any real document needs before its root
# element, and bounding the scan keeps a huge upload from costing a full-body pass.
PROLOGUE_LIMIT = 65536

# The first START-TAG of an actual element: `<` followed by a name character.
_ELEMENT_START = re.compile(rb"<[A-Za-z_]")
_UTF8_BOM = b"\xef\xbb\xbf"


def has_dtd(xml_bytes: bytes) -> bool:
    """True when the document declares a DTD or entity before its root element, OR when its
    prologue is anything other than whitespace, comments, processing instructions and the root.

    A True answer means: refuse this document. Both entity-expansion and XXE need a DTD, so
    this is equivalent to ``defusedxml``'s ``forbid_dtd`` for every format this app parses
    (CAMT, RSS/Atom, IBKR Flex) — none of which legitimately carries one.

    The prologue is WALKED, not searched: comments and PIs are skipped whole, so a ``<a``
    inside ``<!-- <a -->`` can no longer pass for the root and hide the ``<!DOCTYPE`` that
    follows it. Anything the walk does not recognise fails closed.

    Returns False for empty input: "no DTD present" is accurate, and the caller's own
    empty/malformed handling is the right place to reject it, not this one.
    """
    if not xml_bytes:
        return False

    head = bytes(xml_bytes[:PROLOGUE_LIMIT])
    pos = len(_UTF8_BOM) if head.startswith(_UTF8_BOM) else 0
    while True:
        while pos < len(head) and head[pos : pos + 1] in b" \t\r\n":
            pos += 1
        if head.startswith(b"<!--", pos):
            end = head.find(b"-->", pos + 4)
            if end == -1:
                return True
            pos = end + 3
        elif head.startswith(b"<?", pos):
            end = head.find(b"?>", pos + 2)
            if end == -1:
                return True
            pos = end + 2
        elif _ELEMENT_START.match(head, pos):
            return False
        else:
            # `<!DOCTYPE`, `<!ENTITY`, a CDATA section, stray text, a UTF-16 BOM, or running out
            # of bytes inside the bound without a root: none of it is something to parse.
            return True


__all__ = ["PROLOGUE_LIMIT", "has_dtd"]
