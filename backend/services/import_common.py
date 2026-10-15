"""Shared internals for the transaction importers (CSV / OFX / QIF).

Every format-specific parser (``import_csv``/``import_ofx``/``import_qif``) decodes its
file into the SAME internal two-leg ``create_transaction`` draft shape, then hands the
drafts to the helpers here for the format-agnostic parts: dedup against the ledger (and
within the file), the bounded preview projection, and the single commit through the ONE
ledger choke point ``ledger.bulk_create_transactions`` (so spend-rules + categorization +
base-currency math apply on every import path with no duplication).

Layer: services → db → core. No FastAPI/HTTP types here. Money/base figures are NEVER
fabricated — the ledger recomputes the base from the native leg. A re-import is idempotent
because every draft carries a stable origin ``(source, ref)`` pair (format-native:
``csv`` / ``ofx`` / ``qif`` / …), so the already-present rows are skipped (zero duplicates).
"""

from __future__ import annotations

import logging
import urllib.error
from dataclasses import dataclass, field

from backend.core.errors import recovery_hint
from backend.core.net_guard import UnsafeUrlError
from backend.core.pinned_http import pinned_urlopen
from backend.core.txn_sources import ext_ref_pairs, primary_pair
from backend.services import ledger as ledger_service

logger = logging.getLogger(__name__)

# How many drafts the preview returns (the rest are summarised by count).
PREVIEW_LIMIT = 25

# Hard cap on a URL fetch — an import file is line-oriented text/SGML, never tens of MB.
# Mirrors the API's upload cap; a body over this raises ImportTooLarge (→ HTTP 413).
MAX_FETCH_BYTES = 25 * 1024 * 1024  # 25 MB
_FETCH_TIMEOUT = 20.0  # seconds — a hosted CSV / published Google Sheet responds promptly


class ImportError_(ValueError):
    """A user-correctable problem with the upload or its contents.

    (Named with a trailing underscore so it never shadows the builtin ``ImportError``;
    the format modules re-export it under their own ``CsvImportError`` etc. aliases.)"""


class ImportTooLarge(ImportError_):
    """The fetched body exceeded ``MAX_FETCH_BYTES`` (→ HTTP 413, distinct from a 422)."""


# The import formats the codebase understands. ``ofx`` is the canonical parser for BOTH
# plain OFX and Quicken's QFX/QBO variant (same SGML/XML body + a few extra ``INTU.*`` tags
# the scanner harmlessly ignores) — ``qfx``/``qbo`` are accepted as explicit aliases and
# folded to ``ofx`` by ``detect_format``.
SUPPORTED_FORMATS = ("csv", "ofx", "qif", "mt940", "camt", "xlsx", "pdf")
_FORMAT_ALIASES = {"qfx": "ofx", "qbo": "ofx", "sta": "mt940"}


def detect_format(declared: str | None, filename: str | None, content: bytes) -> str:
    """Resolve the import format from (in priority order) an explicit ``declared`` field,
    the ``filename`` extension, then a content sniff. Returns a canonical parser id
    (``csv``/``ofx``/``qif``/``mt940``/``camt``/``xlsx``/``pdf``) — QFX/QBO fold to ``ofx``
    (same parser), ``sta`` to ``mt940``. Raises ImportError_ for an explicitly-declared
    unsupported format.

    Single source of truth shared by the REST route (``api.import_api._resolve_format``)
    and the Lucius ``import_transactions`` tool, so UI and Lucius detect format identically
    (no per-surface drift)."""
    fmt = (declared or "").strip().lower()
    if fmt and fmt != "auto":
        fmt = _FORMAT_ALIASES.get(fmt, fmt)
        if fmt not in SUPPORTED_FORMATS:
            raise ImportError_(
                f"unsupported format '{declared}' (use csv|ofx|qfx|qif|mt940|camt|xlsx|pdf)"
            )
        return fmt

    name = (filename or "").lower()
    if name.endswith(".ofx") or name.endswith(".qfx") or name.endswith(".qbo"):
        return "ofx"
    if name.endswith(".qif"):
        return "qif"
    if name.endswith(".mt940") or name.endswith(".sta"):
        return "mt940"
    if name.endswith(".camt"):
        return "camt"
    if name.endswith(".xlsx"):
        return "xlsx"
    if name.endswith(".pdf"):
        return "pdf"
    if name.endswith(".csv") or name.endswith(".txt"):
        return "csv"
    # An .xml extension is shared by camt + arbitrary XML; defer to the content sniff below.

    # Content sniff (no reliable extension) — each structured format carries an unambiguous
    # header. Imported here (not at module top) to avoid a parser↔common import cycle. The
    # binary magics (PDF %PDF-, XLSX ZIP/OOXML) are checked first — they're unambiguous and a
    # stray text sniff could mis-read them.
    from backend.services import (
        import_camt,
        import_mt940,
        import_ofx,
        import_pdf,
        import_qif,
        import_xlsx,
    )

    if import_pdf._looks_like_pdf(content):
        return "pdf"
    if import_xlsx._looks_like_xlsx(content):
        return "xlsx"
    if import_ofx._looks_like_ofx(content):
        return "ofx"
    if import_camt._looks_like_camt(content):
        return "camt"
    if import_qif._looks_like_qif(content):
        return "qif"
    if import_mt940._looks_like_mt940(content):
        return "mt940"
    return "csv"  # default to the mapped path; an unparseable CSV still 422s cleanly


