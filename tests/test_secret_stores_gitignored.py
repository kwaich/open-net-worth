"""Regression guard: every secret-bearing config store MUST be git-ignored.

`.gitignore` is a per-file allowlist here (each ignored data file is enumerated), so a
new secret store is trivially forgotten — and then its cleartext secret (HMAC key, bot
token, API key, RSA key) gets committed by a routine `git add -A`. This test fails if any
known secret store is not ignored (the WhatsApp/Slack/Discord/Enable-Banking gap that a
security review caught). Add a new secret store's path here AND to `.gitignore` together.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Every config/data file that holds a write-only secret (HMAC app secret, bot token,
# Ed25519 key, API key/access token, RSA private key, password hash, access URL).
_SECRET_STORES = [
    "backend/data/auth.json",
    "backend/data/telegram_config.json",
    "backend/data/slack_config.json",
    "backend/data/discord_config.json",
    "backend/data/whatsapp_inbound_config.json",
    "backend/data/channels_config.json",
    "backend/data/simplefin_config.json",
    "backend/data/gocardless_config.json",
    "backend/data/plaid_config.json",
    "backend/data/enable_banking_config.json",
    "backend/data/pluggy_config.json",
    "backend/data/alpaca_config.json",
    "backend/data/ibkr_config.json",
    "backend/data/search_config.json",
]


@pytest.mark.parametrize("path", _SECRET_STORES)
def test_secret_store_is_gitignored(path: str) -> None:
    """git check-ignore must report the secret store as ignored (exit 0)."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=_REPO_ROOT,
        capture_output=True,
    )
    assert result.returncode == 0, f"{path} is NOT git-ignored — its secret could be committed"


def test_no_secret_store_is_tracked() -> None:
    """No secret store may be tracked in the index (already committed = exposed)."""
    tracked = subprocess.run(
        ["git", "ls-files"] + _SECRET_STORES,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert not tracked, f"secret store(s) tracked in git: {tracked!r}"
