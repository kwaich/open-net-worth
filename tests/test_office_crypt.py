"""Unit tests for the shared password-protected Office document helper
(services/office_crypt.py) — reused by api/upload.py (chat/email ingest) and the
statements/import pipeline (services/statements/parser.py, services/import_xlsx.py) so the
two ingresses can't drift on how a password-protected xlsx/xls is detected and decrypted.

A real encrypted OOXML file is an OLE/CFB container msoffcrypto (decrypt-only) offers no way
to author in a test, so ``msoffcrypto.OfficeFile`` is faked at the module boundary — this
still exercises the real detection/branching logic in ``decrypt_office`` itself."""

from __future__ import annotations

import io

import pytest

from backend.services import office_crypt


class _Plain:
    """A real, unencrypted-but-recognisable Office container."""

    def __init__(self, _f):
        pass

    def is_encrypted(self):
        return False


class _Encrypted:
    def __init__(self, _f):
        pass

    def is_encrypted(self):
        return True

    def load_key(self, password):
        if password != "hunter2":
            raise ValueError("bad key")

    def decrypt(self, out):
        out.write(b"decrypted-payload")


def test_unencrypted_file_passes_through_unchanged(monkeypatch):
    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _Plain)
    content, was_encrypted = office_crypt.decrypt_office(b"plain-bytes", None)
    assert content == b"plain-bytes"
    assert was_encrypted is False


def test_unrecognised_container_is_left_to_the_caller(monkeypatch):
    """``msoffcrypto.OfficeFile`` itself failing to even construct (not a recognisable Office
    container at all) is NOT this module's problem to report — it hands the bytes back
    unchanged so the caller's own reader (openpyxl/xlrd) raises its own actionable error."""

    class _Unrecognised:
        def __init__(self, _f):
            raise Exception("not an office file")

    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _Unrecognised)
    content, was_encrypted = office_crypt.decrypt_office(b"garbage", None)
    assert content == b"garbage"
    assert was_encrypted is False


def test_encrypted_without_password_raises_password_required(monkeypatch):
    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _Encrypted)
    with pytest.raises(ValueError) as exc:
        office_crypt.decrypt_office(b"encrypted", None)
    assert str(exc.value).startswith("password_required:")


def test_encrypted_wrong_password_raises_a_distinct_message(monkeypatch):
    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _Encrypted)
    with pytest.raises(ValueError) as exc:
        office_crypt.decrypt_office(b"encrypted", "nope")
    assert str(exc.value).startswith("Wrong password.")
    assert not str(exc.value).startswith("password_required:")


def test_encrypted_correct_password_decrypts(monkeypatch):
    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _Encrypted)
    content, was_encrypted = office_crypt.decrypt_office(b"encrypted", "hunter2")
    assert content == b"decrypted-payload"
    assert was_encrypted is True


def test_resend_hint_is_threaded_into_both_messages(monkeypatch):
    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _Encrypted)
    with pytest.raises(ValueError) as exc:
        office_crypt.decrypt_office(b"encrypted", None, resend_hint="Try again, pal.")
    assert "Try again, pal." in str(exc.value)


def test_real_msoffcrypto_smoke_on_plain_bytes():
    """Sanity check against the REAL msoffcrypto (not the fake) — a plain, non-Office blob
    (a CSV) is simply not a recognisable Office container, and decrypt_office leaves it
    untouched rather than raising, so a genuinely non-xlsx upload's OWN reader reports it."""
    content, was_encrypted = office_crypt.decrypt_office(
        b"Date,Amount,Desc\n2026-01-01,-4.5,Coffee\n", None
    )
    assert was_encrypted is False
    assert content == b"Date,Amount,Desc\n2026-01-01,-4.5,Coffee\n"


def test_real_msoffcrypto_smoke_on_real_unencrypted_xlsx():
    """A REAL (unencrypted) xlsx built via openpyxl round-trips through the real
    msoffcrypto — ``is_encrypted()`` must read False so every plain xlsx upload is
    completely unaffected by this change."""
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["Date", "Amount"])
    buf = io.BytesIO()
    wb.save(buf)
    raw = buf.getvalue()
    content, was_encrypted = office_crypt.decrypt_office(raw, None)
    assert was_encrypted is False
    assert content == raw
