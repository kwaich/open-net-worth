"""Which models the live suite runs per CLI — at most two, chosen from the CLI's own answers.

The CLI's configured default (what a user who picked nothing gets) plus the newest model it
lists. Pure functions so ``tests/test_cli_regression.py`` can pin the choice without a CLI.
"""

from __future__ import annotations

import re

_VERSION = re.compile(r"(\d+)(?:[.-](\d+))?")


def version_key(model_id: str) -> tuple[int, int]:
    """``gpt-6.1-sol`` -> (6, 1); ``claude-opus-4-8`` -> (4, 8); no number -> (0, 0)."""
    match = _VERSION.search(model_id)
    return (int(match.group(1)), int(match.group(2) or 0)) if match else (0, 0)


def pick_models(default: str, listed: list[str]) -> list[str]:
    """``[default, newest other]``.

    When the default already IS the newest, the second pick is the next one down — a previous
    generation, so the two runs still cover different models. Ties keep the CLI's own listed
    order (a stable sort). Never more than two.
    """
    ranked = sorted(dict.fromkeys(listed), key=version_key, reverse=True)
    others = [m for m in ranked if m != default]
    return [*([default] if default else []), *others[:1]]
