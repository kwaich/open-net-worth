"""Render a download URL as a QR code, as a data URI.

A data URI rather than raw SVG markup so the frontend can put it in an ``<img
src>`` instead of ``dangerouslySetInnerHTML``; and rather than a second endpoint
so the URL — which carries a download ticket — never becomes its own request the
browser logs and caches separately.
"""

from __future__ import annotations

# Sized for scanning a laptop screen from phone distance without dominating the
# settings panel. Error level M tolerates a little glare and still stays compact.
_SCALE = 5
_BORDER = 4  # the spec's quiet zone; under 4 modules some cameras miss it
_ERROR = "m"


def data_uri(url: str) -> str:
    """Return ``data:image/svg+xml;...`` encoding ``url`` as a QR code."""
    import segno

    # Dark modules on an explicit white ground: a transparent background vanished on a dark
    # theme, and phone cameras need the light quiet zone around the code to lock on.
    return segno.make(url, error=_ERROR).svg_data_uri(
        scale=_SCALE, border=_BORDER, dark="#000000", light="#ffffff"
    )
