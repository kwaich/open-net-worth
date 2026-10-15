"""A text-free identity for a scanned statement: a perceptual hash of page one's masthead.

A statement the bank renders as one image per page has no text to fingerprint, so the
template hash (`profiles._document_fingerprint`) is None and nothing about the source can
be remembered. What IS stable month to month is the look of the top of page one — logo,
headings, column captions — so that band, rendered small and reduced to a grid of
brightness gradients (dHash), stands in for the missing template text.

The render is this module's own, at fixed parameters, deliberately not the parse
rendering: the hash is an IDENTITY and must not move when the parser's rendering changes
(the scar in `test_statement_fingerprint_stability`).

Measured: five monthly statements of one real source sit at most 1 bit apart (36–37 of the
120 bits set); unrelated real documents 40–49 apart. Synthetic sources that share a template
and differ only in the bank's name and address sit 5–13 apart — and the TEXT key separates
those, because name and address are digit-free template lines. So the tolerance is set from
the same-source drift, not from the gap to other sources: `MAX_DISTANCE` is twice the
observed drift, and a source that drifts further mints a stray profile (re-pick the account
once) rather than landing on another bank's accounts and parse instructions.

A key is an identity only when it carries structure at BOTH poles. A blank band is all
zeros; a band under a monotone brightness ramp — uneven lighting on a photographed page, a
header gradient — is all ONES, since every right-of-left comparison tilts the same way, and
every such document would mint the same key. So a hash with fewer than `_MIN_BITS` set OR
fewer than `_MIN_BITS` clear is refused at mint and ignored at lookup; a stored key that
fails `is_image_key` (prefix, exact lowercase hex, balance) matches nothing. De-trending the
rows instead (row-median or endpoint-slope thresholds) was measured and rejected: it keeps
ramped sources 15–26 apart but costs same-source stability (median: real drift 1 → 2;
slope: a 3 px offset 6 → 16 bits).
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

PREFIX = "img:"
MAX_DISTANCE = 2
_MIN_BITS = 12

_SCALE = 0.5
_MAX_WIDTH = 1024
_MAX_BAND_HEIGHT = 256
_BAND = 0.25
_COLS = 16
_ROWS = 8
_BITS = _ROWS * (_COLS - 1)
_HEX_WIDTH = (_BITS + 3) // 4
_KEY_BODY = re.compile(rf"[0-9a-f]{{{_HEX_WIDTH}}}")


def is_image_key(fingerprint: str) -> bool:
    """A well-formed image key with enough structure at both poles to be an identity."""
    if not fingerprint.startswith(PREFIX) or not _KEY_BODY.fullmatch(fingerprint[len(PREFIX) :]):
        return False
    ones = int(fingerprint[len(PREFIX) :], 16).bit_count()
    return min(ones, _BITS - ones) >= _MIN_BITS


def distance(a: str, b: str) -> int:
    return (int(a[len(PREFIX) :], 16) ^ int(b[len(PREFIX) :], 16)).bit_count()


def masthead_fingerprint(content: bytes) -> str | None:
    band = _render_band(content)
    if band is None:
        return None
    cells = _cell_means(band)
    bits = "".join("1" if row[j + 1] > row[j] else "0" for row in cells for j in range(_COLS - 1))
    key = f"{PREFIX}{int(bits, 2):0{_HEX_WIDTH}x}"
    return key if is_image_key(key) else None


def _render_band(content: bytes) -> tuple[bytes, int, int, int] | None:
    """The top band of page one, grayscale, at a fixed small scale, as
    (samples, width, height, stride). None when the bytes are not a renderable PDF.

    Only the band is rasterized (``clip``), and the scale is capped on BOTH axes, so an
    untrusted upload cannot make this allocate more than ``_MAX_WIDTH * _MAX_BAND_HEIGHT``
    bytes whatever its page geometry says."""
    try:
        import pymupdf

        doc = pymupdf.open(stream=content, filetype="pdf")
    except Exception:  # noqa: BLE001
        logger.debug("page_hash: not a PDF", exc_info=True)
        return None
    try:
        if doc.page_count == 0:
            return None
        page = doc[0]
        rect = page.rect
        if not (rect.width > 0 and rect.height > 0):
            return None
        band_height = rect.height * _BAND
        scale = min(_SCALE, _MAX_WIDTH / rect.width, _MAX_BAND_HEIGHT / band_height)
        band = pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + band_height)
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False, clip=band
        )
        if pix.height < _ROWS or pix.width < _COLS:
            return None
        return bytes(pix.samples), pix.width, pix.height, pix.stride
    except Exception:  # noqa: BLE001
        logger.debug("page_hash: page one is not renderable", exc_info=True)
        return None
    finally:
        doc.close()


def _cell_means(band: tuple[bytes, int, int, int]) -> list[list[float]]:
    samples, width, height, stride = band
    cells: list[list[float]] = []
    for i in range(_ROWS):
        y0, y1 = i * height // _ROWS, (i + 1) * height // _ROWS
        row: list[float] = []
        for j in range(_COLS):
            x0, x1 = j * width // _COLS, (j + 1) * width // _COLS
            total = 0
            for y in range(y0, y1):
                total += sum(samples[y * stride + x0 : y * stride + x1])
            row.append(total / ((y1 - y0) * (x1 - x0)))
        cells.append(row)
    return cells
