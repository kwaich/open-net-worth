"""Domain tags on write tools — registry mechanics + the classification sweep.

The sweep is the load-bearing part. Every registered production tool must be EXPLICITLY
classified as one of three things; there is no "unclassified" state a new tool can ship in:

  1. ``mutates=(...)`` on its ``@tool`` decorator — it writes those data domains.
  2. ``READ_ONLY_TOOLS`` — reviewed, writes nothing persistent.
  3. ``NO_DOMAIN_WRITE_TOOLS`` — it has a real side effect, but no LOCAL data domain a
     frontend view could refetch (outbound-only, sandboxed, or turn-ephemeral).

This replaced a name-prefix sweep that only policed tools whose NAME started with one of
19 write-ish verbs. That let a genuinely-writing tool ship untagged as long as it was
called ``get_``/``list_``/``propose_`` — which is exactly how ``get_watchlist``,
``get_live_prices``, ``diff_statement``, ``propose_lot_dispose`` and friends went years
without a tag. Classification is now by REVIEW, not by naming luck.

Tagging a ``propose_*`` tool was always a smell rather than a resolution: a preview that
writes leaves debris behind when the user says no. **2026-07-19 (harness reliability
program): the 13 ``propose_*``/``apply_*`` pairs that used to split "pure preview" from
"the write" are MERGED** into one confirm-token'd tool each (mirroring
``add_transactions``/``update_transactions`` — ``docs/adr/
2026-07-19-ledger-confirm-gate-one-tool-stored-args.md``): the surviving ``apply_*`` name
carries the real ``mutates=(...)`` tag (a card-capable/attended call previews and stages
without writing; an unattended/direct call writes immediately — see
``registry.should_preview_write``), and the former ``propose_*`` name is now an ALIAS of
it, so it resolves to the SAME tag — there is no longer a separately-classified
non-writing preview tool for these 13 operations, and ``READ_ONLY_TOOLS`` no longer lists
them (an alias is not a live registered name — see ``_production_tool_names``).

Out of scope for ``mutates=`` (deliberately, so the tag stays a data-refresh signal):
telemetry and conversation bookkeeping — ``cost_log.json`` (written by EVERY tool that
touches the LLM), the tool-discovery log, chat session meta. None of them back a view.
"""

import re

from backend.ai.tools._write_classification import NO_DOMAIN_WRITE_TOOLS, READ_ONLY_TOOLS
from backend.ai.tools.registry import (
    iter_registered_tools,
    mutated_domains,
    mutating_tool_domains,
)


def test_mutated_domains_for_tagged_tool():
    assert mutated_domains("add_transactions") == ("ledger",)


def test_mutated_domains_unknown_or_read_tool_is_empty():
    assert mutated_domains("get_portfolio") == ()
    assert mutated_domains("no_such_tool") == ()


def test_mutating_tool_domains_map_shape():
    m = mutating_tool_domains()
    # Canonical names only — aliases are resolved by mutated_domains(), never listed here.
    assert m["add_transactions"] == ("ledger",)
    assert "get_portfolio" not in m
    for name, doms in m.items():
        assert isinstance(doms, tuple) and doms, name
        for d in doms:
            assert re.match(r"^([a-z_]{1,24}|\*)$", d), (name, d)


def _production_tool_names() -> list[str]:
    # Scope to PRODUCTION tool modules: the registry is a process-global that other suites
    # may (legitimately) register runtime/test tools into, which made an unscoped sweep
    # order-dependent. Production coverage is what this polices.
    return [
        name
        for name, _desc, module in iter_registered_tools()
        if module.startswith("backend.ai.tools")
    ]


def test_every_tool_is_explicitly_classified():
    """No tool ships unclassified — regardless of what it is NAMED."""
    tagged = mutating_tool_domains()
    unclassified = sorted(
        name
        for name in _production_tool_names()
        if name not in tagged and name not in READ_ONLY_TOOLS and name not in NO_DOMAIN_WRITE_TOOLS
    )
    assert unclassified == [], (
        "these tools declare neither mutates=(...) nor a reviewed read-only marker: "
        f"{unclassified}. Trace the handler through its services/db calls, then either add "
        "mutates=(domains) or add it to READ_ONLY_TOOLS / NO_DOMAIN_WRITE_TOOLS in this file."
    )


def test_classification_is_unambiguous():
    """A tool is in exactly one bucket — a tag plus a read-only marker is a contradiction."""
    tagged = set(mutating_tool_domains())
    assert not (tagged & READ_ONLY_TOOLS), sorted(tagged & READ_ONLY_TOOLS)
    assert not (tagged & set(NO_DOMAIN_WRITE_TOOLS)), sorted(tagged & set(NO_DOMAIN_WRITE_TOOLS))
    assert not (READ_ONLY_TOOLS & set(NO_DOMAIN_WRITE_TOOLS))


def test_exempt_sets_have_no_stale_entries():
    """A removed/renamed tool must not linger as a silent exemption."""
    live = set(_production_tool_names())
    assert not (READ_ONLY_TOOLS - live), sorted(READ_ONLY_TOOLS - live)
    assert not (set(NO_DOMAIN_WRITE_TOOLS) - live), sorted(set(NO_DOMAIN_WRITE_TOOLS) - live)


def test_no_domain_write_exemptions_carry_a_reason():
    for name, reason in NO_DOMAIN_WRITE_TOOLS.items():
        assert reason.strip(), name


# Regression pins for the read-SHAPED tools that genuinely write. These are the exact
# cases the old prefix sweep could never catch — if someone "cleans up" one of these tags
# because the name looks like a read, this fails.
def test_get_live_prices_is_a_read_and_records_no_history():
    """It used to append to price_history on every call — see READ_ONLY_TOOLS above."""
    import inspect

    from backend.services.prices import fetch_live

    assert mutated_domains("get_live_prices") == ()
    assert inspect.signature(fetch_live).parameters["record_history"].default is True
    src = inspect.getsource(__import__("backend.ai.tools.prices", fromlist=["x"]).get_live_prices)
    assert "record_history=False" in src, "the tool must opt OUT of recording"


