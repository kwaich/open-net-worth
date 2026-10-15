"""The signed ``.shortcut`` files this instance serves to a phone.

iOS imports only signed shortcut files, and signing needs Apple's ``shortcuts sign`` on a
Mac signed in to iCloud — so it happens once, at release (``make ios-shortcuts-sign``), and
the results are committed under ``signed/`` beside a manifest pinning, per shortcut, BOTH the
unsigned source it was signed from and the signed bytes themselves.

Both halves matter. A source mismatch means the template changed since signing — serving it
would install yesterday's shortcut. A signed-bytes mismatch means the file was swapped: every
user pastes their API token into this shortcut, and ``--mode anyone`` lets any Apple ID sign
a look-alike that also posts the code elsewhere. Either way the file is treated as absent.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, Optional

from .catalog import ShortcutSpec
from .plist import to_bytes

SIGNED_DIR = Path(__file__).resolve().parent / "signed"
MANIFEST = SIGNED_DIR / "manifest.json"


def source_digest(spec: ShortcutSpec) -> str:
    """sha256 of the unsigned build — what a signature was taken over."""
    return hashlib.sha256(to_bytes(spec.build())).hexdigest()


def signed_path(spec: ShortcutSpec) -> Path:
    return SIGNED_DIR / f"{spec.slug}.shortcut"


def _manifest() -> Dict[str, Dict[str, str]]:
    try:
        return json.loads(MANIFEST.read_text())
    except (OSError, ValueError):
        return {}


def _read(spec: ShortcutSpec) -> Optional[bytes]:
    try:
        return signed_path(spec).read_bytes()
    except OSError:
        return None


def signed_bytes(spec: ShortcutSpec) -> Optional[bytes]:
    """The signed file for ``spec``, or ``None`` when missing, stale or not the file signed."""
    entry = _manifest().get(spec.slug) or {}
    payload = _read(spec)
    if payload is None or entry.get("source") != source_digest(spec):
        return None
    if entry.get("signed") != hashlib.sha256(payload).hexdigest():
        return None
    return payload


def is_current(spec: ShortcutSpec) -> bool:
    return signed_bytes(spec) is not None


def record(spec: ShortcutSpec) -> None:
    """Pin ``signed_path(spec)`` — just signed from the current build — in the manifest."""
    payload = signed_path(spec).read_bytes()
    manifest = _manifest()
    manifest[spec.slug] = {
        "source": source_digest(spec),
        "signed": hashlib.sha256(payload).hexdigest(),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
