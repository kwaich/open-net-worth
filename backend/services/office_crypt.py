"""Password-protected Office document (xlsx/xls) detection + decryption.

A password-protected OOXML workbook is stored as an OLE/CFB container wrapping the real
(encrypted) ZIP payload — a ZIP-based reader (``openpyxl``, ``xlrd``) sees a file that simply
"is not a zip file" and raises accordingly, giving the user no hint it is a password problem
rather than a corrupt/unsupported one. ``msoffcrypto`` recognises + decrypts the container.

One implementation, reused by every ingress that can receive an Office attachment: the
chat/email upload path (``api/upload.py``) and the statements/import pipeline
(``services/statements/parser.py``, ``services/import_xlsx.py``) — instead of writing this
detection twice and letting the two drift (the statements/import path never got it the first
time around: password-protected .xlsx surfaced as a raw ``zipfile.BadZipFile`` message with
no hint it was a password problem).
"""

from __future__ import annotations

import io

import msoffcrypto


def decrypt_office(
    content: bytes,
    password: str | None,
    *,
    resend_hint: str = "Resend it with the password.",
) -> tuple[bytes, bool]:
    """Returns ``(content, was_encrypted)``.

    Raises ``ValueError`` with a user-correctable, greppable message on failure — the
    ``"password_required:"`` prefix is a stable contract other callers pattern-match on
    (e.g. ``connectors/telegram/handlers.py``), so keep it verbatim if you touch this.
    A file that isn't a recognisable Office container at all (not just unencrypted) is left
    to the caller's own reader to report — ``is_encrypted()`` only trips on a real OLE/CFB
    wrapper, so a plain-zip xlsx/xls sails through unchanged.
    """
    f = io.BytesIO(content)
    try:
        office = msoffcrypto.OfficeFile(f)
    except Exception:
        return content, False
    if not office.is_encrypted():
        return content, False
    if not password:
        raise ValueError(f"password_required: This file is password-protected. {resend_hint}")
    try:
        office.load_key(password=password)
        out = io.BytesIO()
        office.decrypt(out)
        return out.getvalue(), True
    except Exception:
        raise ValueError(f"Wrong password. {resend_hint}")
