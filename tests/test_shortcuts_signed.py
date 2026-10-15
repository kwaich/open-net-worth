"""The shipped shortcuts must be signed from the CURRENT template.

iOS refuses an unsigned ``.shortcut`` file, and a signed one is sealed — so a template change
that is not re-signed would ship either nothing installable or yesterday's shortcut. Signing
needs macOS + iCloud, so it cannot run in CI; this test is the gate that makes a forgotten
``make ios-shortcuts-sign`` fail loudly instead.
"""

import pytest

from backend.services.shortcuts import signed
from backend.services.shortcuts.catalog import SPECS


@pytest.mark.parametrize("spec", SPECS, ids=[s.slug for s in SPECS])
def test_shipped_shortcut_is_signed_from_the_current_build(spec):
    assert signed.signed_path(spec).is_file(), (
        f"{spec.slug} has no signed file — run `make ios-shortcuts-sign` on a Mac"
    )
    assert signed.is_current(spec), (
        f"{spec.slug} was signed from an older template — run `make ios-shortcuts-sign`"
    )


@pytest.mark.parametrize("spec", SPECS, ids=[s.slug for s in SPECS])
def test_a_signed_file_is_not_a_bare_plist(spec):
    """``shortcuts sign`` wraps the plist in an Apple Encrypted Archive; a raw ``bplist`` copied
    in by hand would be served as "signed" and refused on the phone."""
    if not signed.signed_path(spec).is_file():
        pytest.skip("not signed yet — the test above reports it")
    assert signed.signed_path(spec).read_bytes()[:4] == b"AEA1"
