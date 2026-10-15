"""A scanned statement is identified by its masthead image, not by text it does not have.

A card statement rendered by the bank's output engine as one image per page has no text
layer at all. The document fingerprint hashes the first digit-free lines of the EXTRACTED
text, so for that file it is None, ``_instruction_fingerprints`` came back empty, and every
memory hung off the fingerprint silently did nothing: ``peek_source`` answered "unknown"
each month, ``ensure_source_profile`` returned None, and "Remember for this source" wrote
nothing. Reported case: five monthly statements imported into the same card account since
April, the account re-picked by hand every time, no profile row ever created.

The text-free identity is a perceptual hash (dHash) of the rendered TOP BAND of page one —
the masthead: logo, headings, column captions. Measured on the reported source's five
months: at most 1 differing bit between any pair, 36–37 of 120 bits set; unrelated real
documents 40–49 bits apart. The lookup tolerates a small distance so a masthead that
carries a statement date still resolves instead of minting a stray profile beside the
remembered one — the exact scar the text path carries
(`test_statement_fingerprint_stability`) — but only twice the observed drift: sources
sharing a template sit 5–13 apart, and a stray profile costs one re-pick where a merged
one pre-fills another bank's accounts and steers the parse with its instructions. A band
with no structure at either pole — blank, or under a brightness ramp — has no key.
"""

from __future__ import annotations

import pymupdf
import pytest

from backend.db.statement_format_store import StatementFormatStore
from backend.services.statements import page_hash, profiles

_W, _H = 420, 595

_LIGHTHOUSE = (
    "BANK OF LIGHTHOUSE",
    "Lighthouse Tower, Harbour Road",
    "Customer service  Mon-Fri",
    "Account summary        Statement period",
    "Card number   Credit limit   Minimum payment",
)
_MERIDIAN = (
    "meridian",
    "Meridian Plaza, Quay Street",
    "Talk to us  Mon-Sat",
    "Summary of account        Period",
    "Card   Limit   Due",
)


def _scanned_pdf(masthead: tuple[str, ...], *, date_line: str, body: str) -> bytes:
    src = pymupdf.open()
    page = src.new_page(width=_W, height=_H)
    page.insert_text((40, 60), masthead[0], fontsize=22)
    for i, line in enumerate(masthead[1:]):
        page.insert_text((40, 90 + 14 * i), line, fontsize=10)
    page.insert_text((40, 160), date_line, fontsize=10)
    page.insert_text((40, 320), body, fontsize=10)
    pix = page.get_pixmap(dpi=100)
    out = pymupdf.open()
    dst = out.new_page(width=_W, height=_H)
    dst.insert_image(dst.rect, pixmap=pix)
    return out.tobytes()


JUNE = _scanned_pdf(
    _LIGHTHOUSE, date_line="Statement date 02 Jun 2026", body="01 Jun  COFFEE  4.50"
)
JULY = _scanned_pdf(
    _LIGHTHOUSE,
    date_line="Statement date 01 Jul 2026",
    body="03 Jul  GROCER  81.20\n09 Jul  TAXI  12.00",
)
OTHER_BANK = _scanned_pdf(
    _MERIDIAN, date_line="Your statement for July", body="03 Jul  GROCER  81.20"
)
SPARSE = _scanned_pdf(("Notice",), date_line="", body="")


def _photographed_pdf(masthead: tuple[str, ...], *, dark: float, light: float) -> bytes:
    """A page under uneven lighting: brightness ramps left to right across the whole page."""
    src = pymupdf.open()
    page = src.new_page(width=_W, height=_H)
    for i in range(60):
        g = dark + (light - dark) * i / 59
        cell = pymupdf.Rect(i * _W / 60, 0, (i + 1) * _W / 60, _H)
        page.draw_rect(cell, color=(g, g, g), fill=(g, g, g), width=0)
    page.insert_text((40, 60), masthead[0], fontsize=22)
    for i, line in enumerate(masthead[1:]):
        page.insert_text((40, 90 + 14 * i), line, fontsize=10)
    pix = page.get_pixmap(dpi=100)
    out = pymupdf.open()
    dst = out.new_page(width=_W, height=_H)
    dst.insert_image(dst.rect, pixmap=pix)
    return out.tobytes()


_KEY = page_hash.PREFIX + f"{(1 << page_hash._MIN_BITS) - 1:030x}"


def _flip(key: str, bits: int) -> str:
    value = int(key[len(page_hash.PREFIX) :], 16) ^ (((1 << bits) - 1) << 60)
    return page_hash.PREFIX + f"{value:030x}"


@pytest.fixture()
def store(db_isolated) -> StatementFormatStore:
    return StatementFormatStore()


def test_the_fixture_really_has_no_text_layer() -> None:
    doc = pymupdf.open(stream=JUNE, filetype="pdf")
    assert doc.page_count == 1
    assert all(not page.get_text().strip() for page in doc)
    assert profiles._document_fingerprint(JUNE, "pdf") is None


