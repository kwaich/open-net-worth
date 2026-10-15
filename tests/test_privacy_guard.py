"""The commit/PR guard that keeps real personal and financial data out of the repo.

A personal-finance app built with AI agents keeps real data close at hand (a live book,
real statements for QA, screenshots of real screens). pii_lint catches ID/phone/account
SHAPES and gitleaks catches secrets; this guard catches the rest — see its module docstring.
"""

from __future__ import annotations

import pytest

from backend.scripts import privacy_guard as pg


@pytest.mark.parametrize(
    "path",
    [
        "data/onw.db",
        "backups/onw.db-wal",
        "secrets/server.pem",
        ".env",
        "backend/.env.local",
        "exports/dbs-june.ofx",
        "statements/2026-09-01_Statement.pdf",
        "notes/spending.csv",
        "docs/qa/shots/dashboard.png",
        "frontend/src/components/screenshot.jpg",
    ],
)
def test_files_that_carry_real_data_are_refused(path):
    assert pg.check_path(path)


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        "tests/fixtures/statement-synthetic.pdf",
        "qa/browser-use/fixtures/statement-import-handoff.ofx",
        "frontend/public/connector-logos/dbs.svg",
        "frontend/public/icons/app.png",
        "desktop/resources/icon.png",
        "backend/services/ledger.py",
    ],
)
def test_synthetic_fixtures_product_art_and_code_pass(path):
    assert pg.check_path(path) is None


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("see /Users/jdoe/code/app", True),  # privacy-guard: ok
        ("C:\\Users\\Jane Doe\\Documents", True),  # privacy-guard: ok
        ("/home/onw/.open-net-worth", False),  # the Docker image's own user
        ("/Users/owner/code/x", False),
        ("/Users/$USER/.cache", False),
        ("a tiny PATH/HOME/TMPDIR/locale", False),
        ("/homebrew/bin/claude", False),
    ],
)
def test_local_home_paths_name_a_real_user_unless_a_placeholder(text, flagged):
    assert bool(pg.LOCAL_PATH.search(text)) is flagged


def test_a_denylist_hit_is_reported_by_number_never_by_the_term(monkeypatch):
    monkeypatch.setenv("PRIVACY_DENYLIST", "Quokka Holdings\nqa-secret-merchant")
    monkeypatch.setenv("ONW_PRIVACY_DENYLIST", "/nonexistent")
    monkeypatch.setattr(pg, "_changed_files", lambda mode, rng: ["docs/note.md"])
    monkeypatch.setattr(
        pg, "_added_lines", lambda mode, rng, path: [(3, "paid QUOKKA holdings again")]
    )
    findings = pg.scan("staged")
    assert findings == ["docs/note.md:3: matches private denylist entry #1"]
    assert "quokka" not in " ".join(findings).lower()  # a public CI log can't leak the term


def test_the_allow_marker_passes_a_synthetic_match(monkeypatch):
    monkeypatch.setenv("PRIVACY_DENYLIST", "")
    monkeypatch.setenv("ONW_PRIVACY_DENYLIST", "/nonexistent")
    monkeypatch.setattr(pg, "_changed_files", lambda mode, rng: ["tests/x.py"])
    monkeypatch.setattr(
        pg,
        "_added_lines",
        lambda mode, rng, path: [(1, 'p = "/Users/jdoe/x"  # privacy-guard: ok')],
    )
    assert pg.scan("staged") == []