def test_get_watchlist_is_a_read_and_a_job_owns_the_reconciliation():
    """It used to call `sync_from_holdings` on every read — see READ_ONLY_TOOLS above.

    Reconciling is now a scheduled job. Pinned here rather than left to a comment because
    the two facts have to move together: if the job is ever removed and nothing replaces it,
    the auto-managed My-holdings watchlist silently stops being reconciled for every reader
    that is not an open screen — including the model.
    """
    import inspect

    import backend.scheduler as scheduler

    assert mutated_domains("get_watchlist") == ()
    src = inspect.getsource(__import__("backend.ai.tools.watchlist", fromlist=["x"]).get_watchlist)
    # The CALL, not the word: `getsource` includes the @tool decorator, whose comment
    # explains where the reconciliation went.
    assert "store.sync_from_holdings(" not in src, "a read must not reconcile"
    assert "sync_from_holdings" in inspect.getsource(scheduler._run_watchlist_sync)
    assert "watchlist_sync" in inspect.getsource(scheduler.start), "the job must be registered"


def test_list_match_reviews_filters_zombies_without_writing():
    """The sweep's two halves are separable and only one is a mutation.

    Filtering still has to happen — a review pointing at a deleted transaction is not merely
    stale, it is something a reader could act on (merging against a row that is already
    gone). Stamping the store is what a `list_` tool has no business doing.
    """
    import inspect

    from backend.services import match_review

    assert mutated_domains("list_match_reviews") == ()
    src = inspect.getsource(
        __import__("backend.ai.tools.matching", fromlist=["x"]).list_match_reviews
    )
    assert "persist_sweep=False" in src, "the tool must opt OUT of stamping"

    # The filter half still runs with persist off: a zombie is dropped from the result and
    # counted, and `set_status` is never reached.
    calls: list = []
    live, count = match_review._sweep_zombies(
        [
            {"id": "mr_live", "incoming_id": "a", "canonical_id": "b"},
            {"id": "mr_zombie", "incoming_id": "gone", "canonical_id": "b"},
        ],
        {"a": {"id": "a"}, "b": {"id": "b"}},
        persist=False,
    )
    assert [i["id"] for i in live] == ["mr_live"], "the zombie must still be filtered out"
    assert count == 1
    assert calls == []


def test_read_shaped_writers_stay_tagged():
    assert mutated_domains("diff_statement") == ("uploads",)
    assert mutated_domains("verify_statement_balance") == ("uploads",)
    assert mutated_domains("email_search_messages") == ("config",)
    assert mutated_domains("email_get_messages") == ("config",)
    assert mutated_domains("mark_notifications_read") == ("notification_log",)
    assert mutated_domains("telegram_send_message") == ("notification_log",)
    assert mutated_domains("webhook_send_message") == ("notification_log",)
    assert mutated_domains("resolve_instruments") == ("portfolio",)
    assert mutated_domains("scan_intel") == ("intel", "watchlist")
    for fetch_tool in (
        "simplefin_fetch_transactions",
        "plaid_fetch_transactions",
        "lunch_money_fetch_transactions",
        "lunch_flow_fetch_transactions",
        "ynab_fetch_transactions",
        "enable_banking_fetch_transactions",
        "yodlee_fetch_transactions",
        "alpaca_fetch_positions",
        "ibkr_fetch_activity",
    ):
        assert mutated_domains(fetch_tool) == ("config",), fetch_tool


def test_the_merged_write_tools_carry_their_domain_tag():
    """The 2026-07-19 propose/apply collapse left ONE tool per write, and it must be tagged
    for what it CAN do. The former ``propose_*`` names are retired outright (ADR
    2026-08-25-tool-names-are-final), so there is nothing to compare them against any more —
    the live tool's tag is the whole answer.

    Static tags describe what a tool CAN do, not what a given call did — whether THIS
    particular call actually writes is decided dynamically by
    `registry.should_preview_write()` (card-capable/attended → preview only;
    unattended/direct → writes), which is exercised behaviorally in
    tests/test_propose_does_not_write.py, not by this static-tag sweep."""
    assert mutated_domains("update_positions") == ("portfolio",)
    assert mutated_domains("dispose_lots") == ("portfolio",)
    for retired in ("propose_position_update", "propose_lot_dispose"):
        assert mutated_domains(retired) == (), f"{retired} is retired — it must tag nothing"


# The frontend's deleted MUTATING_TOOLS set (useChat.ts) — pin that every member still
# triggers the RouterShell core refresh via domains (superset allowed, never a loss).
OLD_FRONTEND_MUTATING_TOOLS = {
    "update_positions",
    "add_positions",
    "remove_positions",
    "add_transactions",
    "add_transactions",
    "update_transactions",
    "update_transactions",
    "create_payees",
    "rename_payees",
    "merge_payees",
    "delete_payees",
    "delete_transactions",
    "delete_transactions_where",
    "create_account",
    "reconcile_accounts",
    "restore_checkpoint",
    "update_action_status",
}
ROUTERSHELL_CORE_DOMAINS = {"portfolio", "ledger", "accounts", "payees", "categories", "budget"}


def test_old_frontend_mutating_set_still_covered():
    for name in OLD_FRONTEND_MUTATING_TOOLS:
        doms = set(mutated_domains(name))
        assert doms, f"{name} lost its refresh trigger"
        assert doms & ROUTERSHELL_CORE_DOMAINS or "*" in doms, (name, doms)
