"""backend/core/version.py — one version string, read from the installed distribution."""

from __future__ import annotations

from importlib import metadata

import backend.core.version as mod


def test_falls_back_to_the_dev_sentinel_when_not_installed(monkeypatch):
    def missing(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(mod.metadata, "version", missing)
    assert mod.app_version() == mod.DEV_VERSION


def test_reads_the_installed_distribution(monkeypatch):
    monkeypatch.setattr(
        mod.metadata, "version", lambda name: "9.9.9" if name == mod.DISTRIBUTION else "wrong"
    )
    assert mod.app_version() == "9.9.9"


def test_image_without_a_distribution_reads_the_baked_version(monkeypatch, tmp_path):
    def missing(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    baked = tmp_path / "VERSION"
    baked.write_text("1.2.3\n")
    monkeypatch.setattr(mod.metadata, "version", missing)
    monkeypatch.setattr(mod, "BAKED_VERSION_FILE", baked)
    assert mod.app_version() == "1.2.3"
    baked.write_text("\n")
    assert mod.app_version() == mod.DEV_VERSION


def test_health_reports_the_running_version():
    from fastapi.testclient import TestClient

    from backend.main import app

    body = TestClient(app).get("/api/health").json()
    assert body == {
        "status": "ok",
        "version": mod.APP_VERSION,
        "gateway": False,
        "contracts": {"gateway": 1, "move": 1},
    }
