"""Monkeypatchable source-file seams for every JSON-absorbing revision.

Each cutover revision (0002 evidence, 0004 price_history, 0005 instruments, 0007 money core,
...) reads its source path from here rather than importing ``backend.core.paths`` directly, so
a test can point a single revision at a throwaway fixture file
(``monkeypatch.setattr(..., "SOURCE", tmp_path/...)``) without redirecting every path in the
app. Names are per-store and additive — never rename one (a test/monkeypatch site is bound to
the exact name). This module is now the ONE sources seam for every cutover revision in the
repo: a new absorb adds a constant here, it never reaches into ``backend.core.paths`` directly
from inside a revision or ``*_io.py`` module.
"""

from backend.core.paths import ACCOUNTS_JSON as ACCOUNTS_SOURCE  # noqa: F401
from backend.core.paths import ACTIONS_JSON as ACTIONS_SOURCE  # noqa: F401
from backend.core.paths import AGENT_FINDINGS_JSON as AGENT_FINDINGS_SOURCE  # noqa: F401
from backend.core.paths import ALERTS_JSON as ALERTS_SOURCE  # noqa: F401
from backend.core.paths import ALPACA_CONFIG_JSON as ALPACA_CONFIG_SOURCE  # noqa: F401
from backend.core.paths import APP_CONFIG_JSON as APP_CONFIG_SOURCE  # noqa: F401
from backend.core.paths import ARTIFACTS_JSON as ARTIFACTS_SOURCE  # noqa: F401
from backend.core.paths import ASSET_BRIEF_JSON as ASSET_BRIEF_SOURCE  # noqa: F401

