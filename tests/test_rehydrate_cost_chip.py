"""Regression tests: the per-turn cost chip must survive a conversation reload.

Bug: a live assistant bubble shows a cost chip (`m.usage`), built from the SSE
`turn_done` event's aggregated token/cost numbers. `MessageBubble.tsx` only renders it
when `m.usage` is set. `assistant.rehydrate_messages` rebuilds messages from the
on-disk transcript (model content blocks only) and never emitted `usage` — so the chip
silently disappeared the moment a conversation fell back to the backend rehydrate path
(``frontend/src/utils/chatStore.ts``'s ``fetchRemoteMessages`` — used whenever nothing
is cached locally: another browser/device, or a cleared IndexedDB).

The fix joins each turn's aggregated cost back from `cost_log.json`:
  - EXACT: `assistant.run()` now stamps `turn_index` (the append-only history index the
    turn opened at) onto its cost_log row at write time. `_turn_boundaries` recomputes
    the identical index on reload, giving a zero-ambiguity join for every turn recorded
    after this shipped.
  - BEST-EFFORT: a conversation from BEFORE `turn_index` existed falls back to ordinal
    (chronological) matching, but ONLY when the count of unmatched turns equals the
    count of unmatched (turn_index-less) rows — otherwise nothing is attached, because
    a wrong number is worse than no number (the owner uses this figure to judge real
    spend).
  - A background-agent/memory/intel cost_log row sharing this session_id must never be
    attributed to a message the user sent — `session_chat_records` filters to
    `source == "chat"`.
  - The proactive-alert bridge pair (`_PROACTIVE_TRIGGER`) never went through the model
    and never produced a cost_log row, so it must never consume — or be assigned — a
    turn's cost.

No LLM, no network — pure functions over synthetic transcripts + an isolated cost_log table.
"""

from __future__ import annotations

import pytest

from backend.ai import assistant


def _row(
    *,
    session_id: str,
    ts: str,
    source: str = "chat",
    model: str = "minimax/minimax-m3",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    cost_usd: float = 0.008,
    turn_index: int | None = None,
) -> dict:
    rec = {
        "ts": ts,
        "source": source,
        "session_id": session_id,
        "model": model,
        "input_tokens": input_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cache_read_tokens": cache_read_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    }
    if turn_index is not None:
        rec["turn_index"] = turn_index
    return rec


@pytest.fixture
def cost_log(db_isolated):
    """The raw cost log is the cost_log table now (Wave 5B: JSON->DB) — db_isolated
    gives CostLogStore a fresh, isolated sqlite file; write() replaces the whole log,
    same whole-overwrite semantics the old file write had. Records are minted fresh
    ids via append() (the table's `id` is a real primary key, so a `replace_all` of
    id-less payloads would collide)."""
    from backend.db.cost_log_store import CostLogStore

    def write(records: list[dict]) -> None:
        store = CostLogStore()
        store.replace_all([])
        for rec in records:
            store.append(rec)

    return write


def _assistant_msgs(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m["role"] == "assistant"]


# ── write path: cost_tracker.record() stamps turn_index ──────────────────────────


class TestRecordStampsTurnIndex:
    def test_turn_index_present_when_given(self, db_isolated):
        from backend.ai.cost_tracker import record
        from backend.db.cost_log_store import CostLogStore

        record(
            "chat",
            "claude-x",
            100,
            50,
            session_id="sess-1",
            cost_usd=0.01,
            turn_index=7,
        )
        rows = CostLogStore().all()
        assert rows[0]["turn_index"] == 7

    def test_turn_index_absent_when_not_given(self, db_isolated):
        from backend.ai.cost_tracker import record
        from backend.db.cost_log_store import CostLogStore

        record("chat", "claude-x", 100, 50, session_id="sess-1", cost_usd=0.01)
        rows = CostLogStore().all()
        assert "turn_index" not in rows[0]


# ── read path: rehydrate re-attaches usage ────────────────────────────────────────


class TestExactTurnIndexJoin:
    def test_recovered_residue_and_current_post_share_one_cost_boundary(
        self, cost_log, monkeypatch
    ):
        history = [
            {"role": "user", "content": "recovered one"},
            {"role": "user", "content": "recovered two"},
            {"role": "user", "content": "current post"},
            {"role": "assistant", "content": [{"type": "text", "text": "one reply"}]},
        ]
        monkeypatch.setattr(assistant, "_load_session", lambda sid: history)
        cost_log(
            [
                _row(
                    session_id="residue-session",
                    ts="2026-08-09T10:00:00+00:00",
                    turn_index=0,
                    cost_usd=0.004,
                )
            ]
        )

        messages = assistant.rehydrate_messages("residue-session")

        assert [m["text"] for m in messages if m["role"] == "user"] == [
            "recovered one",
            "recovered two",
            "current post",
        ]
        replies = _assistant_msgs(messages)
        assert len(replies) == 1
        assert replies[0]["usage"]["cost_usd"] == 0.004

    def test_two_turns_each_get_their_own_row_on_the_final_bubble(self, cost_log, monkeypatch):
        # Turn 1: user@0 -> assistant@1 (plain reply, no tool call).
        # Turn 2: user@2 -> assistant@3 (tool_use) -> user@4 (tool_result) -> assistant@5 (final).
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
            {"role": "user", "content": "what's my balance"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu-1", "name": "get_net_worth", "input": {}}
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": "{}"}],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "$42."}]},
        ]
        monkeypatch.setattr(assistant, "_load_session", lambda sid: history)
        cost_log(
            [
                _row(
                    session_id="sess-1",
                    ts="2026-07-19T10:00:00+00:00",
                    turn_index=0,
                    cost_usd=0.001,
                ),
                _row(
                    session_id="sess-1",
                    ts="2026-07-19T10:01:00+00:00",
                    turn_index=2,
                    cost_usd=0.008,
                ),
            ]
        )

        messages = assistant.rehydrate_messages("sess-1")
        asst = _assistant_msgs(messages)
        # Turn 2's tool-call round and its final-answer round merge into ONE bubble —
        # this is the same grouping the live SSE path shows (chatReducer's
        # `ensureAssistant` reuses the trailing bubble across a whole tool loop).
        assert len(asst) == 2

        # Turn 1's only assistant bubble gets turn 1's usage.
        assert asst[0]["text"] == "hello"
        assert asst[0]["usage"]["cost_usd"] == 0.001

        # Turn 2's single merged bubble gets turn 2's usage, on its final round.
        assert asst[1]["text"] == "$42."
        assert asst[1]["usage"]["cost_usd"] == 0.008
        assert asst[1]["usage"]["model"] == "minimax/minimax-m3"

    def test_agent_source_row_never_attributed_to_a_chat_bubble(self, cost_log, monkeypatch):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        ]
        monkeypatch.setattr(assistant, "_load_session", lambda sid: history)
        # A background agent run happens to share this session_id and this exact
        # turn_index — must never be picked up (source != "chat").
        cost_log(
            [
                _row(
                    session_id="sess-1",
                    ts="2026-07-19T10:00:00+00:00",
                    turn_index=0,
                    source="agent:bookkeeper",
                )
            ]
        )

        messages = assistant.rehydrate_messages("sess-1")
        asst = _assistant_msgs(messages)
        assert "usage" not in asst[0]


