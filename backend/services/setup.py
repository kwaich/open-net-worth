"""setup_service — app bootstrap helpers.

Extracted so ai/prompts can check setup state without importing api/setup. This is the ONE
implementation of the predicate: ``api/setup.py`` delegates here rather than keeping a second
copy (they had already drifted apart — one read positions via ``api.portfolio.read_rows``, the
other via ``positions_service.hydrated()``).
"""

from __future__ import annotations


def is_fresh_setup() -> bool:
    """True for an unconfigured empty instance or a restored book awaiting machine setup.

    The stamp (``OnboardingStateStore``) is authoritative. The data check is the compatibility
    path for instances predating it: no stamp, but plainly past setup, and first run must not
    re-open on them.

    The old ``profile.name`` sniff is GONE — name/nickname are collected by Lucius AFTER first
    run completes (design spec §5.2), so a fully set-up instance may legitimately have no name.
    Keying freshness off a field first run never wrote is exactly what forced the frontend's
    per-browser localStorage backstop, and why first run reappeared on a second device.
    """
    try:
        from backend.db.onboarding_state_store import OnboardingStateStore

        state = OnboardingStateStore()
        if state.completed_at():
            return False
        return state.restore_pending() or is_empty_book()
    except Exception:
        return False


def is_empty_book() -> bool:
    """True when the instance holds no accounts and no positions.

    Deliberately SEPARATE from ``is_fresh_setup`` — they answer different questions and
    collapsing them is a live bug:

      * ``is_fresh_setup`` — "should first run appear?" Answered by the completion stamp.
      * ``is_empty_book``  — "is there anything in here yet?" Answered by the data alone.

    Lucius's onboarding block keys off THIS one. First run stamps completion and *then* hands
    off to Lucius, so gating his instructions on ``is_fresh_setup`` would silence them at
    exactly the moment they are needed. Keyed on data, the block is present while the book is
    empty and disappears on its own once the user has an account or a holding — which is also
    what stops it riding every turn forever.
    """
    try:
        from backend.db.account_store import AccountStore
        from backend.services import positions as positions_service

        accounts = [a for a in AccountStore().all() if not a.get("archived")]
        return len(accounts) == 0 and len(positions_service.hydrated()) == 0
    except Exception:
        return False
