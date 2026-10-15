"""``txn.source`` is the WRITER — RFC 2026-08-02 (one fact, one field).

The field used to answer two questions at once: WHO wrote the row (`ai`/`agent`/`manual`) and
WHAT FORMAT arrived (`csv_import`/`pdf_import`). Since the same principal demonstrably arrives
both ways, one field could not hold both — and the one it destroyed is the one nothing else
records. These tests pin the four properties that make the collapse safe.

Offline: pure vocabulary functions + an isolated tmp_path store. No LLM, no network.
"""

from __future__ import annotations

import pytest

from backend.core.txn_sources import (
    DELIBERATE_SOURCES,
    TRUSTED_WRITERS,
    WRITERS,
    added_by_label,
    canonical_writer,
    default_needs_review,
    display_source,
)

# ── 1. The set is CLOSED ───────────────────────────────────────────────────────


def test_writers_is_the_closed_seven_and_nothing_else():
    assert WRITERS == {"manual", "ai", "agent", "automation", "import", "api", "feed"}


def test_a_new_provider_needs_no_new_writer_value():
    """The whole reason the set can stay closed: a bank/broker provider is `feed`, and WHICH
    provider lives in external_refs. A user-installed integration therefore works on day one
    with no core change — it does not need a value here and must never be given one."""
    assert canonical_writer("plaid") == "feed"
    assert canonical_writer("simplefin") == "feed"
    assert canonical_writer("ibkr") == "feed"
    assert canonical_writer("lunch_money") == "feed"
    assert canonical_writer("ynab") == "feed"
    assert "plaid" not in WRITERS


# ── 2. Retired values keep PARSING (a source string is persisted data) ─────────


@pytest.mark.parametrize(
    "retired,canonical",
    [
        ("csv_import", "import"),
        ("qif_import", "import"),
        ("pdf_import", "import"),
        ("ofx_import", "import"),
        ("telegram", "manual"),
        ("slack", "manual"),
        ("discord", "manual"),
        ("whatsapp", "manual"),
        ("email", "agent"),
        ("recurring", "automation"),
        ("gocardless", "feed"),
        ("bank", "feed"),
    ],
)
def test_every_retired_value_still_resolves(retired, canonical):
    assert canonical_writer(retired) == canonical


def test_canonical_writer_is_idempotent():
    for w in WRITERS:
        assert canonical_writer(w) == w


# ── 3. Unknown fails SAFE (this is the bug class the RFC removes) ──────────────


def test_an_unknown_writer_is_reviewed_not_trusted():
    """The live bug: `pdf_import` was never registered, so `default_needs_review` returned
    False and 12 rows went straight past the queue that `csv_import` rows land in. An
    unrecognised writer must be the LEAST trusted thing in the system, not the most."""
    assert default_needs_review("acme_bank") is True


def test_an_absent_source_is_not_an_unknown_one():
    """A row with NO source is legacy/hand-built — always defaulted to `manual`, the owner's
    own. Treating absent as unknown flags every such create, which makes a later "mark for
    review" edit a no-op so the flag event never fires."""
    assert default_needs_review(None) is False
    assert default_needs_review("") is False
    assert display_source(None) == "manual"
    assert display_source("acme_bank") == "import"


def test_an_unknown_writer_never_renders_as_you():
    """Defaulting an unknown source to `manual` did not merely mislabel — it made a plugin's
    rows claim the owner hand-entered them, which is a false provenance claim."""
    assert added_by_label(display_source("acme_bank")) != "You"
    assert added_by_label(display_source("acme_bank")) == "File import"


def test_pdf_import_now_lands_in_the_review_queue():
    """The 12-row regression, stated directly."""
    assert default_needs_review("pdf_import") is True
    assert default_needs_review("csv_import") is True


def test_only_manual_and_feed_are_trusted():
    assert TRUSTED_WRITERS == {"manual", "feed"}
    assert default_needs_review("manual") is False
    assert default_needs_review("feed") is False
    for w in WRITERS - TRUSTED_WRITERS:
        assert default_needs_review(w) is True, w


# ── 4. Deliberate = a HUMAN was at the keyboard ────────────────────────────────


def test_deliberate_is_the_owner_typing_or_asking_lucius():
    assert DELIBERATE_SOURCES == {"manual", "ai"}


def test_a_chat_channel_is_still_the_owner_typing():
    """`telegram`/`slack` are CHANNELS, not writers — the owner is still the one typing, so
    two chat-logged coffees must not be flagged against each other. Testing the raw string
    against the set made the answer depend on which surface the row arrived through."""
    from backend.services.matching import _is_deliberate

    for channel in ("telegram", "slack", "discord", "whatsapp"):
        assert _is_deliberate({"source": channel}) is True, channel


def test_an_unattended_agent_is_not_deliberate_even_without_a_batch():
    """Why `batch_id is None` cannot replace this set: an agent logging one row at 3am
    also has no batch id, and nobody was there to mean it."""
    from backend.services.matching import _is_deliberate

    assert _is_deliberate({"source": "agent"}) is False
    assert _is_deliberate({"source": "feed"}) is False
