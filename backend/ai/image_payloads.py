"""Validation and resource bounds for model-facing image payloads.

The stored transcript may retain user-visible attachment metadata, but raw image bytes
crossing into a model process are a bounded resource. Every interactive channel enters
through ``validate_message_images``; CLI transports validate again at their own boundary
so legacy history or a non-chat caller cannot bypass the limits.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence

from backend.core.errors import ValidationError

MAX_IMAGES_PER_TURN = 40
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 40 * 1024 * 1024

IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def message_image_payloads(messages: Sequence[dict]) -> list[dict]:
    """Extract base64 image sources, rejecting shapes this boundary cannot validate."""
    images: list[dict] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "image":
                continue
            source = block.get("source")
            if (
                not isinstance(source, dict)
                or source.get("type") not in (None, "base64")
                or "data" not in source
            ):
                raise ValidationError("Each image block must use a base64 image source.")
            images.append(source)
    return images


def decode_image_payloads(images: Sequence[dict], *, enforce_count: bool = True) -> list[bytes]:
    """Strictly decode supported images under per-image and aggregate byte bounds.

    ``enforce_count=False`` exists only for the reader-fallback boundary: its extractor
    owns a stricter fail-closed page-count refusal, which must remain an honest chat
    response instead of becoming a generic request error. Byte admission still happens
    before that extractor is called.
    """
    if enforce_count and len(images) > MAX_IMAGES_PER_TURN:
        raise ValidationError(f"A chat turn can include at most {MAX_IMAGES_PER_TURN} images.")

    decoded: list[bytes] = []
    total = 0
    for index, image in enumerate(images, start=1):
        media_type = str(image.get("media_type") or "").strip().lower()
        if media_type not in IMAGE_EXTENSIONS:
            raise ValidationError(
                f"Unsupported image type for image {index}: {media_type or 'missing'}."
            )
        data = image.get("data")
        if not isinstance(data, str):
            raise ValidationError(f"Image {index} must contain valid base64 data.")
        # Reject an obviously oversized encoded value before allocating its decoded copy.
        max_encoded = ((MAX_IMAGE_BYTES + 2) // 3) * 4
        if len(data) > max_encoded:
            raise ValidationError(f"Each image can be at most {MAX_IMAGE_BYTES} bytes.")
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValidationError(f"Image {index} must contain valid base64 data.") from exc
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValidationError(f"Each image can be at most {MAX_IMAGE_BYTES} bytes.")
        total += len(raw)
        if total > MAX_TOTAL_IMAGE_BYTES:
            raise ValidationError(
                f"The combined image payload can be at most {MAX_TOTAL_IMAGE_BYTES} bytes."
            )
        decoded.append(raw)
    return decoded


def validate_message_images(messages: Sequence[dict]) -> None:
    """Validate every raw image admitted by one normalized conversation request."""
    decode_image_payloads(message_image_payloads(messages))