def fetch_url_bytes(url: str) -> tuple[bytes, str]:
    """Fetch an import file over HTTP(S) and return ``(content, filename_hint)``.

    Handles a plain hosted CSV/OFX/QIF URL and a Google Sheets "Publish to web → CSV"
    link (an ordinary ``…/pub?output=csv`` URL — no auth, no per-provider special-casing).
    Redirects are followed (Google's publish URL 307s to a ``docs.google.com`` CSV host).
    The ``filename_hint`` (the final URL's last path segment) feeds the SAME extension/
    content format detection the upload path uses, so an OFX/QIF URL auto-detects too.

    NEVER trusts the network: a non-http(s) scheme, an unreachable/erroring host, a
    non-2xx status or an oversized body all raise ImportError_/ImportTooLarge (user-
    correctable → 422/413), never an opaque 500.

    SSRF: guarded. This docstring used to say "the operator pastes their OWN sheet URL — we
    do not block private ranges", and that premise was simply wrong: ``source_url`` is a field
    on ``ImportTransactionsInput`` (``ai/tools/imports.py``), so the URL can be chosen by the
    MODEL, and therefore by whatever untrusted text steered it — an email body, a fetched
    page, a statement memo. Injected text asking to "import your statement from
    ``http://169.254.169.254/…``" would have been honoured, with the response body surfacing
    back through the import preview.

    Fetched through ``core.pinned_http.pinned_urlopen`` rather than ``httpx`` with
    ``follow_redirects=True``: it resolves, validates and PINS every hop, so a host that
    passes the check cannot then re-resolve to a private address, and a public URL cannot
    302 inward. Redirects still work — Google's publish URL 307s to ``docs.google.com``,
    which is the whole reason this function follows them (audit FINDING 12)."""
    clean = (url or "").strip()
    if not clean:
        raise ImportError_("No URL provided.")
    if not (clean.startswith("http://") or clean.startswith("https://")):
        raise ImportError_("The URL must start with http:// or https://.")

    try:
        with pinned_urlopen(
            clean,
            headers={"User-Agent": "OpenNetWorth-import/1.0"},
            timeout=_FETCH_TIMEOUT,
        ) as raw:
            status = raw.status
            final_url = raw.geturl()
            # Read one byte past the cap so an oversized body is REFUSED rather than
            # silently truncated into a short, plausible-looking import.
            content = raw.read(MAX_FETCH_BYTES + 1)
    except UnsafeUrlError as exc:
        raise ImportError_(f"Refusing to fetch that URL: {exc}")
    except urllib.error.HTTPError as exc:
        raise ImportError_(f"The URL returned HTTP {exc.code} — check it is published / public.")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ImportError_(f"Could not fetch the URL: {exc}")

    if status >= 400:
        raise ImportError_(f"The URL returned HTTP {status} — check it is published / public.")

    if len(content) > MAX_FETCH_BYTES:
        raise ImportTooLarge("The fetched file is too large.")
    if not content.strip():
        raise ImportError_("The URL returned no data — check it is published as CSV.")

    # Filename hint for format detection: prefer the final (redirected) URL's last path
    # segment (a Google publish URL redirects to a path ending in the export name).
    path = str(final_url).split("?", 1)[0].rstrip("/")
    filename_hint = path.rsplit("/", 1)[-1] if "/" in path else ""
    return content, filename_hint