class TestLegacyOrdinalFallback:
    def test_counts_match_exactly_attaches_in_chronological_order(self, cost_log, monkeypatch):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
            {"role": "user", "content": "again"},
            {"role": "assistant", "content": [{"type": "text", "text": "sure"}]},
        ]
        monkeypatch.setattr(assistant, "_load_session", lambda sid: history)
        # Legacy rows: no turn_index field at all (pre-migration data).
        cost_log(
            [
                _row(session_id="sess-1", ts="2026-07-19T10:00:00+00:00", cost_usd=0.001),
                _row(session_id="sess-1", ts="2026-07-19T10:01:00+00:00", cost_usd=0.002),
            ]
        )

        messages = assistant.rehydrate_messages("sess-1")
        asst = _assistant_msgs(messages)
        assert asst[0]["usage"]["cost_usd"] == 0.001
        assert asst[1]["usage"]["cost_usd"] == 0.002

    def test_count_mismatch_attaches_nothing_rather_than_guess(self, cost_log, monkeypatch):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
            {"role": "user", "content": "again"},
            {"role": "assistant", "content": [{"type": "text", "text": "sure"}]},
        ]
        monkeypatch.setattr(assistant, "_load_session", lambda sid: history)
        # Only ONE legacy row for TWO real turns (e.g. one turn errored before ever
        # reaching _record_cost) — genuinely ambiguous which turn it belongs to.
        cost_log([_row(session_id="sess-1", ts="2026-07-19T10:00:00+00:00", cost_usd=0.001)])

        messages = assistant.rehydrate_messages("sess-1")
        asst = _assistant_msgs(messages)
        assert "usage" not in asst[0]
        assert "usage" not in asst[1]

    def test_no_cost_rows_at_all_leaves_chip_hidden(self, cost_log, monkeypatch):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        ]
        monkeypatch.setattr(assistant, "_load_session", lambda sid: history)
        cost_log([])

        messages = assistant.rehydrate_messages("sess-1")
        asst = _assistant_msgs(messages)
        assert "usage" not in asst[0]


# (The proactive BRIDGE is gone — 2026-08-24. An alert is an ordinary appended
# assistant message now, so it opens no synthetic turn and there is no cost-row
# ordinal to exclude. The class that pinned that exclusion went with it.)


class TestNoSessionOrEmptyHistory:
    def test_missing_session_returns_none(self, cost_log, monkeypatch):
        monkeypatch.setattr(assistant, "_load_session", lambda sid: None)
        cost_log([])
        assert assistant.rehydrate_messages("nope") is None

    def test_rehydrate_never_raises_when_cost_log_is_corrupt(self, db_isolated, monkeypatch):
        """No more on-disk file to hand-corrupt (cost_log is a checkpoint-EXCLUDED DB
        table now) — break the read itself instead, the real remaining failure mode.
        services.cost._load_records() already wraps CostLogStore().all() in a broad
        try/except returning [] on any error; this proves rehydrate never raises even
        when that read blows up."""
        from backend.db.cost_log_store import CostLogStore

        def boom(self):
            raise RuntimeError("simulated db read failure")

        monkeypatch.setattr(CostLogStore, "all", boom)
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        ]
        monkeypatch.setattr(assistant, "_load_session", lambda sid: history)

        messages = assistant.rehydrate_messages("sess-1")
        asst = _assistant_msgs(messages)
        assert "usage" not in asst[0]
