"""Write the shortcut files: unsigned placeholders, and (``--sign``) the signed copies we ship.

The shortcuts are defined in ``backend.services.shortcuts.catalog`` — one generator, so
the file a stranger inspects in the repo and the file a phone installs can never drift.

* ``docs/shortcuts/dist/`` — unsigned placeholders, readable and diffable. iOS will not
  import these (it refuses unsigned shortcut files); they are the source a signature is
  taken over, and what someone building by hand can read.
* ``backend/services/shortcuts/signed/`` — the same builds after ``shortcuts sign --mode
  anyone``, plus a manifest of the source hash each was signed from. Only macOS can sign,
  and only when signed in to iCloud; the signer's Apple ID name is what installers see.

Run ``make ios-shortcuts`` (placeholders) or ``make ios-shortcuts-sign`` (at release).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from backend.services.shortcuts import signed
from backend.services.shortcuts.catalog import SPECS
from backend.services.shortcuts.plist import write_shortcut

DIST = Path(__file__).resolve().parents[2] / "docs" / "shortcuts" / "dist"
_SIGN_TIMEOUT = 120


def _sign(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["shortcuts", "sign", "--mode", "anyone", "--input", str(source), "--output", str(dest)],
        check=True,
        timeout=_SIGN_TIMEOUT,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DIST, help="where the placeholders go")
    parser.add_argument("--sign", action="store_true", help="also sign them (macOS only)")
    args = parser.parse_args()

    if args.sign and shutil.which("shortcuts") is None:
        sys.exit("`shortcuts` not found — signing needs macOS 12+ signed in to iCloud")

    for spec in SPECS:
        path = write_shortcut(spec.build(), args.out / spec.filename)
        print(f"wrote {path}")
        if args.sign:
            _sign(path, signed.signed_path(spec))
            signed.record(spec)
            print(f"signed {signed.signed_path(spec)}")


if __name__ == "__main__":
    main()