# UPLOADS_JSON was renamed ATTACHMENTS_JSON at revision 0041 (cross-channel-attachments
# RFC Phase 2) — the SEAM NAME (UPLOADS_SOURCE) stays put per this module's own "never
# rename one" rule (revision 0018 monkeypatches it); only the backend.core.paths target
# it aliases changes, which is exactly what this indirection exists to absorb.
from backend.core.paths import ATTACHMENTS_JSON as UPLOADS_SOURCE  # noqa: F401
from backend.core.paths import AUTH_JSON as AUTH_SOURCE  # noqa: F401
from backend.core.paths import AUTOMATION_RUNS_JSON as AUTOMATION_RUNS_SOURCE  # noqa: F401
from backend.core.paths import AUTOMATION_RUNTIME_JSON as AUTOMATION_RUNTIME_SOURCE  # noqa: F401
from backend.core.paths import AUTOMATIONS_JSON as AUTOMATIONS_SOURCE  # noqa: F401
from backend.core.paths import BATCH_LEDGER_JSON as BATCH_LEDGER_SOURCE  # noqa: F401
from backend.core.paths import BUDGET_ASSIGNMENTS_JSON as BUDGET_ASSIGNMENTS_SOURCE  # noqa: F401
from backend.core.paths import CATEGORIES_JSON as CATEGORIES_SOURCE  # noqa: F401
from backend.core.paths import CATEGORY_GROUPS_JSON as CATEGORY_GROUPS_SOURCE  # noqa: F401
from backend.core.paths import CHANNELS_CONFIG_JSON as CHANNELS_CONFIG_SOURCE  # noqa: F401
from backend.core.paths import CHAT_PAIRING_JSON as CHAT_PAIRING_SOURCE  # noqa: F401
from backend.core.paths import CHECKPOINTS_INDEX as CHECKPOINTS_INDEX_SOURCE  # noqa: F401
from backend.core.paths import COMMITMENTS_JSON as COMMITMENTS_SOURCE  # noqa: F401
from backend.core.paths import CONNECTOR_SESSIONS_JSON as CONNECTOR_SESSIONS_SOURCE  # noqa: F401
from backend.core.paths import CORRECTIONS_JSON as CORRECTIONS_SOURCE  # noqa: F401
from backend.core.paths import COST_LOG_DAILY_JSON as COST_LOG_DAILY_SOURCE  # noqa: F401
from backend.core.paths import COST_LOG_JSON as COST_LOG_SOURCE  # noqa: F401
from backend.core.paths import DASHBOARD_LAYOUT_JSON as DASHBOARD_LAYOUT_SOURCE  # noqa: F401
from backend.core.paths import DEDUP_CONFIG_JSON as DEDUP_CONFIG_SOURCE  # noqa: F401
from backend.core.paths import DIGEST_STATE_JSON as DIGEST_STATE_SOURCE  # noqa: F401
from backend.core.paths import DISCORD_CONFIG_JSON as DISCORD_CONFIG_SOURCE  # noqa: F401
from backend.core.paths import EMAIL_INGEST_JSON as EMAIL_INGEST_SOURCE  # noqa: F401
from backend.core.paths import EMAIL_SNAPSHOTS_JSON as EMAIL_SNAPSHOTS_SOURCE  # noqa: F401
from backend.core.paths import (
    ENABLE_BANKING_CONFIG_JSON as ENABLE_BANKING_CONFIG_SOURCE,  # noqa: F401
)
from backend.core.paths import ENVELOPES_JSON as ENVELOPES_SOURCE  # noqa: F401
from backend.core.paths import EVIDENCE_JSON as SOURCE  # noqa: F401
from backend.core.paths import GOCARDLESS_CONFIG_JSON as GOCARDLESS_CONFIG_SOURCE  # noqa: F401
from backend.core.paths import GRANTS_JSON as GRANTS_SOURCE  # noqa: F401
from backend.core.paths import HARNESS_STATE_JSON as HARNESS_STATE_SOURCE  # noqa: F401
from backend.core.paths import IBKR_CONFIG_JSON as IBKR_CONFIG_SOURCE  # noqa: F401
from backend.core.paths import IMPORT_BATCHES_JSON as IMPORT_BATCHES_SOURCE  # noqa: F401
from backend.core.paths import INCOME_EVENTS_JSON as INCOME_EVENTS_SOURCE  # noqa: F401
from backend.core.paths import INSTRUMENTS_JSON as INSTRUMENTS_SOURCE  # noqa: F401
from backend.core.paths import INTEL_SIGNALS_JSON as INTEL_SIGNALS_SOURCE  # noqa: F401
from backend.core.paths import LOTS_JSON as LOTS_SOURCE  # noqa: F401
from backend.core.paths import MATCH_DECISIONS_JSON as MATCH_DECISIONS_SOURCE  # noqa: F401
from backend.core.paths import MATCH_REVIEWS_JSON as MATCH_REVIEWS_SOURCE  # noqa: F401
from backend.core.paths import MCP_CLIENTS_JSON as MCP_CLIENTS_SOURCE  # noqa: F401
from backend.core.paths import MCP_PENDING_CALLS_JSON as MCP_PENDING_CALLS_SOURCE  # noqa: F401
from backend.core.paths import MCP_WRITE_LOG_JSON as MCP_WRITE_LOG_SOURCE  # noqa: F401
from backend.core.paths import (
    MEMORY_CONSOLIDATE_STATE_JSON as MEMORY_CONSOLIDATE_STATE_SOURCE,  # noqa: F401
)
from backend.core.paths import MEMORY_JSON as MEMORY_SOURCE  # noqa: F401
from backend.core.paths import MEMORY_SETTINGS_JSON as MEMORY_SETTINGS_SOURCE  # noqa: F401
from backend.core.paths import MERGE_JOURNAL_JSON as MERGE_JOURNAL_SOURCE  # noqa: F401
from backend.core.paths import MODEL_CONFIG_JSON as MODEL_CONFIG_SOURCE  # noqa: F401
from backend.core.paths import MODEL_TELEMETRY_JSON as MODEL_TELEMETRY_SOURCE  # noqa: F401
from backend.core.paths import NEWS_SCREEN_STATE_JSON as NEWS_SCREEN_STATE_SOURCE  # noqa: F401
from backend.core.paths import NEWS_SOURCES_JSON as NEWS_SOURCES_SOURCE  # noqa: F401
from backend.core.paths import OPTION_CONTRACTS_JSON as OPTION_CONTRACTS_SOURCE  # noqa: F401
from backend.core.paths import PAYEES_JSON as PAYEES_SOURCE  # noqa: F401
from backend.core.paths import PENDING_ANALYSIS_JSON as PENDING_ANALYSIS_SOURCE  # noqa: F401
from backend.core.paths import PENDING_PLANS_JSON as PENDING_PLANS_SOURCE  # noqa: F401
from backend.core.paths import PENDING_PUSHES_JSON as PENDING_PUSHES_SOURCE  # noqa: F401
from backend.core.paths import PLAID_CONFIG_JSON as PLAID_CONFIG_SOURCE  # noqa: F401
from backend.core.paths import PLAN_STATE_JSON as PLAN_STATE_SOURCE  # noqa: F401
from backend.core.paths import PLUGGY_CONFIG_JSON as PLUGGY_CONFIG_SOURCE  # noqa: F401
from backend.core.paths import POSITIONS_JSON as POSITIONS_SOURCE  # noqa: F401
from backend.core.paths import PREFERRED_MODELS_JSON as PREFERRED_MODELS_SOURCE  # noqa: F401
from backend.core.paths import PRICE_HISTORY_JSON as PRICE_HISTORY_SOURCE  # noqa: F401
from backend.core.paths import PROFILE_JSON as PROFILE_SOURCE  # noqa: F401
from backend.core.paths import REALIZED_EVENTS_JSON as REALIZED_EVENTS_SOURCE  # noqa: F401
from backend.core.paths import RECURRING_STREAMS_JSON as RECURRING_STREAMS_SOURCE  # noqa: F401
from backend.core.paths import RUN_HISTORY_JSON as RUN_HISTORY_SOURCE  # noqa: F401
from backend.core.paths import SEARCH_CONFIG_JSON as SEARCH_CONFIG_SOURCE  # noqa: F401
from backend.core.paths import SESSIONS_DIR as SESSIONS_SOURCE  # noqa: F401
from backend.core.paths import SIGNALS_JSON as SIGNALS_SOURCE  # noqa: F401
from backend.core.paths import SIMPLEFIN_CONFIG_JSON as SIMPLEFIN_CONFIG_SOURCE  # noqa: F401
from backend.core.paths import SKILLS_STATE_JSON as SKILLS_STATE_SOURCE  # noqa: F401
from backend.core.paths import SLACK_CONFIG_JSON as SLACK_CONFIG_SOURCE  # noqa: F401
from backend.core.paths import STATEMENT_FORMATS_JSON as STATEMENT_FORMATS_SOURCE  # noqa: F401
from backend.core.paths import STATEMENTS_JSON as STATEMENTS_SOURCE  # noqa: F401
from backend.core.paths import SUBSCRIPTIONS_JSON as SUBSCRIPTIONS_SOURCE  # noqa: F401
from backend.core.paths import TARGETS_JSON as TARGETS_SOURCE  # noqa: F401
from backend.core.paths import TELEGRAM_CONFIG_JSON as TELEGRAM_CONFIG_SOURCE  # noqa: F401
from backend.core.paths import TOOL_CALL_LOG_JSON as TOOL_CALL_LOG_SOURCE  # noqa: F401
from backend.core.paths import TOOL_DISCOVERY_LOG_JSON as TOOL_DISCOVERY_LOG_SOURCE  # noqa: F401
from backend.core.paths import TRANSACTIONS_JSON as TRANSACTIONS_SOURCE  # noqa: F401
from backend.core.paths import TXN_AUDIT_JSON as TXN_AUDIT_SOURCE  # noqa: F401
from backend.core.paths import VEST_EVENTS_JSON as VEST_EVENTS_SOURCE  # noqa: F401
from backend.core.paths import WATCHLIST_JSON as WATCHLIST_SOURCE  # noqa: F401
from backend.core.paths import (
    WHATSAPP_INBOUND_CONFIG_JSON as WHATSAPP_INBOUND_CONFIG_SOURCE,  # noqa: F401
)
from backend.core.paths import YODLEE_CONFIG_JSON as YODLEE_CONFIG_SOURCE  # noqa: F401
