"""A source's fingerprint is its IDENTITY — it must not move.

Two live incidents, both silent:

1. The hash was computed over the PARSE rendering. Switching PDF extraction to
   ``extraction_mode="layout"`` (needed to recover the withdrawal/deposit columns) rewrote
   every line, so every stored profile became unaddressable. The next import minted an empty
   profile beside it and the owner's three chosen accounts, instructions and match policy were
   stranded. Re-importing could not heal it — a lookup cannot find what it can no longer
   address, so it just wrote a second blank row.

2. A Meridian Bank consolidated statement draws its issue date in a custom font that maps digits to
   CONTROL-CHARACTER glyph codes. ``str.isdigit`` sees no digit there, so the date sailed
   through the "digit-free template lines" filter and into the hash — a different fingerprint
   every month, re-teaching the same source as brand new each time ("when importing june, it
   doesn't show the accounts").

Fixing (2) would have stranded every profile a SECOND time, so the lookup carries a legacy key
and re-keys on hit. That self-heal is what recovered the owner's live data without a migration.
"""

from __future__ import annotations

import pytest

from backend.services.statements import profiles


class _FakeUpload:
    """Stands in for the parser: returns different text per extraction mode, like a real PDF."""

    def __init__(self, *, layout_text: str, reading_text: str):
        self.layout_text = layout_text
        self.reading_text = reading_text
        self.modes: list[bool] = []

    def __call__(self, content: bytes, *, layout: bool = True) -> str:
        self.modes.append(layout)
        return self.layout_text if layout else self.reading_text


_TEMPLATE = "\n".join(
    [
        "CONSOLIDATED STATEMENT",
        "NGUYEN VAN A",
        "SUMMARY OF CURRENCY BREAKDOWN:",
        "ACCOUNT ACCOUNT NO. BALANCE",
        "(BASE CURRENCY)",
        "DEPOSITS",
    ]
)


def _patch_upload(monkeypatch, upload) -> None:
    import backend.services.statements.parser as parser_mod

    monkeypatch.setattr(parser_mod, "pdf_text", upload)


def test_the_hash_ignores_how_the_parser_renders_the_page(monkeypatch) -> None:
    """Incident 1. The identity must come from the STABLE rendering, not the faithful one."""
    upload = _FakeUpload(
        layout_text="   " + _TEMPLATE.replace("\n", "   \n     "),  # column-padded
        reading_text=_TEMPLATE,
    )
    _patch_upload(monkeypatch, upload)

    fp = profiles._document_fingerprint(b"x", "pdf")

    assert fp
    # It asked for reading order — the mode the parse does NOT use, and therefore the one that
    # does not move when the parse changes.
    assert upload.modes == [False]


def test_a_date_drawn_in_control_character_glyphs_is_excluded(monkeypatch) -> None:
    """Incident 2. Same statement, next month: only the font-mapped date differs."""
    august = _TEMPLATE + "\n\x03\x04\x01\x01 \x02\x04\x08\x07%\x04\x02)\x01\x08)\x03\x01\x03\x07,"
    june = _TEMPLATE + "\n\x03\x04\x01\x01 \x02\x04\x08\x07%\x04\x01)\x01\x07)\x03\x01\x03\x07,"

    _patch_upload(monkeypatch, _FakeUpload(layout_text=august, reading_text=august))
    fp_august = profiles._document_fingerprint(b"a", "pdf")
    _patch_upload(monkeypatch, _FakeUpload(layout_text=june, reading_text=june))
    fp_june = profiles._document_fingerprint(b"j", "pdf")

    assert fp_august and fp_august == fp_june


