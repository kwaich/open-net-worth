"""backend/core/shipped_assets.py — the repo-checkout-vs-installed-wheel asset seam, and the
scripts/bundle_assets.py step that fills backend/_bundled/ for the wheel."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import backend.core.shipped_assets as mod

REPO = Path(__file__).resolve().parent.parent


def test_repo_copy_wins_even_when_a_stale_bundled_copy_exists(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "frontend" / "dist").mkdir(parents=True)
    (tmp_path / "bundled" / "frontend" / "dist").mkdir(parents=True)
    monkeypatch.setattr(mod, "REPO_ROOT", repo)
    monkeypatch.setattr(mod, "BUNDLED_DIR", tmp_path / "bundled")

    assert mod.shipped_asset("frontend/dist") == repo / "frontend" / "dist"


def test_falls_back_to_the_bundled_copy_when_there_is_no_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path / "site-packages")
    monkeypatch.setattr(mod, "BUNDLED_DIR", tmp_path / "bundled")

    assert mod.shipped_asset("docs/product") == tmp_path / "bundled" / "docs" / "product"


def test_this_checkout_resolves_the_tracked_product_docs():
    assert mod.PRODUCT_DOCS_DIR == REPO / "docs" / "product"
    assert mod.PRODUCT_DOCS_DIR.is_dir()


def test_paths_reexports_the_same_product_docs_dir():
    from backend.core.paths import PRODUCT_DOCS_DIR

    assert PRODUCT_DOCS_DIR == mod.PRODUCT_DOCS_DIR


# ── scripts/bundle_assets.py ──────────────────────────────────────────────────


def _bundle_script():
    spec = importlib.util.spec_from_file_location(
        "bundle_assets", REPO / "scripts" / "bundle_assets.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_repo(root: Path, *, built: tuple[str, ...]) -> None:
    if "frontend" in built:
        (root / "frontend" / "dist" / "assets").mkdir(parents=True)
        (root / "frontend" / "dist" / "index.html").write_text('<div id="root"></div>')
        (root / "frontend" / "dist" / "assets" / "app.js").write_text("x")
        (root / "frontend" / "dist" / "assets" / "app.js.map").write_text("{}")
    if "docs-site" in built:
        (root / "docs-site" / "dist-embed").mkdir(parents=True)
        (root / "docs-site" / "dist-embed" / "index.html").write_text("<html>")
    if "product" in built:
        (root / "docs" / "product").mkdir(parents=True)
        (root / "docs" / "product" / "index.md").write_text("# hi")


def test_bundle_refuses_when_an_asset_is_not_built(tmp_path, monkeypatch, capsys):
    bundle = _bundle_script()
    _fake_repo(tmp_path, built=("frontend", "product"))
    monkeypatch.setattr(bundle, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bundle, "BUNDLED_DIR", tmp_path / "backend" / "_bundled")

    assert bundle.main() == 1
    assert "docs-site/dist-embed" in capsys.readouterr().err
    assert not (tmp_path / "backend" / "_bundled").exists()


def test_bundle_copies_all_three_drops_stale_content_and_source_maps(tmp_path, monkeypatch):
    bundle = _bundle_script()
    _fake_repo(tmp_path, built=("frontend", "docs-site", "product"))
    bundled = tmp_path / "backend" / "_bundled"
    bundled.mkdir(parents=True)
    (bundled / "stale.txt").write_text("from an earlier build")
    monkeypatch.setattr(bundle, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bundle, "BUNDLED_DIR", bundled)

    assert bundle.main() == 0

    assert (bundled / "frontend" / "dist" / "index.html").is_file()
    assert (bundled / "frontend" / "dist" / "assets" / "app.js").is_file()
    assert not (bundled / "frontend" / "dist" / "assets" / "app.js.map").exists()
    assert (bundled / "docs-site" / "dist-embed" / "index.html").is_file()
    assert (bundled / "docs" / "product" / "index.md").is_file()
    assert not (bundled / "stale.txt").exists()
