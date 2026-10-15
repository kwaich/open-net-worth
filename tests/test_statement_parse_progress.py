"""``services.statements.parse_progress`` — the live-stage registry behind statement-preview
progress (B20): a bounded, TTL-swept, thread-safe channel bound as a ContextVar so
`report()` calls deep inside the parser/guards/decisions modules reach the API's snapshot
without a token threaded through every signature. Mirrors `mcp.probe_progress`'s test shape.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.services.statements import parse_progress


@pytest.fixture(autouse=True)
def _clean_channels():
    parse_progress._reset_for_tests()
    yield
    parse_progress._reset_for_tests()


# ── the registry ──────────────────────────────────────────────────────────────


def test_stages_are_ordered_and_timestamped():
    with parse_progress.track("tok-order-0001"):
        parse_progress.report("render", page=1, pages=1)
        parse_progress.report("read", page=1, pages=2)
        parse_progress.report("read", page=2, pages=2)
        snap = parse_progress.snapshot("tok-order-0001")
    stages = snap["stages"]
    # Two entries, not three: the second "read" call is the SAME (still-open) stage as the
    # first, so it updates in place rather than appending (see the dedicated tests below).
    assert [s["stage"] for s in stages] == ["render", "read"]
    assert all(s["started_at"] for s in stages)
    assert stages[-1]["page"] == 2 and stages[-1]["pages"] == 2


def test_same_stage_report_updates_the_open_entry_in_place():
    with parse_progress.track("tok-inplace-0001"):
        parse_progress.report("read", page=1, pages=5)
        parse_progress.report("read", page=2, pages=5)
        parse_progress.report("read", page=3, pages=5, retry=1)
        snap = parse_progress.snapshot("tok-inplace-0001")
    reads = [s for s in snap["stages"] if s["stage"] == "read"]
    assert len(reads) == 1
    assert reads[0]["page"] == 3 and reads[0]["retry"] == 1
    assert reads[0]["done_at"] is None  # still the open/current stage


def test_a_different_stage_report_still_appends():
    with parse_progress.track("tok-diff-0001"):
        parse_progress.report("read", page=1, pages=1)
        parse_progress.report("reconcile")
        snap = parse_progress.snapshot("tok-diff-0001")
    assert [s["stage"] for s in snap["stages"]] == ["read", "reconcile"]
    assert snap["stages"][0]["done_at"] is not None  # closed by the stage change


def test_a_new_report_closes_the_previous_stage():
    with parse_progress.track("tok-close-0001"):
        parse_progress.report("render")
        mid = parse_progress.snapshot("tok-close-0001")
        assert mid["stages"][0]["done_at"] is None
        parse_progress.report("reconcile")
        after = parse_progress.snapshot("tok-close-0001")
    assert after["stages"][0]["done_at"] is not None
    assert after["stages"][1]["done_at"] is None  # the LAST stage stays open until done/error


def test_retry_rides_the_stage_entry():
    with parse_progress.track("tok-retry-0001"):
        parse_progress.report("read", page=3, pages=10, retry=1)
        snap = parse_progress.snapshot("tok-retry-0001")
    assert snap["stages"][0]["retry"] == 1
    assert "retry 1" in snap["stages"][0]["label"]


def test_label_table_formats_read_and_static_stages():
    with parse_progress.track("tok-labels-0001"):
        parse_progress.report("render", page=1, pages=2)
        parse_progress.report("read", page=1, pages=4)
        parse_progress.report("match")
        snap = parse_progress.snapshot("tok-labels-0001")
    labels = [s["label"] for s in snap["stages"]]
    assert labels[0] == "Rendering page 1 of 2"
    assert labels[1] == "Reading page 1 of 4"
    assert labels[2] == parse_progress.STAGE_LABELS["match"]


def test_done_and_error_shape_on_clean_finish():
    with parse_progress.track("tok-done-0001"):
        parse_progress.report("render")
    snap = parse_progress.snapshot("tok-done-0001")
    assert snap["done"] is True
    assert snap["error"] is None
    assert snap["stages"][-1]["done_at"] is not None


def test_error_is_a_message_never_a_traceback():
    with pytest.raises(ValueError):
        with parse_progress.track("tok-error-0001"):
            parse_progress.report("read", page=1, pages=1)
            raise ValueError("couldn't read the PDF: bad byte range")
    snap = parse_progress.snapshot("tok-error-0001")
    assert snap["done"] is True
    assert snap["error"] == "ValueError: couldn't read the PDF: bad byte range"
    assert "Traceback" not in snap["error"]


def test_error_message_strips_paths_and_urls():
    with pytest.raises(RuntimeError):
        with parse_progress.track("tok-error-0002"):
            raise RuntimeError(
                "fetch failed for https://provider.example/v1/x?key=secret at "
                "/Users/someone/.open-net-worth/data/files/statements/abc.pdf"
            )
    err = parse_progress.snapshot("tok-error-0002")["error"]
    assert "https://provider.example" not in err
    assert "/Users/someone" not in err
    assert err.startswith("RuntimeError: ")
    assert "<url>" in err and "<path>" in err


def test_bounded_size_evicts_the_oldest():
    for i in range(parse_progress.MAX_TOKENS * 2):
        parse_progress._open(f"tok-flood-{i:08d}")
    assert len(parse_progress._channels) <= parse_progress.MAX_TOKENS


def test_ttl_sweep_evicts_a_stale_channel(monkeypatch):
    with parse_progress.track("tok-ttl-0001"):
        pass
    assert parse_progress.snapshot("tok-ttl-0001") is not None
    monkeypatch.setattr(parse_progress, "TTL_SECONDS", -1.0)
    assert parse_progress.snapshot("tok-ttl-0001") is None
    assert "tok-ttl-0001" not in parse_progress._channels


def test_unknown_token_is_none_not_a_finished_shape():
    assert parse_progress.snapshot("never-existed-0001") is None


@pytest.mark.parametrize("token", ["", "short", "has spaces!!", "../etc/passwd", "x" * 65, None, 7])
def test_an_unusable_token_yields_no_channel(token):
    with parse_progress.track(token) as channel:
        assert channel is None
        parse_progress.report("render")  # never raises with nothing tracked


def test_report_outside_track_is_a_noop():
    parse_progress.report("render")  # must not raise, must not create a channel
    assert parse_progress._channels == {}


def test_snapshot_never_carries_row_data():
    with parse_progress.track("tok-shape-0001"):
        parse_progress.report("read", page=1, pages=1)
    snap = parse_progress.snapshot("tok-shape-0001")
    assert set(snap) == {"stages", "started_at", "elapsed_s", "done", "error", "statement_id"}
    assert set(snap["stages"][0]) == {
        "stage",
        "label",
        "started_at",
        "done_at",
        "page",
        "pages",
        "retry",
    }


# ── statement-id alias + persistence hook (B20 async mode) ────────────────────


def test_bind_statement_makes_the_channel_readable_by_either_key():
    with parse_progress.track("tok-alias-0001"):
        parse_progress.bind_statement("stmt_abc123")
        parse_progress.report("render")
    by_token = parse_progress.snapshot("tok-alias-0001")
    by_alias = parse_progress.snapshot("stmt_abc123")
    assert by_token == by_alias


def test_unbind_statement_drops_the_alias_but_leaves_the_token_channel_alone():
    """Live QA (round after 75feada85): `_async_accepted` calls this the moment it decides
    to schedule a NEW job for a statement id — so a poll in the gap before the new job's
    own `bind_statement` runs falls through to the record's `status` column instead of
    reading a channel that finished describing a DIFFERENT (previous) run."""
    with parse_progress.track("tok-unbind-0001"):
        parse_progress.bind_statement("stmt_unbind")
        parse_progress.report("render")

    assert parse_progress.snapshot("stmt_unbind") is not None
    parse_progress.unbind_statement("stmt_unbind")
    assert parse_progress.snapshot("stmt_unbind") is None
    # The token itself is untouched — only the alias is dropped.
    assert parse_progress.snapshot("tok-unbind-0001") is not None


def test_unbind_statement_with_no_alias_is_harmless():
    parse_progress.unbind_statement("stmt_never_bound")


def test_bind_statement_outside_track_is_a_noop():
    parse_progress.bind_statement("stmt_orphan")
    assert parse_progress.snapshot("stmt_orphan") is None


def test_snapshot_carries_the_bound_statement_id_once_set():
    with parse_progress.track("tok-sid-0001"):
        assert parse_progress.snapshot("tok-sid-0001")["statement_id"] is None
        parse_progress.bind_statement("stmt_sid_0001")
        assert parse_progress.snapshot("tok-sid-0001")["statement_id"] == "stmt_sid_0001"


def test_bind_statement_attaches_the_on_update_hook():
    seen: list[dict] = []
    with parse_progress.track("tok-hook-0001"):
        parse_progress.bind_statement("stmt_hook", on_update=seen.append)
        parse_progress.report("render")
        parse_progress.report("read", page=1, pages=1)
    assert len(seen) >= 3  # render, read, and the final mark_done
    assert seen[-1]["done"] is True


def test_bind_statement_hook_survives_a_failed_parse():
    seen: list[dict] = []
    with pytest.raises(RuntimeError):
        with parse_progress.track("tok-hook-0002"):
            parse_progress.bind_statement("stmt_hook_2", on_update=seen.append)
            raise RuntimeError("boom")
    assert seen[-1]["done"] is True
    assert seen[-1]["error"] == "RuntimeError: boom"


def test_status_for_maps_the_lifecycle():
    assert parse_progress.status_for({"done": False, "error": None}) == "parsing"
    assert parse_progress.status_for({"done": True, "error": None}) == "ready"
    assert parse_progress.status_for({"done": True, "error": "nope"}) == "failed"


# ── thread visibility: the parse thread must see the same channel ─────────────


def test_visible_across_parser_run_async():
    from backend.services.statements.parser import run_async

    async def _coro():
        parse_progress.report("read", page=1, pages=2)

    async def _outer():
        with parse_progress.track("tok-thread-0001"):
            run_async(_coro())
            return parse_progress.snapshot("tok-thread-0001")

    snap = asyncio.run(_outer())
    assert snap["stages"][-1]["stage"] == "read"
    assert snap["stages"][-1]["page"] == 1


def test_visible_across_asyncio_to_thread():
    async def _outer():
        with parse_progress.track("tok-thread-0002"):
            await asyncio.to_thread(parse_progress.report, "read", page=2, pages=2)
            return parse_progress.snapshot("tok-thread-0002")

    snap = asyncio.run(_outer())
    assert snap["stages"][-1]["page"] == 2


# ── real stage boundaries, unstubbed guard ─────────────────────────────────────


def test_apply_guards_reports_reconcile():
    from backend.services.statements.canonical import ParseResult, StatementMeta
    from backend.services.statements.guards import apply_guards

    with parse_progress.track("tok-guard-0001"):
        apply_guards(ParseResult(meta=StatementMeta(), rows=[], tier="text"))
        snap = parse_progress.snapshot("tok-guard-0001")
    assert snap["stages"][0]["stage"] == "reconcile"
