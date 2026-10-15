"""Make this process hard to read from a shell running as the same user.

The in-browser terminal runs as the app's own user, so by default it could read the app's memory
and environment through ``/proc/<pid>/environ``, ``/proc/<pid>/mem`` or ptrace — including the
gateway token. Marking the process non-dumpable makes the kernel root-own those files, and the
secrets the settings have already absorbed are dropped from the live environment. (The kernel's
original ``environ`` block is not rewritten by ``unsetenv``; non-dumpable is what protects it.)

Linux only; elsewhere both calls are no-ops. Stdlib only — ``core`` is a leaf.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from collections.abc import Iterable

logger = logging.getLogger(__name__)

_PR_SET_DUMPABLE = 4


def restrict_process_inspection() -> bool:
    """``prctl(PR_SET_DUMPABLE, 0)``. Returns whether it took effect."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            logger.warning("prctl(PR_SET_DUMPABLE) failed: errno %d", ctypes.get_errno())
            return False
    except (OSError, AttributeError):
        logger.warning("could not restrict process inspection", exc_info=True)
        return False
    return True


def forget_env(names: Iterable[str]) -> None:
    """Remove secrets from the live environment once the settings have read them."""
    for name in names:
        os.environ.pop(name, None)
