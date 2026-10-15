"""Where the read-only assets the app serves live: a repo checkout vs an installed wheel.

The built frontend (`frontend/dist/`), the embedded docs site (`docs-site/dist-embed/`) and the
product-docs corpus (`docs/product/`) sit OUTSIDE `backend/` in the repository. A wheel can only
carry files inside its package, so `scripts/bundle_assets.py` copies each of them to
`backend/_bundled/<same relative path>` right before `uv build`.

Resolution prefers the REPO copy whenever one exists — a checkout, `make setup`, Docker (which
COPYs the sources) — so a stale `_bundled/` left over from an earlier local wheel build can never
shadow live source. The bundled copy is used only where there is no repository around the
package at all: an installed wheel.

Stdlib-only, like everything under core/.
"""

from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND.parent
BUNDLED_DIR = BACKEND / "_bundled"


def shipped_asset(relative: str) -> Path:
    """Resolve a repo-relative, read-only asset directory for a checkout or a wheel."""
    repo_copy = REPO_ROOT / relative
    if repo_copy.exists():
        return repo_copy
    return BUNDLED_DIR / relative


FRONTEND_DIST_DIR = shipped_asset("frontend/dist")
DOCS_SITE_DIST_DIR = shipped_asset("docs-site/dist-embed")
PRODUCT_DOCS_DIR = shipped_asset("docs/product")
