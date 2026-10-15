"""An uploaded CAMT statement may not carry a DTD.

Flagged by an independent scan (2026-08-28) and by this repo's own audit: `import_camt.py`
and `statements/structured.py` parsed user-uploaded XML with stdlib ElementTree and no DTD
rejection, while `feeds.py` and `aggregation/ibkr.py` — parsing *less* trusted input, from
RSS and a broker API — both had it. CAMT is the one XML format a person UPLOADS, arriving from
wherever they got it, so it was the worst file to have missed.

Current stdlib expat already blocks external-entity resolution and caps entity amplification,
so this is defence in depth rather than a live hole on a modern interpreter. It matters anyway:
the guard is what makes the property hold on an older system expat, and the codebase's own
convention already said to do this.

The shared implementation is `core.safe_xml.has_dtd` — extracted rather than copied a third
time, because per-site rules are what produced this gap.
"""

from __future__ import annotations

import pytest

from backend.core.safe_xml import PROLOGUE_LIMIT, has_dtd
from backend.services.import_camt import CamtImportError, parse_camt
from backend.services.statements.structured import _camt_meta

_XXE = (
    b'<?xml version="1.0"?>\n'
    b'<!DOCTYPE Document [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
    b"<Document><BkToCstmrStmt><Stmt><Ntry>&xxe;</Ntry></Stmt></BkToCstmrStmt></Document>"
)

_BILLION_LAUGHS = (
    b'<?xml version="1.0"?>\n'
    b'<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>\n'
    b"<Document><BkToCstmrStmt><Stmt><Ntry>&lol2;</Ntry></Stmt></BkToCstmrStmt></Document>"
)


# ── the shared predicate ────────────────────────────────────────────────────────────────────


def test_a_doctype_before_the_root_is_flagged() -> None:
    assert has_dtd(_XXE) is True


def test_an_entity_declaration_is_flagged() -> None:
    assert has_dtd(_BILLION_LAUGHS) is True


def test_an_ordinary_document_is_not_flagged() -> None:
    assert has_dtd(b'<?xml version="1.0"?><Document><BkToCstmrStmt/></Document>') is False


def test_doctype_mentioned_in_CONTENT_is_not_flagged() -> None:
    """Why the scan is bounded to the prologue: a statement memo (or a feed item) may quote the
    string. Only a declaration BEFORE the root element can actually define an entity."""
    body = b"<Document><Ntry><AddtlNtryInf>see &lt;!DOCTYPE&gt; in the spec</AddtlNtryInf></Ntry></Document>"
    assert has_dtd(body) is False


def test_a_document_with_no_root_inside_the_bound_is_refused() -> None:
    """No legitimate file has 64 KB of prologue — treat it as hostile rather than parse it."""
    assert has_dtd(b" " * (PROLOGUE_LIMIT + 10)) is True


def test_empty_input_is_not_flagged() -> None:
    """ "No DTD present" is the accurate answer; rejecting empty input is the caller's job."""
    assert has_dtd(b"") is False


# ── the two call sites ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("payload", [_XXE, _BILLION_LAUGHS], ids=["xxe", "billion-laughs"])
def test_parse_camt_refuses_a_dtd_bearing_upload(payload: bytes) -> None:
    with pytest.raises(CamtImportError) as excinfo:
        parse_camt(payload)
    assert "DTD or entity" in str(excinfo.value)


@pytest.mark.parametrize("payload", [_XXE, _BILLION_LAUGHS], ids=["xxe", "billion-laughs"])
def test_camt_meta_returns_empty_rather_than_parsing_a_dtd(payload: bytes) -> None:
    """The metadata read is optional by contract ("an entry-only document still parses"), so it
    degrades to empty meta instead of raising — but it must not hand the bytes to the parser."""
    meta = _camt_meta(payload)
    assert meta.opening_balance is None and meta.closing_balance is None


def test_a_clean_camt_file_still_parses() -> None:
    """The guard must not cost a legitimate import."""
    clean = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b"<Document><BkToCstmrStmt><Stmt><Acct><Id><IBAN>X</IBAN></Id></Acct>"
        b'<Ntry><Amt Ccy="EUR">10.00</Amt><CdtDbtInd>DBIT</CdtDbtInd>'
        b"<BookgDt><Dt>2026-06-01</Dt></BookgDt>"
        b"<NtryDtls><TxDtls><RmtInf><Ustrd>Coffee</Ustrd></RmtInf></TxDtls></NtryDtls>"
        b"</Ntry></Stmt></BkToCstmrStmt></Document>"
    )
    _account, rows = parse_camt(clean)
    assert len(rows) == 1
    assert rows[0]["amount"] == "10.00"  # parse_camt keeps the raw string; mapping converts


# ── scan#7: a comment (or PI) carrying '<a' must not pass for the root element ──────────────

_COMMENT_BYPASS = (
    b"<!-- <a -->"
    b'<!DOCTYPE d [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
    b"<Document><Ntry>&xxe;</Ntry></Document>"
)


@pytest.mark.parametrize(
    "doc",
    [
        _COMMENT_BYPASS,
        b"<?xml version='1.0'?><!-- <a --><!DOCTYPE d [<!ENTITY x 'y'>]><r>&x;</r>",
        b"<?pi <a ?><!DOCTYPE d [<!ENTITY x 'y'>]><r>&x;</r>",
        b"\xef\xbb\xbf  <!-- c --> \n<!-- <b -->\n<!DOCTYPE d><r/>",
        b"<!-- unterminated <a ",
        b"stray text <r/>",
        b"<![CDATA[x]]><r/>",
    ],
)
def test_comment_and_pi_prologue_tricks_are_flagged(doc: bytes) -> None:
    assert has_dtd(doc) is True


def test_comments_and_pis_before_a_clean_root_are_fine() -> None:
    assert (
        has_dtd(b"\xef\xbb\xbf<?xml version='1.0'?>\n<!-- <a -->\n<?pi x?>\n<Document/>") is False
    )


def test_the_bypass_payload_is_refused_end_to_end() -> None:
    with pytest.raises(CamtImportError):
        parse_camt(_COMMENT_BYPASS)
