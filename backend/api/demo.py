"""Demo mode — build the fabricated demo book into an empty instance.

A single thin route over ``backend.scripts.demo.build``, so the onboarding wizard's and the
Overview's "Load demo data" choice can populate an EMPTY instance over HTTP.

The safety promise is unchanged from the fixture-copy version this replaced: the builder
REFUSES a book that already holds accounts, transactions or positions, so this endpoint can
never overwrite real data. There is deliberately no ``force`` flag exposed over the network —
force stays CLI-only (``python3 -m backend.scripts.seed_demo --commit --force``).

This is an instance-lifecycle op (like ``/api/reset``), not a user mutation. It writes through
the ordinary service choke points, which means it IS checkpoint-visible, but undoing a demo
load is still "Clear demo & start fresh" (``POST /api/reset``), not a turn restore — a build
spans thousands of writes across every store. There is no Lucius tool for it (mirroring the
no-AI-tool reset/auth calls).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

router = APIRouter()

log = logging.getLogger(__name__)


@router.post("/api/demo/seed")
def seed_demo():
    """Build the demo book. Returns the build summary, or 409 if the book is not empty."""
    from backend.scripts.demo.build import book_is_empty, build

    if not book_is_empty():
        raise HTTPException(
            status_code=409,
            detail=(
                "This instance already has data. The demo builder never writes over a real "
                "book — reset the instance first if you meant to replace it."
            ),
        )
    log.info("building the demo book")
    return build()
