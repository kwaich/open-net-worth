"""The loop-lag watchdog's idle filter.

loop_watch is a permanent diagnostic, so its false-positive path is the thing worth pinning:
a watchdog that names a blocker while the loop is merely idle trains you to ignore it, which
is strictly worse than not having one. The first version did exactly that (live 2026-08-11
17:10, see _is_idle_frame's docstring), so these are regression tests, not speculation.
"""

from backend.core import loop_watch


def test_idle_asyncio_frame_is_not_a_blocker():
    """The common case: parked in asyncio's own machinery, waiting for IO."""
    idle = "/opt/homebrew/Cellar/python@3.12/.../lib/python3.12/asyncio/base_events.py"
    assert loop_watch._is_idle_frame(idle) is True


def test_idle_selectors_frame_is_not_a_blocker():
    idle = "/opt/homebrew/Cellar/python@3.12/.../lib/python3.12/selectors.py"
    assert loop_watch._is_idle_frame(idle) is True


def test_uvicorn_bootstrap_is_not_a_blocker():
    """The exact frame the first version misreported.

    An idle stack that bottoms out in native kqueue has no asyncio or selectors frame at
    all — its innermost PYTHON frame is uvicorn's own ``server.py`` run(). The buggy filter
    let that through and announced uvicorn's bootstrap as "this is the blocker".
    """
    bootstrap = "/Users/x/code/my-net-worth/.venv/lib/python3.12/site-packages/uvicorn/server.py"
    assert loop_watch._is_idle_frame(bootstrap) is True


def test_application_code_is_a_blocker():
    """The signal the watchdog exists to produce: our own code holding the loop thread."""
    blocking = "/Users/x/code/my-net-worth/backend/ai/tools/email_ingest.py"
    assert loop_watch._is_idle_frame(blocking) is False


def test_third_party_io_library_is_a_blocker():
    """A sync socket in a vendored library blocks the loop just as hard as our own code."""
    blocking = "/Users/x/code/my-net-worth/.venv/lib/python3.12/imaplib.py"
    assert loop_watch._is_idle_frame(blocking) is False


def test_filter_is_a_pure_substring_test_over_every_marker():
    """No short-circuit ordering bug: each marker is checked, whatever position it lands in.

    The original defect was operator precedence collapsing three checks into two. Asserting
    each marker independently is what makes that class of bug impossible to reintroduce.
    """
    for marker in loop_watch._IDLE_FRAME_MARKERS:
        assert loop_watch._is_idle_frame(f"/prefix{marker}/suffix.py") is True


# ── the stall-window summary ─────────────────────────────────────────────────
#
# A stall does not have to have one culprit. The BLOCKED path only fires on 5 consecutive
# samples in the SAME frame, so it is silent on a stall assembled from many short blocks —
# which is exactly what the owner hit live on 2026-08-11 (642ms and 659ms reports with no
# BLOCKED line and every task parked at an await). These pin the three verdicts apart,
# because acting on the wrong one wastes the most time: "starved" and "blocked" have
# opposite fixes.


def _seed(samples):
    loop_watch._recent_samples.clear()
    loop_watch._recent_samples.extend(samples)


def test_no_samples_means_starved_not_blocked():
    """The loop thread was never caught running Python — offloading would be the WRONG fix."""
    _seed([])
    out = loop_watch.summarise_window(lag=0.65, now=100.0)
    assert "STARVED" in out
    assert "will NOT help" in out


def test_one_dominant_frame_is_reported_as_a_near_miss():
    _seed([(99.6 + i * 0.1, "backend/services/ledger.py:3880") for i in range(4)])
    out = loop_watch.summarise_window(lag=0.65, now=100.0)
    assert "near-miss" in out
    assert "4× backend/services/ledger.py:3880" in out


def test_many_frames_are_reported_as_many_short_blocks():
    _seed(
        [
            (99.6, "backend/services/ledger.py:100"),
            (99.7, "backend/services/labels.py:60"),
            (99.8, "backend/db/transaction_store.py:301"),
        ]
    )
    out = loop_watch.summarise_window(lag=0.65, now=100.0)
    assert "3 distinct frames" in out
    assert "many short blocks" in out


def test_samples_outside_the_stall_window_are_ignored():
    """A frame from BEFORE the stall must not be blamed for it."""
    _seed(
        [
            (10.0, "backend/old/unrelated.py:1"),  # long before
            (99.9, "backend/services/ledger.py:100"),  # inside the 650ms window
        ]
    )
    out = loop_watch.summarise_window(lag=0.65, now=100.0)
    assert "unrelated.py" not in out
    assert "ledger.py:100" in out