def test_the_legacy_rule_still_lets_that_date_through(monkeypatch) -> None:
    """The legacy key must reproduce the OLD behaviour exactly — including its bug. If it
    silently matched the strict rule it would not find the profiles it exists to find."""
    august = _TEMPLATE + "\n\x03\x04\x01\x01 \x02\x04\x08\x07%\x04\x02)\x01\x08)"
    june = _TEMPLATE + "\n\x03\x04\x01\x01 \x02\x04\x08\x07%\x04\x01)\x01\x07)"

    _patch_upload(monkeypatch, _FakeUpload(layout_text=august, reading_text=august))
    a = profiles._document_fingerprint(b"a", "pdf", legacy=True)
    _patch_upload(monkeypatch, _FakeUpload(layout_text=june, reading_text=june))
    j = profiles._document_fingerprint(b"j", "pdf", legacy=True)

    assert a != j


def test_ordinary_digits_are_still_excluded() -> None:
    assert profiles._is_template_line("consolidated statement") is True
    assert profiles._is_template_line("statement date 18 aug 2026") is False
    assert profiles._is_template_line("\x03\x04\x01") is False


def test_both_keys_are_offered_strict_first(monkeypatch) -> None:
    text = _TEMPLATE + "\n\x03\x04\x01\x01 \x02\x04\x08\x07%"
    _patch_upload(monkeypatch, _FakeUpload(layout_text=text, reading_text=text))

    fps = profiles._instruction_fingerprints(b"x", "pdf")

    assert len(fps) == 2
    assert fps[0] == profiles._document_fingerprint(b"x", "pdf")
    assert fps[1] == profiles._document_fingerprint(b"x", "pdf", legacy=True)


def test_a_profile_found_by_its_LEGACY_key_is_re_keyed(monkeypatch, tmp_path) -> None:
    """The self-heal. Without it the profile still resolves TODAY and breaks again next month,
    because the legacy hash is the month-unstable one."""
    text = _TEMPLATE + "\n\x03\x04\x01\x01 \x02\x04\x08\x07%"
    _patch_upload(monkeypatch, _FakeUpload(layout_text=text, reading_text=text))
    strict, legacy = profiles._instruction_fingerprints(b"x", "pdf")
    assert strict != legacy

    from backend.db.statement_format_store import StatementFormatStore

    store = StatementFormatStore()
    store.upsert(
        legacy,
        kind="document",
        schema={},
        source_label="Statement 11.pdf",
        account_ids=["acc_one", "acc_two", "acc_three"],
        instructions="route the fast collections",
    )

    hit = profiles.ensure_source_profile(b"x", "pdf", source_label="Statement 11.pdf")

    assert hit is not None
    assert hit["fingerprint"] == strict, "must be re-keyed, not merely found"
    # And nothing the owner taught it is lost in the move.
    assert hit["account_ids"] == ["acc_one", "acc_two", "acc_three"]
    assert hit["instructions"] == "route the fast collections"
    # No blank duplicate was minted beside it.
    assert (
        len([p for p in StatementFormatStore().all_profiles() if p["fingerprint"] == strict]) == 1
    )


def test_rekey_refuses_to_collide_with_another_profile() -> None:
    """Two rows may not share a fingerprint — a rekey that would collide is a no-op, never an
    overwrite of somebody else's memory."""
    from backend.db.statement_format_store import StatementFormatStore

    store = StatementFormatStore()
    a = store.upsert("doc:aaa", kind="document", schema={}, source_label="A")
    store.upsert("doc:bbb", kind="document", schema={}, source_label="B")

    assert store.rekey(a["id"], "doc:bbb") is None
    assert store.find_by_id(a["id"])["fingerprint"] == "doc:aaa"


def test_rekey_on_a_missing_profile_is_harmless() -> None:
    from backend.db.statement_format_store import StatementFormatStore

    assert StatementFormatStore().rekey("fmt_nope", "doc:zzz") is None


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
def test_tabular_sources_are_untouched(monkeypatch, fmt) -> None:
    """Only documents are keyed this way; a tabular source keys off its header row."""
    assert profiles.ensure_source_profile(b"a,b\n1,2\n", fmt, source_label="x.csv") is None