@dataclass
class RowResult:
    """One parsed source row after mapping — either a draft or a skip with a reason."""

    line: int  # 1-based row number within the data rows
    draft: dict | None = None
    skip_reason: str | None = None
    duplicate: bool = False
    # The existing ledger transaction id this row's (source, ref) pair is already booked
    # under — None for an intra-file repeat (nothing in the ledger yet to link to).
    duplicate_of: str | None = None
    # Advisory anomaly/near-duplicate flags (L2) — populated by import_anomalies.detect_anomalies
    # on the PREVIEW path only; each is {type, reason, existing_id?, line?}. Empty = clean row.
    flags: list[dict] = field(default_factory=list)


@dataclass
class PreviewResult:
    """The dry-run outcome: a bounded sample of drafts plus the full counts."""

    columns: list[str]
    total_rows: int
    importable: int  # drafts that would be written (excludes dups + skips)
    duplicates: int  # already in the ledger (or repeated within this file)
    skipped: int  # malformed/empty rows that could not be mapped
    sample: list[dict] = field(default_factory=list)  # first PREVIEW_LIMIT drafts (display shape)
    skip_samples: list[dict] = field(default_factory=list)  # first few skip reasons (display)
    # Advisory anomaly layer (L2) — counts across ALL importable drafts (not just the sample);
    # per-sample drafts carry their own `flags`. Purely informational — the commit imports
    # everything regardless; the user decides. Defaults to all-zero (a clean book never fabricates).
    anomaly_summary: dict = field(
        default_factory=lambda: {
            "near_duplicate": 0,
            "intra_batch_duplicate": 0,
            "transfer_pair": 0,
            "amount_anomaly": 0,
            "flagged_rows": 0,
        }
    )


def normalise_decimal(s: str) -> str:
    """Normalise a numeric string's grouping/decimal separators to a plain ``.``-decimal so
    ``float()`` accepts it — used by the structured (OFX/QIF) parsers, which see both US
    (``1,234.56``) and European (``1.234,56``) conventions.

    Heuristic: when BOTH ``,`` and ``.`` appear, the LAST one is the decimal separator and
    the other is grouping; with only ``,`` it's the decimal; with only ``.`` it's already
    standard. (A lone ``,`` as a thousands separator — ``1,234`` — is rare in OFX/QIF amount
    fields and would mis-read as ``1.234``; the importers' callers strip currency symbols
    first, and a real grouped value virtually always carries a decimal part too.)"""
    s = s.strip()
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):  # European: 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:  # US: 1,234.56
            s = s.replace(",", "")
    elif "," in s:  # only a comma → decimal comma
        s = s.replace(",", ".")
    return s


def known_external_refs() -> set[tuple[str | None, str]]:
    """Every importer ``(source, ref)`` provenance pair already in the ledger (for
    re-import dedup), across all format namespaces (``csv``/``ofx``/``qif``/``mt940``/``camt``/
    ``xlsx``/``pdf``/…).

    Includes pairs ABSORBED into a canonical row's ``external_refs[]`` by a dedup merge — so a
    re-import of a source that was merged away is still skipped (the bulk-commit path does not
    run the engine FLOOR per row; this pre-filter is the re-import floor for imports). This is
    exactly what ``core.txn_sources.ext_ref_pairs`` returns per row (every ``external_refs[]``
    pair, origin and absorbed alike).

    User-deleted rows (``deleted=True`` AND ``merged_into`` is falsy) are excluded — their
    pair is FREE again so a re-import recreates the row. Merge tombstones (``merged_into`` set)
    are also deleted but their absorbed pair is already inherited by the canonical's
    ``external_refs[]``, so the canonical row covers them."""
    return set(known_external_ref_ids())


def known_external_ref_ids() -> dict[tuple[str | None, str], str]:
    """Like ``known_external_refs`` but keeps each pair's owning transaction id, so a
    duplicate-flagged import row can link back to the exact ledger entry protecting it."""
    out: dict[tuple[str | None, str], str] = {}
    for t in ledger_service.transactions.all():
        # A user-delete (deleted=True, no merged_into) frees the pair — re-import must succeed.
        if t.get("deleted") and not t.get("merged_into"):
            continue
        for pair in ext_ref_pairs(t):
            out.setdefault(pair, t["id"])
    return out


