"""Bounds and validation shared by every native chat-image transport."""

from __future__ import annotations

import base64

import pytest

from backend.ai import image_payloads
from backend.core.errors import ValidationError


def _image(data: bytes, media_type: str = "image/png") -> dict:
    return {"media_type": media_type, "data": base64.b64encode(data).decode()}


def test_image_payloads_reject_an_unsupported_media_type():
    with pytest.raises(ValidationError, match="Unsupported image type"):
        image_payloads.decode_image_payloads([_image(b"x", "image/svg+xml")])


def test_image_payloads_reject_one_oversized_image(monkeypatch):
    monkeypatch.setattr(image_payloads, "MAX_IMAGE_BYTES", 3)

    with pytest.raises(ValidationError, match="at most 3 bytes"):
        image_payloads.decode_image_payloads([_image(b"four")])


def test_image_payloads_reject_an_oversized_combined_turn(monkeypatch):
    monkeypatch.setattr(image_payloads, "MAX_TOTAL_IMAGE_BYTES", 5)

    with pytest.raises(ValidationError, match="combined image payload"):
        image_payloads.decode_image_payloads([_image(b"abc"), _image(b"def")])


def test_image_payloads_decode_a_valid_bounded_turn():
    assert image_payloads.decode_image_payloads([_image(b"one"), _image(b"two")]) == [
        b"one",
        b"two",
    ]


@pytest.mark.parametrize("source", [{"type": "url", "url": "https://example.test/a.png"}, {}])
def test_message_images_reject_non_base64_sources(source):
    messages = [{"role": "user", "content": [{"type": "image", "source": source}]}]

    with pytest.raises(ValidationError, match="base64 image source"):
        image_payloads.validate_message_images(messages)