def test_a_scanned_pdf_is_keyed_by_its_masthead_image() -> None:
    keys = profiles._instruction_fingerprints(JUNE, "pdf")

    assert len(keys) == 1
    assert page_hash.is_image_key(keys[0])
    assert keys == profiles._instruction_fingerprints(JUNE, "pdf")


def test_next_month_is_the_same_source_and_another_bank_is_not() -> None:
    june = page_hash.masthead_fingerprint(JUNE)
    july = page_hash.masthead_fingerprint(JULY)
    other = page_hash.masthead_fingerprint(OTHER_BANK)

    assert june and july and other
    assert page_hash.distance(june, july) <= page_hash.MAX_DISTANCE
    assert page_hash.distance(june, other) > page_hash.MAX_DISTANCE


def test_the_remembered_accounts_come_back_next_month(store) -> None:
    """The reported loop, closed: pick the account once, and next month's statement of the
    same source pre-fills it."""
    profile = profiles.ensure_source_profile(JUNE, "pdf", source_label="2026-06-02.pdf")
    assert profile is not None
    profiles.remember_accounts(profile["id"], ["acc_card"])

    peek = profiles.peek_source(JULY, "pdf")

    assert peek["known"] is True
    assert peek["account_ids"] == ["acc_card"]
    assert profiles.peek_source(OTHER_BANK, "pdf")["known"] is False


def test_next_month_does_not_mint_a_second_profile(store) -> None:
    first = profiles.ensure_source_profile(JUNE, "pdf", source_label="june.pdf")
    second = profiles.ensure_source_profile(JULY, "pdf", source_label="july.pdf")

    assert first is not None and second is not None
    assert second["id"] == first["id"]
    assert len(store.all_profiles()) == 1


def test_remember_for_this_source_now_persists(store) -> None:
    saved = profiles.remember_instructions(JUNE, "pdf", "Amounts are SGD; dates are DD/MM")

    assert saved is not None
    assert profiles.find_instructions(JULY, "pdf") == "Amounts are SGD; dates are DD/MM"
    assert profiles.find_instructions(OTHER_BANK, "pdf") == ""


def test_a_near_key_resolves_and_a_far_key_does_not(store) -> None:
    store.upsert(_KEY, kind="document", schema={}, source_label="x", account_ids=["acc_x"])
    near = _flip(_KEY, page_hash.MAX_DISTANCE)
    far = _flip(_KEY, page_hash.MAX_DISTANCE + 1)

    assert page_hash.distance(_KEY, near) == page_hash.MAX_DISTANCE
    assert profiles._lookup(store, near) is not None
    assert profiles._lookup(store, far) is None


def test_an_equidistant_image_key_does_not_adopt_an_arbitrary_profile(store) -> None:
    """Ambiguous image identity must fail closed, never depend on store iteration order."""
    first = _KEY
    second = _flip(_KEY, 4)
    midpoint = _flip(_KEY, 2)
    store.upsert(first, kind="document", schema={}, source_label="first", account_ids=["acc_a"])
    store.upsert(second, kind="document", schema={}, source_label="second", account_ids=["acc_b"])

    assert page_hash.distance(first, midpoint) == page_hash.MAX_DISTANCE
    assert page_hash.distance(second, midpoint) == page_hash.MAX_DISTANCE
    assert profiles._lookup(store, midpoint) is None


def test_a_sparse_masthead_has_no_identity(store) -> None:
    """A band with almost nothing in it sets almost no bits, and every such document sits
    within tolerance of every other: one blank notice would claim, and then overwrite, the
    profile of the next. So under the floor there is no key — nothing to mint, nothing to
    find — and a sparse key already stored is never a hit either."""
    assert page_hash.masthead_fingerprint(SPARSE) is None
    assert profiles._instruction_fingerprints(SPARSE, "pdf") == []
    assert profiles.ensure_source_profile(SPARSE, "pdf", source_label="notice.pdf") is None

    sparse = page_hash.PREFIX + f"{(1 << (page_hash._MIN_BITS - 1)) - 1:030x}"
    store.upsert(sparse, kind="document", schema={}, source_label="x", account_ids=["acc_x"])

    assert not page_hash.is_image_key(sparse)
    assert profiles._lookup(store, sparse) is not None
    assert profiles._lookup(store, _flip(sparse, 1)) is None


def test_a_saturated_masthead_has_no_identity_either() -> None:
    """The other pole. Under a monotone brightness ramp every right-of-left comparison
    tilts the same way, so a blank ramp is all ones and every document photographed under
    the same lighting shares most of the key: three different banks measured 4–8 apart at
    120 bits. The floor is on balance, not on set bits."""
    assert not page_hash.is_image_key(page_hash.PREFIX + "f" * 30)
    twelve_clear = page_hash.PREFIX + f"{(1 << page_hash._BITS) - (1 << page_hash._MIN_BITS):030x}"
    eleven_clear = (
        page_hash.PREFIX + f"{(1 << page_hash._BITS) - (1 << (page_hash._MIN_BITS - 1)):030x}"
    )
    assert page_hash.is_image_key(twelve_clear)
    assert not page_hash.is_image_key(eleven_clear)

    blank_ramp = _photographed_pdf(("",), dark=0.55, light=1.0)
    assert page_hash.masthead_fingerprint(blank_ramp) is None

    for dark, light in ((0.55, 1.0), (0.75, 0.98)):
        keys = [
            page_hash.masthead_fingerprint(_photographed_pdf(m, dark=dark, light=light))
            for m in (_LIGHTHOUSE, _MERIDIAN)
        ]
        minted = [k for k in keys if k]
        assert len(minted) < 2 or page_hash.distance(*minted) > page_hash.MAX_DISTANCE


