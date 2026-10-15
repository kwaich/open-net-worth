"""Unified statement parsing (RFC statement-import-unified).

One pipeline for every messy statement format — tabular (CSV/XLSX) via one-call LLM
schema-inference, text PDFs via chunked extraction, scanned/photo PDFs via vision — emitting one
canonical row shape plus statement-level metadata, hardened by deterministic correctness guards
(balance re-derivation, per-currency reconciliation, locale inference, span-verification).

Layer: services (→ db → core). LLM access ONLY through ``core.llm_seam`` (Contract 1).
"""

from backend.services.statements.canonical import CanonicalRow, ParseResult, StatementMeta
from backend.services.statements.parser import parse_statement, parse_statement_bytes, run_async

__all__ = [
    "CanonicalRow",
    "StatementMeta",
    "ParseResult",
    "parse_statement",
    "parse_statement_bytes",
    "run_async",
]