def disambiguate_identical_rows(results: list[RowResult]) -> None:
    """Append a stable occurrence index to a repeated ref VALUE, counted independently per
    entry (not per row), in file order (RFC transaction-dedup-engine §id fix, amended for the
    sibling-collision case below).

    Content-hashed formats (CSV/QIF) derive every ref from the row's fields, so two
    genuinely-distinct identical rows (two $5 coffees the same day) collide on each ref and the
    second is silently dropped by ``flag_duplicates`` — real data loss. This appends ``:1``,
    ``:2`` … to a repeated value so all survive (the entry's source unchanged), while a value's
    FIRST occurrence stays bare (so a re-import of a file with no twins still dedups
    byte-for-byte, and existing ledger refs are unchanged). Order-stable across re-imports:
    same file → same encounter sequence → same refs.

    The counter is keyed on each entry's own ref VALUE, never on the row's occurrence count.
    ``external_ref`` hashes only account|date|amount, so two DIFFERENT rows (a $5 coffee and a
    $5 bus ride the same day) share one ref and the second is suffixed in file order — stable
    across re-imports of the same file, but a reordered file swaps the two.

    OFX (``FITID``) and bank connectors carry native per-event ids and never reach here.
    Mutates the drafts in place."""
    seen: dict[str, int] = {}
    for r in results:
        if r.draft is None:
            continue
        for entry in r.draft.get("external_refs") or []:
            ref = entry.get("ref")
            if not ref:
                continue
            n = seen.get(ref, 0)
            seen[ref] = n + 1
            if n:
                entry["ref"] = f"{ref}:{n}"


def flag_duplicates(results: list[RowResult]) -> None:
    """Mark each draft RowResult duplicate if its origin ``(source, ref)`` pair is
    already in the ledger OR repeats earlier in this same file. Mutates the results in place."""
    known = known_external_ref_ids()
    seen_in_file: set[tuple[str | None, str]] = set()
    for r in results:
        if r.draft is None:
            continue
        key = primary_pair(r.draft)
        owner = next((known[p] for p in ext_ref_pairs(r.draft) if p in known), None)
        r.duplicate = owner is not None or key in seen_in_file
        r.duplicate_of = owner
        seen_in_file.add(key)


def display_draft(draft: dict, flags: list[dict] | None = None) -> dict:
    """A flat, UI-friendly projection of a draft for the preview table (shared shape).
    ``flags`` (the advisory anomaly markers for this row) ride along so the preview sample
    can mark a near-duplicate / unusual-amount row inline.

    Optional enrichment keys (populated by richer parsers like the LLM PDF importer, absent
    for plain CSV/OFX): ``payee`` (a clean merchant name shown as the row's primary label),
    a raw ``import_description`` (kept verbatim, shown as the sub-line), ``original_amount`` /
    ``original_currency`` (the pre-conversion foreign charge), ``reference`` (the bank Ref No.),
    and ``external_refs`` (so the client-side commit can forward the pair(s)
    for exact re-import dedup)."""
    if draft["kind"] == "income":
        amount = draft["to_amount"]
        currency = draft["to_currency"]
    else:
        amount = -draft["from_amount"]
        currency = draft["from_currency"]
    raw = draft.get("import_description")
    return {
        "date": draft["date"],
        "payee": draft.get("payee"),  # clean merchant name (None for plain CSV/OFX)
        # Primary description: the RAW narration when a richer parser kept it, else the draft's
        # bank/matching string (`description` post-split, ADR 2026-07-17).
        "description": raw if raw else (draft.get("description") or ""),
        "amount": amount,
        "currency": currency,
        "kind": draft["kind"],
        "category": draft.get("category", ""),
        "original_amount": draft.get("original_amount"),
        "original_currency": draft.get("original_currency"),
        "reference": draft.get("reference"),
        "external_refs": draft.get("external_refs") or [],
        "flags": flags or [],
    }


def build_preview(columns: list[str], total_rows: int, results: list[RowResult]) -> PreviewResult:
    """Roll mapped RowResults up into a PreviewResult (counts + bounded display sample).
    Writes nothing — the caller flags duplicates first via ``flag_duplicates``.

    Runs the ADVISORY anomaly/near-duplicate detector (L2) over the importable drafts and
    surfaces its per-row flags + a summary. This is preview-only and informational — the
    commit path imports everything regardless (flagged rows are NOT dropped)."""
    # Advisory layer — annotates each draft RowResult.flags + returns counts. Imported here
    # (not at module top) to avoid a service↔service import cycle at load time; it is pure.
    from backend.services import import_anomalies

    anomaly_summary = import_anomalies.detect_anomalies(results)

    importable = sum(1 for r in results if r.draft and not r.duplicate)
    duplicates = sum(1 for r in results if r.draft and r.duplicate)
    skipped = sum(1 for r in results if r.draft is None)

    sample = [display_draft(r.draft, r.flags) for r in results if r.draft and not r.duplicate][
        :PREVIEW_LIMIT
    ]
    skip_samples = [{"line": r.line, "reason": r.skip_reason} for r in results if r.draft is None][
        :10
    ]
    return PreviewResult(
        columns=columns,
        total_rows=total_rows,
        importable=importable,
        duplicates=duplicates,
        skipped=skipped,
        sample=sample,
        skip_samples=skip_samples,
        anomaly_summary=anomaly_summary,
    )


