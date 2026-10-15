"""A statement's stored `file_path` may only name a file inside the statements directory.

The scar (security audit 2026-08-25, FINDING 9): `file_path` has two writers and only one was
trustworthy. `StatementStore.create` builds it itself — `files/statements/<server-id><ext>` —
so every statement the app stores is safe by construction. But `money_io._statement_row` copies
it VERBATIM out of an imported portable bundle, and the bundle validator checked the row `id`
and nothing else.

The readers then did `DATA_DIR / file_path`, which is not the containment it looks like:
`Path("/data") / "/etc/passwd"` is `/etc/passwd` — an absolute component silently discards
everything to its left — and `../` walks upward. So restoring a hand-edited bundle turned
"open this statement" into "read this arbitrary file", including `onw.db`, which holds every
connector credential in plaintext.

The tell that this was an oversight rather than a decision: the DELETE path in the same store
already refused to act outside the directory, calling an unguarded version an
"ARBITRARY-UNLINK primitive". The reads never got the same treatment.

Defence is in depth here, and each layer is asserted separately below:
  1. the bundle validator refuses a bad `file_path` at import, so it never reaches the store;
  2. `resolve_statement_file` refuses it at read time, so a row that predates the validator
     (or arrives by some future path) still cannot be read.
"""

from __future__ import annotations

from backend.core.paths import statements_files_dir
from backend.db.money_io import validate_money_file_shape
from backend.db.statement_store import resolve_statement_file

# ── Layer 2: the read-time guard ────────────────────────────────────────────────────────────


def test_the_canonical_path_the_app_writes_is_accepted() -> None:
    """The guard must not cost a single legitimate read — this is the shape `create()` mints."""
    assert resolve_statement_file("files/statements/abc123.pdf") is not None


def test_an_absolute_path_is_refused() -> None:
    """The join does not contain it: `DATA_DIR / "/etc/passwd"` IS `/etc/passwd`."""
    assert resolve_statement_file("/etc/passwd") is None


def test_a_dotdot_escape_is_refused() -> None:
    assert resolve_statement_file("../../../../etc/passwd") is None


def test_a_path_pointing_at_the_database_is_refused() -> None:
    """The payload that made this HIGH: `onw.db` holds every credential in plaintext."""
    assert resolve_statement_file("../onw.db") is None


def test_a_path_naming_another_store_reads_from_the_statements_dir_not_that_store() -> None:
    """The resolver REBUILDS from the trusted statements directory plus the row's file name
    rather than joining the stored string, so a path aimed at another store's blob directory
    can never reach it — it degrades to "that name, in my own directory" (which will simply
    not exist), never "that file, over there"."""
    resolved = resolve_statement_file("files/attachments/abc123.pdf")
    assert resolved is not None
    assert resolved.parent == statements_files_dir().resolve()
    assert resolved.name == "abc123.pdf"


# ── Layer 1: the import validator ───────────────────────────────────────────────────────────


def _errors(row: dict) -> list[str]:
    return validate_money_file_shape("statements.json", [row])


def test_a_bundle_row_with_the_stores_own_path_validates() -> None:
    assert _errors({"id": "stmt_abc123", "file_path": "files/statements/stmt_abc123.pdf"}) == []


def test_a_bundle_row_with_no_file_path_still_validates() -> None:
    """Absent/empty is normal — a statement can exist with its bytes already gone."""
    assert _errors({"id": "stmt_abc123"}) == []
    assert _errors({"id": "stmt_abc123", "file_path": ""}) == []


def test_a_bundle_row_with_an_absolute_file_path_is_rejected() -> None:
    errors = _errors(
        {"id": "stmt_abc123", "file_path": "/Users/someone/.open-net-worth/data/onw.db"}
    )
    assert errors and "unsafe statement file_path" in errors[0]


def test_a_bundle_row_with_a_dotdot_file_path_is_rejected() -> None:
    errors = _errors({"id": "stmt_abc123", "file_path": "files/statements/../../../onw.db"})
    assert errors and "unsafe statement file_path" in errors[0]


def test_the_bundles_own_relative_shape_is_accepted() -> None:
    """A portable bundle carries `statements/x.pdf` while the store writes
    `files/statements/x.pdf`. The validator is shape-based, not prefix-based, precisely so an
    honest bundle is not rejected for using its own layout — containment is the resolver's job,
    and it rebuilds from the trusted directory regardless of what prefix the row carried."""
    assert _errors({"id": "stmt_abc123", "file_path": "statements/x.pdf"}) == []


def test_the_id_check_still_runs_alongside_the_new_one() -> None:
    """Adding the file_path rule must not displace the id rule that was already there."""
    errors = _errors({"id": "../evil", "file_path": "/etc/passwd"})
    assert any("unsafe statement id" in e for e in errors)
    assert any("unsafe statement file_path" in e for e in errors)