def test_a_malformed_stored_key_is_ignored(store) -> None:
    """`int(x, 16)` accepts signs, `0x`, underscores and Unicode digits; a signed key XORs
    to within a bit of ANY probe. Only exact lowercase hex of the right width is a key."""
    body = _KEY[len(page_hash.PREFIX) :]
    for bad in (
        page_hash.PREFIX + "f" * 29,
        page_hash.PREFIX + "g" * 30,
        page_hash.PREFIX,
        page_hash.PREFIX + body.upper(),
        page_hash.PREFIX + "-" + body[1:],
        page_hash.PREFIX + "0x" + body[2:],
        page_hash.PREFIX + "+" + body[1:],
        page_hash.PREFIX + body[:10] + "_" + body[11:],
        " " + _KEY,
    ):
        store.upsert(bad, kind="document", schema={}, source_label="x", account_ids=["acc_x"])
        assert not page_hash.is_image_key(bad)

    assert profiles._lookup(store, _KEY) is None


def test_a_text_pdf_keeps_its_text_key(monkeypatch) -> None:
    """The image key is the fallback for a source with no text, never a second identity
    for one that has it — a text PDF must not move onto a different key."""
    import backend.services.statements.parser as parser_mod

    text = "\n".join(["CONSOLIDATED STATEMENT", "ACCOUNT SUMMARY", "DEPOSITS", "WITHDRAWALS"])
    monkeypatch.setattr(parser_mod, "pdf_text", lambda content, **kw: text)

    def _never(content):
        raise AssertionError("the masthead hash must not be consulted for a text source")

    monkeypatch.setattr(page_hash, "masthead_fingerprint", _never)

    keys = profiles._instruction_fingerprints(b"x", "pdf")

    assert keys and all(k.startswith("doc:") for k in keys)


def test_the_fingerprint_extracts_text_once_and_never_renders_a_page(monkeypatch) -> None:
    """The peek fires on every file pick, on the event loop. It used to extract the text
    twice (strict key, legacy key) through ``to_upload`` — which, for a scan, rasterizes
    EVERY page to an image the fingerprint never reads: 1.6 s for a five-page statement."""
    import backend.services.statements.parser as parser_mod

    calls: list[bool] = []
    real = parser_mod.pdf_text

    def counting(content, *, layout=True):
        calls.append(layout)
        return real(content, layout=layout)

    def never_render(*a, **kw):
        raise AssertionError("the fingerprint must not go through the page renderer")

    monkeypatch.setattr(parser_mod, "pdf_text", counting)
    monkeypatch.setattr(parser_mod, "to_upload", never_render)
    monkeypatch.setattr(parser_mod, "_pdf_to_upload", never_render)

    keys = profiles._instruction_fingerprints(JUNE, "pdf")

    assert calls == [False]
    assert keys == [page_hash.masthead_fingerprint(JUNE)]


def test_an_oversized_page_renders_a_bounded_band() -> None:
    """The peek fires on every file pick, so the render must be bounded by geometry it does
    not trust: a 523-byte PDF declaring a 2,000,000-pt-tall page would rasterize 256 MB at
    the width-capped scale; the band is bounded to 256 KB whatever the page says."""
    tall = pymupdf.open()
    tall.new_page(width=2048, height=2_000_000).insert_text((100, 300), "TALL", fontsize=200)
    wide = pymupdf.open()
    wide.new_page(width=14400, height=14400).insert_text((100, 900), "WIDE", fontsize=600)

    for pdf in (tall.tobytes(), wide.tobytes()):
        band = page_hash._render_band(pdf)
        if band is not None:
            samples, width, height, _ = band
            assert len(samples) <= page_hash._MAX_WIDTH * page_hash._MAX_BAND_HEIGHT
            assert width * height <= page_hash._MAX_WIDTH * page_hash._MAX_BAND_HEIGHT

    assert page_hash._render_band(tall.tobytes()) is None
    assert page_hash._render_band(wide.tobytes()) is not None


def test_a_degenerate_page_has_no_key() -> None:
    flat = pymupdf.open()
    flat.new_page(width=600, height=1)

    assert page_hash.masthead_fingerprint(flat.tobytes()) is None


def test_garbage_or_a_blank_first_page_has_no_key() -> None:
    """No structure, no identity: a blank masthead would key every such document alike."""
    blank = pymupdf.open()
    blank.new_page(width=_W, height=_H)

    assert page_hash.masthead_fingerprint(b"not a pdf") is None
    assert page_hash.masthead_fingerprint(blank.tobytes()) is None
