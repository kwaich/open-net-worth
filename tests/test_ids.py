"""Unit tests for the shared id minter (ADR 0011).

`core.ids.new_id(prefix)` mints a short, prefixed, token-cheap entity id —
`f"{prefix}_{uuid4().hex[:12]}"` (48 random bits, hex, no dashes) — with an optional
`exists` check-and-retry for belt-and-suspenders uniqueness.
"""

from __future__ import annotations

import re

import pytest

from backend.core.ids import new_id

_SUFFIX = re.compile(r"^[0-9a-f]{12}$")


def test_format_is_prefix_underscore_12_hex():
    out = new_id("txn")
    assert out.startswith("txn_")
    suffix = out[len("txn_") :]
    assert _SUFFIX.match(suffix), out
    assert len(out) == len("txn_") + 12
    assert "-" not in out  # no dashes (token-cheap)


def test_two_mints_differ():
    assert new_id("acc") != new_id("acc")


@pytest.mark.parametrize("bad", ["", " ", "Txn", "txn_", "1txn", "tx n", "TXN"])
def test_invalid_prefix_rejected(bad):
    # Prefix must be lowercase alnum starting with a letter (a stable, self-describing tag).
    with pytest.raises(ValueError):
        new_id(bad)


def test_exists_callback_forces_a_retry():
    calls = {"n": 0}

    def exists(candidate: str) -> bool:
        calls["n"] += 1
        return calls["n"] == 1  # collide once, then accept

    out = new_id("lot", exists=exists)
    assert calls["n"] == 2  # retried past the simulated collision
    assert out.startswith("lot_")


def test_exists_callback_accepts_first_when_unique():
    out = new_id("pos", exists=lambda _c: False)
    assert out.startswith("pos_")


def test_exhaustion_raises_runtime_error():
    # A degenerate `exists` that rejects every candidate must raise after the retry bound —
    # never loop forever. (Real 48-bit collisions never get here; this guards a buggy callback.)
    with pytest.raises(RuntimeError):
        new_id("txn", exists=lambda _c: True)
