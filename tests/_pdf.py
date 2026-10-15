"""A real one-page PDF for upload tests, built with pypdf."""

from __future__ import annotations

import io

import pypdf


def blank_pdf(*, user_password: str | None = None) -> bytes:
    """`user_password=""` encrypts with an owner password only — readable without one, as many
    bank statements are; any other string is needed to open it at all."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    if user_password is not None:
        writer.encrypt(user_password=user_password, owner_password="owner")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
