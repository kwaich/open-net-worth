"""Rename `signals` -> `notification_log` (table only — pure `op.rename_table`, no data
rewrite, no schema change).

## Why

`signals`/`SignalsStore` was named for a "monitoring/news signal ring", but an audit of its
actual live content (200/200 ring rows, the full cap) found it holds NONE of that: automation
run digests (daily transactions, IBKR trade recaps, mail-scan summaries), `run_ledger`
failure/recovery streak alerts, `ai/_confirm_gate.py`'s unattended-write review rows, and a
handful of reminders (bill due, equity vest, DCA nudge) — operational/system activity, not
market or news signal data.

That name collided in two concrete, confusing ways with the UNRELATED `intel_signals` table
(`IntelSignalsStore` — the analyst/Intel-tab pipeline's own story-arc store, checkpoint-
INCLUDED, NOT touched by this migration):

  * the `@`-mention type literally named `intel_signal` was backed by THIS store
    (`SignalsStore`), not `IntelSignalsStore` — a reader would reasonably assume the
    opposite from the name alone;
  * `SignalsStore`/`IntelSignalsStore` read as siblings/variants of the same concept
    (substring-collide even in a `grep`) when they are unrelated stores with unrelated
    owners, unrelated checkpoint semantics (this table is checkpoint-EXCLUDED/disposable;
    `intel_signals` is checkpoint-INCLUDED/durable), and unrelated writers.

`notification_log` names what the table actually is: a disposable, checkpoint-excluded ring
buffer of things the app told the user about. Follows revision 0040's rename precedent
(`op.rename_table`, no data migration needed — every row's shape is unchanged).

## What this does NOT do

Does not touch `intel_signals` / `IntelSignalsStore`. Does not touch the Inbox tab/aggregator
(`services/inbox.py`, `api/inbox.py`) — it keeps reading from this table under its new name;
tearing down the Inbox tab itself is a separate, later change. Does not change any row's
payload shape, `MAX_SIGNALS`/200-row cap behavior, or checkpoint-exclusion status — purely a
name change, applied atomically here and across every application-layer reference in the
same commit (store class, REST routes, AI tool names+aliases, the `@`-mention type,
checkpoint-exclusion list, portability wipe registration).
"""

from alembic import op

revision = "202608201228_rename_signals_to_notification_log"
down_revision = "202608172253_email_tool_permissions_owner_fix_cleanup"
branch_labels = None
depends_on = None

_OLD_TABLE = "signals"
_TABLE = "notification_log"


def upgrade() -> None:
    op.rename_table(_OLD_TABLE, _TABLE)
    # A table rename does not rename its indexes — `payload_table()` names this index
    # `ix_<table>_seq`, so the live Python definition now expects `ix_notification_log_seq`
    # while the physical index (created by revision 0015) is still `ix_signals_seq`. Left
    # unfixed, `alembic check`'s autogenerate diff correctly flags the mismatch.
    op.drop_index("ix_signals_seq", table_name=_TABLE)
    op.create_index(f"ix_{_TABLE}_seq", _TABLE, ["seq"], unique=True)


def downgrade() -> None:
    op.drop_index(f"ix_{_TABLE}_seq", table_name=_TABLE)
    op.create_index("ix_signals_seq", _TABLE, ["seq"], unique=True)
    op.rename_table(_TABLE, _OLD_TABLE)
