"""Bank/brokerage aggregation adapters.

A thin **Data-source** seam: each provider (SimpleFIN, Plaid, Enable Banking, Yodlee, …) is a
small adapter that pulls accounts + transactions and funnels every imported txn through
the existing ``ledger_service.bulk_create_transactions`` choke point (so the spend-rules
engine + categorization apply uniformly, and ``external_ref`` dedup means a re-sync is
idempotent). No bespoke per-provider core machinery — a new provider is a new module here.
"""
