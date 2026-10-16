"""The commit/PR guard that keeps real personal and financial data out of the repo.

A personal-finance app built with AI agents keeps real data close at hand (a live book,
real statements for QA, screenshots of real screens). pii_lint catches ID/phone/account
SHAPES and gitleaks catches secrets; this guard catches the rest — see its module docstring.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.scripts import privacy_guard as pg

HOOK_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent_privacy_hook.py"


@pytest.fixture(autouse=True)
def _synthetic_denylist_only(tmp_path, monkeypatch):
    """No test may read the owner's real denylist: HOME and both env sources are redirected."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("ONW_PRIVACY_DENYLIST", str(tmp_path / "no-such-denylist.txt"))
    monkeypatch.delenv("PRIVACY_DENYLIST", raising=False)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway git repo (one root commit) the guard is pointed at instead of the checkout."""
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(pg, "ROOT", root)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    git("init", "-q", "-b", "main")
    (root / "README.md").write_text("synthetic\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "root")
    return SimpleNamespace(root=root, git=git)


def _write(repo, rel: str, text: str | bytes) -> None:
    target = repo.root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        target.write_bytes(text)
    else:
        target.write_text(text, encoding="utf-8")


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


def _lines(source_lines):
    return lambda source, path: source_lines


def test_a_denylist_hit_is_reported_by_number_never_by_the_term(monkeypatch):
    monkeypatch.setenv("PRIVACY_DENYLIST", "Quokka Holdings\nqa-secret-merchant")
    monkeypatch.setattr(pg, "_sources", lambda mode, rng: iter([pg._Source("", ("docs/note.md",))]))
    monkeypatch.setattr(pg, "_added_lines", _lines([(3, "paid QUOKKA holdings again")]))
    findings = pg.scan("staged")
    assert findings == ["docs/note.md:3: matches private denylist entry #1"]
    assert "quokka" not in " ".join(findings).lower()  # a public CI log can't leak the term


# --- hole 5: the allow marker is for synthetic home paths, never for the denylist --------------


def _scan_one(monkeypatch, path, text):
    monkeypatch.setattr(pg, "_sources", lambda mode, rng: iter([pg._Source("", (path,))]))
    monkeypatch.setattr(pg, "_added_lines", _lines([(1, text)]))
    return pg.scan("staged")


def test_the_allow_marker_passes_a_synthetic_home_path(monkeypatch):
    assert _scan_one(monkeypatch, "tests/x.py", 'p = "/Users/jdoe/x"  # privacy-guard: ok') == []


def test_the_allow_marker_never_silences_a_denylist_match(monkeypatch):
    monkeypatch.setenv("PRIVACY_DENYLIST", "Quokka Holdings")
    found = _scan_one(monkeypatch, "docs/x.md", "Quokka Holdings  <!-- privacy-guard: ok -->")
    assert found == ["docs/x.md:1: matches private denylist entry #1"]


@pytest.mark.parametrize("path", ["LICENSE", "NOTICE"])
def test_the_marker_covers_the_denylist_only_in_license_and_notice(monkeypatch, path):
    monkeypatch.setenv("PRIVACY_DENYLIST", "Quokka Holdings")
    text = "Copyright (c) Quokka Holdings  privacy-guard: ok"
    assert _scan_one(monkeypatch, path, text) == []
    assert _scan_one(monkeypatch, path, "Copyright (c) Quokka Holdings")  # unmarked: still flagged


def test_the_guard_s_own_file_is_exempt_from_the_path_rule_but_not_the_denylist(monkeypatch):
    monkeypatch.setenv("PRIVACY_DENYLIST", "Quokka Holdings")
    own = "backend/scripts/privacy_guard.py"
    assert _scan_one(monkeypatch, own, "see /Users/jdoe/code") == []  # privacy-guard: ok
    assert _scan_one(monkeypatch, own, "Quokka Holdings")


# --- hole 3: accents, decomposition, Unicode boundaries ----------------------------------------


@pytest.mark.parametrize(
    ("term", "text"),
    [
        ("Quôkka", "paid Quokka rent"),
        ("Quokka", "paid QUÔKKA rent"),
        ("Quôkka", "paid " + unicodedata.normalize("NFD", "Quôkka") + " rent"),
        ("Đokka", "to dokka today"),
        ("Zoë Quokká", "zoe quokka"),
        ("Quokka Holdings", "quokka\u00a0 holdings"),  # NBSP and double space
        ("Quokka", "Quo\u200bkka"),  # zero-width space
        ("Müller", "MULLER & co"),
    ],
)
def test_denylist_matches_across_accents_case_and_normalization(term, text):
    (pat,) = pg._patterns([term])
    assert pat.search(pg._fold(text))


@pytest.mark.parametrize(
    ("term", "text"),
    [
        ("Quôkka", "Quokkas"),  # a longer word
        ("Quôkka", "xQuokka"),
        ("Quôkka", "Quôkka\u00e9"),  # followed by a letter (é)
        ("Quokka", "Quokkaville"),
    ],
)
def test_denylist_still_needs_a_whole_word(term, text):
    (pat,) = pg._patterns([term])
    assert not pat.search(pg._fold(text))


def test_underscore_and_punctuation_still_bound_a_denylist_word():
    (pat,) = pg._patterns(["Quokka"])
    assert pat.search(pg._fold("acct_quokka_01"))
    assert pat.search(pg._fold("(Quokka)"))


def test_line_numbers_are_reported_against_the_original_text(repo, monkeypatch):
    monkeypatch.setenv("PRIVACY_DENYLIST", "Quôkka")
    _write(repo, "notes.md", "one\n" + unicodedata.normalize("NFD", "two Quôkka") + "\nthree\n")
    assert pg.scan("worktree") == ["notes.md:2: matches private denylist entry #1"]


# --- hole 1: `git add x && git commit` has nothing staged when the hook runs --------------------


def test_worktree_scan_sees_files_that_are_not_staged_yet(repo):
    _write(repo, "scan/2026-statement.pdf", b"%PDF-1.4")
    _write(repo, "src/app.py", 'HOME = "/Users/jdoe/code"\n')  # privacy-guard: ok
    assert pg.scan("staged") == []  # what the hook used to run: nothing is staged yet
    findings = pg.scan("worktree")
    assert any(f.startswith("scan/2026-statement.pdf:") for f in findings)
    assert "src/app.py:1: a local home path names a real machine user" in findings


def test_worktree_scan_works_before_the_first_commit(tmp_path, monkeypatch):
    root = tmp_path / "fresh"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    monkeypatch.setattr(pg, "ROOT", root)
    _write(SimpleNamespace(root=root), "a.db", b"x")
    assert pg.scan("worktree") and "a.db" in pg.scan("worktree")[0]


def test_worktree_scan_ignores_gitignored_files(repo):
    _write(repo, ".gitignore", "scratch/\n")
    _write(repo, "scratch/statement.pdf", b"%PDF")
    assert pg.scan("worktree") == []


# --- hole 2: a file added then deleted inside the range is still published ----------------------


def test_range_scans_every_commit_not_the_net_diff(repo):
    base = repo.git("rev-parse", "HEAD").strip()
    _write(repo, "leak/statement.pdf", b"%PDF")
    _write(repo, "leak/notes.txt", "token at /home/jdoe/.secret\n")  # privacy-guard: ok
    repo.git("add", ".")
    repo.git("commit", "-qm", "oops")
    repo.git("rm", "-rq", "leak")
    repo.git("commit", "-qm", "remove it again")
    assert repo.git("diff", "--name-only", f"{base}..HEAD") == ""  # the net diff is empty
    findings = pg.scan("range", f"{base}..HEAD")
    assert any("leak/statement.pdf: .pdf outside" in f for f in findings)
    assert any("leak/notes.txt:1: a local home path" in f for f in findings)
    assert all(f.split(":")[0].isalnum() and len(f.split(":")[0]) == 9 for f in findings)


def test_range_content_rules_read_each_commits_own_added_lines(repo, monkeypatch):
    monkeypatch.setenv("PRIVACY_DENYLIST", "Quokka Holdings")
    base = repo.git("rev-parse", "HEAD").strip()
    _write(repo, "a.md", "keep\nQuokka Holdings\n")
    repo.git("add", ".")
    repo.git("commit", "-qm", "one")
    _write(repo, "a.md", "keep\n")
    repo.git("commit", "-qam", "two")
    (finding,) = pg.scan("range", f"{base}..HEAD")
    assert finding.endswith("a.md:2: matches private denylist entry #1")


def test_range_includes_a_root_commit(repo):
    _write(repo, "later.md", "fine\n")
    repo.git("add", ".")
    repo.git("commit", "-qm", "two")
    repo.git("checkout", "-q", "--orphan", "orphan")
    repo.git("rm", "-rqf", ".")
    _write(repo, "dump.sqlite", b"x")
    repo.git("add", ".")
    repo.git("commit", "-qm", "orphan root")
    assert any("dump.sqlite" in f for f in pg.scan("range", "HEAD"))


def test_range_checks_side_branch_commits_and_what_a_merge_adds_itself(repo):
    base = repo.git("rev-parse", "HEAD").strip()
    repo.git("checkout", "-qb", "side")
    _write(repo, "side.db", b"x")
    repo.git("add", ".")
    repo.git("commit", "-qm", "side leak")
    repo.git("rm", "-q", "side.db")
    repo.git("commit", "-qm", "side cleanup")
    repo.git("checkout", "-q", "main")
    _write(repo, "main.md", "x\n")
    repo.git("add", ".")
    repo.git("commit", "-qm", "main work")
    repo.git("merge", "--no-ff", "--no-commit", "-q", "side")
    _write(repo, "merge-only.md", "pasted /Users/jdoe/x\n")  # privacy-guard: ok
    repo.git("add", ".")
    repo.git("commit", "-qm", "merge")
    findings = pg.scan("range", f"{base}..HEAD")
    assert any("side.db" in f for f in findings)  # the side branch's own history
    assert any("merge-only.md:1: a local home path" in f for f in findings)  # the evil merge
    assert not any("main.md" in f for f in findings)  # already-reviewed lines are not re-flagged
    assert len(findings) == 2


def test_range_rejects_an_option_shaped_argument(repo):
    with pytest.raises(ValueError):
        pg.scan("range", "--all")


# --- hole 4: archives and book backups ----------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "open-net-worth-export-2026-10-10.zip",
        "docs/backup.tar.gz",
        "x/book.tgz",
        "x/book.7z",
        "x/book.rar",
        "x/onw.db.bak",
        "x/data.gz",
        "open-net-worth-export.json",
        "backups/onw-backup.json",
        "x/onw-book.json",
        "x/sanitised.json",
        "x/Sanitized-Book.JSON",
    ],
)
def test_archives_and_book_backups_are_refused(path):
    assert pg.check_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "tests/fixtures/synthetic-book.zip",
        "tests/fixtures/onw-book-export.json",
        "backend/scripts/demo/seed.tar.gz",
        "package.json",
        "frontend/src/i18n/en.json",
    ],
)
def test_synthetic_fixture_archives_and_ordinary_json_pass(path):
    assert pg.check_path(path) is None


def test_a_json_with_a_book_export_manifest_is_refused_whatever_its_name(repo):
    manifest = {
        "schema_version": 2,
        "app_version": "1.0.0",
        "exported_at": "2026-10-10T00:00:00+00:00",
        "base_currency": "USD",
        "blob_trees": {},
        "entries": {},
    }
    _write(repo, "notes/manifest.json", json.dumps(manifest, indent=2) + "\n")
    _write(repo, "notes/plain.json", json.dumps({"schema_version": 2}, indent=2) + "\n")
    findings = pg.scan("worktree")
    assert findings == ["notes/manifest.json: looks like a book export manifest (real data)"]


# --- hole 6: an empty denylist must not pass silently -------------------------------------------


def _run_main(monkeypatch, repo, *argv):
    monkeypatch.setattr("sys.argv", ["privacy_guard", *argv])
    return pg.main()


def test_require_denylist_fails_when_no_entries_are_loaded(repo, monkeypatch, capsys):
    assert _run_main(monkeypatch, repo, "--worktree", "--require-denylist") == 1
    assert "0 private denylist entries" in capsys.readouterr().out


def test_require_denylist_passes_with_entries(repo, monkeypatch, capsys):
    monkeypatch.setenv("PRIVACY_DENYLIST", "Quokka Holdings")
    assert _run_main(monkeypatch, repo, "--worktree", "--require-denylist") == 0
    assert "clean (1 private denylist entry)" in capsys.readouterr().out


def test_a_missing_denylist_warns_loudly_but_still_scans(repo, monkeypatch, capsys):
    assert _run_main(monkeypatch, repo, "--worktree") == 0
    captured = capsys.readouterr()
    assert "no private denylist entries loaded" in captured.err
    assert "no-such-denylist.txt" in captured.err  # names where it looked, never a term


def test_a_denylist_file_is_read_from_the_env_path(repo, monkeypatch, tmp_path):
    listing = tmp_path / "list.txt"
    listing.write_text("# synthetic\nQuokka Holdings\n", encoding="utf-8")
    monkeypatch.setenv("ONW_PRIVACY_DENYLIST", str(listing))
    assert pg._denylist() == ["Quokka Holdings"]


# --- the shipped tree stays clean under the new rules -------------------------------------------


def test_the_checkout_tree_is_clean(tmp_path, monkeypatch):
    listing = tmp_path / "list.txt"
    listing.write_text("Zzy" + "zx Ledgerworks\nĐặng Zz" + "yzx\n", encoding="utf-8")
    monkeypatch.setenv("ONW_PRIVACY_DENYLIST", str(listing))
    assert pg.scan("tree") == []


# --- the agent hook ------------------------------------------------------------------------------


@pytest.fixture
def hook():
    spec = importlib.util.spec_from_file_location("agent_privacy_hook", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git add a && git commit -m x", [("--worktree",)]),
        ("git commit -am 'x'", [("--worktree",)]),
        ("git push origin main", [("--range", "origin/main..HEAD")]),
        ("gh pr create --fill", [("--range", "origin/main..HEAD")]),
        (
            "git commit -m x && git push",
            [("--worktree",), ("--range", "origin/main..HEAD")],
        ),
        ("git status && ls", []),
    ],
)
def test_the_hook_picks_worktree_for_commits_and_range_for_pushes(
    hook, monkeypatch, command, expected
):
    monkeypatch.setattr(hook, "_upstream_base", lambda: "origin/main")
    assert hook.guard_runs(command) == expected


def test_the_hook_scans_all_history_when_there_is_no_upstream(hook, monkeypatch):
    monkeypatch.setattr(hook, "_upstream_base", lambda: None)
    assert hook.guard_runs("git push") == [("--range", "HEAD")]


def test_the_hook_blocks_with_exit_2_on_a_finding_and_runs_each_check(hook, monkeypatch, capsys):
    calls = []

    def fake_guard(*args):
        calls.append(args)
        code = 1 if args == ("--worktree",) else 0
        return subprocess.CompletedProcess(args, code, stdout="  leak.pdf: refused\n", stderr="")

    monkeypatch.setattr(hook, "_guard", fake_guard)
    monkeypatch.setattr(hook, "_upstream_base", lambda: "origin/main")
    payload = json.dumps({"tool_input": {"command": "git add . && git commit -m x && git push"}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert hook.main() == 2
    assert calls == [("--worktree",), ("--range", "origin/main..HEAD")]
    assert "leak.pdf" in capsys.readouterr().err
