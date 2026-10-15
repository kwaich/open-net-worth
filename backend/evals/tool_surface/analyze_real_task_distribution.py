"""Discovery only: what does the owner's real Lucius traffic actually look like?

Answers the question the model-routing decision has to hinge on — "if 40% of turns
are 'log this expense' and 3% are multi-step analysis, the routing decision hinges on
the 40%" — by mining the REAL session transcripts and cost log instead of guessing.

Reads (never writes):
  - chat sessions (Wave 5E, JSON->DB: db/session_store.py's `sessions` table — was
    ~/.open-net-worth/data/sessions/*.json)
  - ~/.open-net-worth/data/cost_log.json      — every LLM call this instance has made
    NOTE: this path predates Wave 5B (JSON->DB), which moved cost_log onto the `cost_log`
    table and renamed the source file to `.migrated-<ts>` — this constant is now STALE
    (out of THIS wave's scope; flagged, not fixed, mirroring this file's own existing
    "KNOWN GAP" precedent for the routing-config migration script).

This is READ-ONLY discovery tooling: it does not touch model_config.json, does not
change which model runs for any caller, and does not write anything back to the data
directory — a plain SELECT through SessionStore is exactly as safe to run against a
live instance as opening files for reading was.

Usage:
    .venv/bin/python3 -m backend.evals.tool_surface.analyze_real_task_distribution
    .venv/bin/python3 -m backend.evals.tool_surface.analyze_real_task_distribution --out FILE.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

COST_LOG = Path.home() / ".open-net-worth" / "data" / "cost_log.json"

# Tool name -> coarse domain bucket. Deliberately coarse (not the app's own
# `mutates=` domain vocabulary) — this groups tools the way a routing DECISION would
# ("all ledger-write turns"), not the way the write-classification test does.
_DOMAIN_MAP: dict[str, str] = {}


def _register(names: list[str], domain: str) -> None:
    for name in names:
        _DOMAIN_MAP[name] = domain


_register(
    [
        "add_transaction",
        "add_transactions",
        "bulk_add_transactions",
        "update_transaction",
        "bulk_update_transactions",
        "delete_transaction",
        "delete_transactions",
        "bulk_delete_transactions",
        "set_transaction_cleared",
    ],
    "ledger_write",
)
_register(
    [
        "get_transactions",
        "get_ledger_summary",
        "get_spending_summary",
        "get_uncategorized_summary",
        "trace_transaction",
        "find_duplicates",
        "list_match_reviews",
        "resolve_match_review",
        "merge_transactions",
    ],
    "ledger_read",
)
_register(
    [
        "import_transactions",
        "list_import_batches",
        "rollback_import_batch",
        "restore_import_batch",
        "verify_statement_balance",
        "diff_statement",
    ],
    "imports",
)
_register(
    [
        "rename_payee",
        "merge_payees",
        "delete_payee",
        "get_payees",
        "create_payee",
        "set_payee_default_category",
        "set_payee_autocategorize",
        "set_payee_hidden",
        "add_payee_alias",
        "remove_payee_alias",
        "add_payee_rule",
        "remove_payee_rule",
        "set_payee_rules",
        "apply_payee_rules",
        "apply_payee_rules_to_payee",
        "apply_payee_category",
    ],
    "payees",
)
_register(
    ["list_accounts", "add_account", "update_account", "delete_account", "reconcile_account"],
    "accounts",
)
_register(
    [
        "get_portfolio",
        "get_live_prices",
        "get_lots",
        "propose_position_add",
        "propose_position_update",
        "propose_position_remove",
        "add_positions",
        "update_positions",
        "remove_positions",
        "resolve_instrument",
        "get_realized_pnl",
        "propose_lot_acquire",
        "acquire_lots",
        "propose_lot_dispose",
        "dispose_lots",
        "propose_lot_dispose_undo",
        "undo_lot_disposals",
        "update_instrument",
    ],
    "investments",
)
_register(
    [
        "get_budget_month",
        "assign_budget",
        "move_money",
        "set_envelope_target",
        "apply_budget_change",
        "list_envelopes",
        "list_archived_envelopes",
        "create_envelope",
        "update_envelopes",
        "merge_envelopes",
        "link_categories",
        "unlink_categories",
        "delete_envelope",
        "restore_envelope",
    ],
    "budget",
)
_register(
    [
        "list_categories",
        "add_category",
        "rename_category",
        "remove_category",
        "set_category_hidden",
    ],
    "categories",
)
_register(
    [
        "get_watchlist",
        "list_watchlists",
        "create_watchlist",
        "update_watchlist",
        "delete_watchlist",
        "add_watchlist_item",
        "remove_watchlist_item",
        "update_watchlist_item",
        "set_price_alert",
        "scan_intel",
        "get_signals",
        "get_rebalance_plan",
        "get_returns",
        "get_net_worth_history",
        "simulate_retirement",
        "get_report",
        "get_news_latency",
        "get_news_sources",
        "set_news_source_enabled",
        "add_news_source",
        "remove_news_source",
    ],
    "intel",
)
_register(
    [
        "list_agents",
        "run_agent_now",
        "enable_agent",
        "disable_agent",
        "get_agent_findings",
        "get_agent_metrics",
        "get_agent_runs",
        "create_agent",
        "update_agent",
        "delete_agent",
        "apply_agent_change",
        "get_self_improvements",
        "review_self_improvement",
    ],
    "agents",
)
_register(
    [
        "schedule_task",
        "list_scheduled_tasks",
        "update_scheduled_task",
        "cancel_scheduled_task",
        "run_scheduled_task_now",
        "telegram_send_message",
        "webhook_send_message",
    ],
    "scheduling",
)
_register(
    [
        "get_commitments",
        "update_commitment_installment",
        "create_commitment",
        "update_commitment",
        "delete_commitment",
    ],
    "commitments",
)
_register(["get_net_worth", "get_profile", "update_profile"], "profile_networth")
_register(
    [
        "list_grants",
        "get_equity_report",
        "get_upcoming_vests",
        "propose_vest",
        "record_vest",
        "propose_vest_undo",
        "undo_vest",
        "create_grant",
        "update_grant",
        "cancel_grant",
    ],
    "equity",
)
_register(
    [
        "get_options",
        "get_option_contract",
        "propose_open_option",
        "propose_close_option",
        "open_option",
        "close_option",
        "update_option",
        "cancel_option",
    ],
    "options",
)
_register(["web_search", "fetch_page", "calculate"], "research")
_register(["email_search_messages", "email_get_messages", "email_send_message"], "email")
_register(["create_artifact"], "artifacts")
_register(["remember_fact", "forget_fact"], "memory")
_register(
    [
        "list_rules",
        "test_rule",
        "create_rule",
        "update_rule",
        "delete_rule",
        "apply_rules_to_history",
        "get_rule_suggestions",
        "get_category_suggestions",
    ],
    "rules",
)
_register(["find_tools"], "discovery")
_register(["get_model_config", "update_model_config", "set_provider_credentials"], "config")
_register(
    [
        "list_skills",
        "activate_skill",
        "create_skill",
        "update_skill",
        "delete_skill",
        "run_script",
        "run_code",
        "apply_run_code",
    ],
    "skills",
)
_register(["get_cost_summary", "get_recent_logs"], "diagnostics")


def _domain_of(tool: str) -> str:
    if tool in _DOMAIN_MAP:
        return _DOMAIN_MAP[tool]
    if tool.startswith(("get_", "list_")):
        return "other_read"
    return f"other:{tool}"


def _session_transcripts() -> list[tuple[str, list]]:
    """Every session's (id, transcript) pair. Wave 5E (JSON->DB): was a glob over
    SESSIONS_DIR/*.json (skipping ai/memory.py's own `.extraction_cursor.json`
    sidecars, which never lived in the `sessions` table to begin with — nothing to
    skip here any more); chat sessions are DB-backed now (db/session_store.py), so
    this reads through SessionStore instead of opening files. Still read-only, still
    safe to run against a live instance."""
    from backend.db.session_store import SessionStore

    store = SessionStore()
    pairs: list[tuple[str, list]] = []
    for meta in store.all_meta():
        sid = meta.get("id")
        if not sid:
            continue
        transcript = store.get_transcript(sid)
        if isinstance(transcript, list):
            pairs.append((sid, transcript))
    return pairs


def extract_turns() -> list[tuple[str, str, list[str]]]:
    """One (session_id, user_text, tool_names_called_in_response) per real user turn.

    A "turn" is a user message with STRING content (something the user actually typed —
    a tool_result is a list-shaped content block, so it is excluded) plus every tool the
    assistant called before the user's NEXT string turn.
    """
    turns: list[tuple[str, str, list[str]]] = []
    for sid, data in _session_transcripts():
        current_text: str | None = None
        current_tools: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role == "user" and isinstance(content, str):
                if current_text is not None:
                    turns.append((sid, current_text, current_tools))
                current_text = content
                current_tools = []
            elif role == "assistant" and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        current_tools.append(block.get("name", ""))
        if current_text is not None:
            turns.append((sid, current_text, current_tools))
    return turns


def classify_turn(tools: list[str]) -> str:
    if not tools:
        return "no_tool_chitchat"
    counts = Counter(_domain_of(t) for t in tools)
    return counts.most_common(1)[0][0]


def turn_distribution() -> dict:
    turns = extract_turns()
    counter = Counter(classify_turn(tools) for _, _, tools in turns)
    total = sum(counter.values())
    examples: dict[str, list[str]] = defaultdict(list)
    for _, text, tools in turns:
        cat = classify_turn(tools)
        if len(examples[cat]) < 3:
            examples[cat].append(text[:140].replace("\n", " "))
    return {
        "total_turns": total,
        "sessions_scanned": len(_session_transcripts()),
        "by_category": {
            cat: {"count": n, "pct": round(n / total * 100, 1), "examples": examples[cat]}
            for cat, n in counter.most_common()
        },
    }


def cost_log_summary() -> dict:
    try:
        with open(COST_LOG, encoding="utf-8") as fh:
            rows = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"could not read cost_log.json: {exc}"}

    chat_rows = [r for r in rows if r.get("source") == "chat"]
    by_model: dict[str, dict] = {}
    for r in chat_rows:
        m = r.get("model") or "?"
        entry = by_model.setdefault(
            m, {"n": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}
        )
        entry["n"] += 1
        entry["cost_usd"] += r.get("cost_usd") or 0
        entry["input_tokens"] += r.get("input_tokens") or 0
        entry["output_tokens"] += r.get("output_tokens") or 0
    for m, entry in by_model.items():
        entry["avg_cost_per_call"] = round(entry["cost_usd"] / entry["n"], 5) if entry["n"] else 0.0
        entry["cost_usd"] = round(entry["cost_usd"], 4)

    return {
        "total_rows": len(rows),
        "chat_rows": len(chat_rows),
        "total_cost_usd_all_sources": round(sum(r.get("cost_usd") or 0 for r in rows), 4),
        "total_cost_usd_chat_only": round(sum(r.get("cost_usd") or 0 for r in chat_rows), 4),
        "chat_by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1]["cost_usd"])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    result = {
        "turn_distribution": turn_distribution(),
        "cost_log_summary": cost_log_summary(),
    }
    text = json.dumps(result, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
