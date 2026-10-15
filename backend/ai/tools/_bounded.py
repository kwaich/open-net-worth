"""Shared row-count bounding for AI read tools.

Large tool results are the single biggest slice of context spend: transcript bytes
measured 48% tool_results vs 7% assistant prose. The fix is to bound the result AT
THE TOOL (Claude Code's model: ``Read`` has offset/limit, ``Grep`` has head_limit)
rather than to compact it afterwards.

THE INVARIANT THIS MODULE EXISTS TO ENFORCE: a bounded read must never look like a
complete one. A model that silently receives 25 of 312 rows will confidently report a
wrong total, which is worse than returning nothing. So every cap here emits BOTH the
true total and a concrete next step, and callers attach them to the payload.

WHAT THIS MODULE DELIBERATELY DOES NOT DO: field projection. Capping the ROW COUNT
while reporting the true total is safe. Dropping FIELDS is not — ``_project_txn``
(ai/tools/ledger.py) and ``_project_position`` (ai/tools/portfolio.py) both carry the
2026-07-09 scar where a curated allowlist silently hid ticker/quantity/taxable/source
from every agent reading the ledger, and a model-chosen field list on a money-bearing
read is worse still: it can select ``amount`` over ``base_amount`` and quietly defeat
the historical-rate lock. Server-chosen aggregate modes (``summary_only``) are fine
because the server picks the fields; caller-chosen ones are not.
"""

from __future__ import annotations

from typing import Any

# Defaults, tuned from the live forensics over 289 sessions rather than guessed.
# get_transactions at limit=100 measured ~15,175 tokens; 25 rows is enough to
# characterise a period while keeping a single read near ~4K tokens, and the
# `summary` block already answers the money question over the FULL filtered set.
DEFAULT_TXN_LIMIT = 25
DEFAULT_LOT_LIMIT = 50
DEFAULT_FINDING_LIMIT = 20
DEFAULT_SIGNAL_LIMIT = 20
DEFAULT_COMMITMENT_LIMIT = 20
DEFAULT_ASSET_LIMIT = 25
DEFAULT_ASSET_MARK_LIMIT = 50


def cap(rows: list[Any], limit: int | None) -> tuple[list[Any], int]:
    """Return ``(page, total)``. ``limit`` None/<=0 means no cap.

    Total is the PRE-cap length, so the caller can always report what was withheld.
    """
    total = len(rows)
    if limit is None or limit <= 0 or total <= limit:
        return rows, total
    return rows[:limit], total


def elide_body(row: dict, body_field: str, refetch_hint: str) -> dict:
    """Drop one large free-text body field from a roster row, leaving a re-fetch pointer.

    Agent and skill roster reads used to spread each whole stored record, so every roster
    read shipped every large instruction body. A roster answers what exists and whether it
    is enabled; the body is only needed once you are editing or auditing one item, which is
    a separate, targeted read.

    Agent tool-scope fields stay in full because ``list_agents`` uses them when explaining
    whether an agent is safe to enable. Skills have no runtime tool scope.
    """
    body = row.get(body_field)
    if not isinstance(body, str) or not body:
        return row
    out = dict(row)
    out.pop(body_field, None)
    out[f"{body_field}_chars"] = len(body)
    out[f"{body_field}_omitted"] = refetch_hint
    return out