def build_commit(total_rows: int, results: list[RowResult]) -> dict:
    """Persist the non-duplicate drafts through the ONE ledger choke point
    (``bulk_create_transactions`` → spend-rules + categorization + base math apply).
    Idempotent. Returns ``{created, duplicates, skipped, total_rows}``."""
    duplicates = sum(1 for r in results if r.draft and r.duplicate)
    skipped = sum(1 for r in results if r.draft is None)
    drafts = [r.draft for r in results if r.draft and not r.duplicate]

    created_rows: list[dict] = []
    if drafts:
        # Provenance (transaction-provenance RFC): attribute a statement import to
        # actor=user / surface=import — but only when no richer scope is already active, so a
        # chat-driven import keeps its chat session + intent. We own the clear only if we set.
        from backend.core.audit_context import clear_audit_scope, set_audit_scope_if_absent

        _owns_scope = set_audit_scope_if_absent("user", "import")
        try:
            created_rows, refused_rows = ledger_service.bulk_create_transactions(drafts)
            # A refusal is REPORTED, never dropped silently (ADR 2026-08-01). The preview
            # already showed the user this row's near_duplicate flag; the guard is what
            # stops it being written anyway on a blind commit-everything path.
            for _r in refused_rows:
                logger.warning(
                    "import: row refused as duplicate of %s - %s",
                    _r.get("_duplicate_of"),
                    _r.get("_duplicate_reason"),
                )
        finally:
            if _owns_scope:
                clear_audit_scope()

    # Dedup / match engine (RFC transaction-dedup-engine): surface cross-source duplicates and
    # transfer pairs among the just-imported rows vs the existing ledger. Created-then-flagged —
    # rows are NOT dropped; fuzzy matches are enqueued for review (auto-merge needs Phase 2). The
    # exact-id hard dedup above (flag_duplicates) already removed byte-identical re-imports. The
    # review counts are NOT folded into this import summary (stable contract) — the UI reads them
    # from GET /api/matches/reviews. Best-effort: never break a completed import.
    if created_rows:
        try:
            from backend.services import match_review

            # Group this import's review items under its batch so the UI resolves a messy import
            # in one decision (RFC §review ergonomics).
            batch_id = created_rows[0].get("batch_id")
            match_review.review_created_rows(created_rows, batch_id=batch_id)
        except Exception:  # noqa: BLE001 — surfacing must never break a completed import
            logger.exception("import: dedup review pass failed")

    return {
        "created": len(created_rows),
        "duplicates": duplicates,
        "skipped": skipped,
        "total_rows": total_rows,
    }


def resolve_account(account_id: str | None, account_name: str | None) -> str:
    """Resolve a destination ONW account id for a structured (OFX/QIF) import. A chosen
    ``account_id`` wins (and may itself be a name); otherwise the file-derived name is used.
    Raises ImportError_ if neither yields a known account.

    Both refs go through ``services.accounts.resolve_account_ref`` — the same resolver the
    ledger create choke uses. This used to be a second, private name-matching loop that
    returned whichever match it hit FIRST, so a statement naming an account two of yours
    share (the "IBKR USD" / "IBKR SGD" custodian shape) would import a whole file's rows
    against an arbitrary one of them. The shared resolver refuses instead; the ambiguity
    error is translated to ImportError_ so the import surfaces keep their error contract."""
    from backend.core.errors import ValidationError
    from backend.services import accounts as accounts_service

    ref: str = (account_id or "").strip() or (account_name or "").strip()
    if not ref:
        raise ImportError_("No account chosen and the file names no account.")
    rows = ledger_service.accounts.all()  # ONE store identity, shared with the ledger's checks
    try:
        resolved = accounts_service.resolve_account_ref(ref, rows=rows)
    except ValidationError as exc:
        raise ImportError_(str(exc)) from exc
    if resolved is None:
        names = [(a.get("name") or "").strip() for a in ledger_service.accounts.all()]
        raise ImportError_(
            f"No account named '{ref}'." + recovery_hint(ref, names, tool_hint="list_accounts")
        )
    return resolved
