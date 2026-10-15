"""Format memory (RFC D3): fingerprint a source's layout, remember how to read it, forget on
demand. The tabular fast path this enables is the cost story's centerpiece — a repeat import
of a known export runs ZERO LLM calls; a drifted layout misses/fails validation and re-learns
with a version bump, never a silent mis-parse."""

from __future__ import annotations

import hashlib
import logging
import unicodedata

from backend.db.statement_format_store import StatementFormatStore
from backend.services.statements import page_hash

logger = logging.getLogger(__name__)

# A fingerprint candidate per early row — bank preambles push headers down (a Mekong Bank
# XLSX puts its header at grid index 12). INVARIANT: this window must cover every header
# `learn()` can key by, or a profile becomes write-only — learned and re-learned (re-billed)
# on every import but never found, and remembered instructions silently dropped (a reported
# case). Schema inference samples grid[:30] (`tiers._MAX_SCHEMA_SAMPLE_ROWS`),
# so 40 covers any inferable header with margin; `learn()` refuses anything past it.
_MAX_HEADER_SCAN_ROWS = 40


def _normalize_cell(label: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(label or ""))
    stripped = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return " ".join(stripped.casefold().split())


def fingerprint_row(row: list[str]) -> str | None:
    """The fingerprint of one candidate header row: sha256 over the normalized cell tuple.
    Rows with fewer than 2 non-empty cells can't identify a table → None."""
    cells = [_normalize_cell(c) for c in row]
    if sum(1 for c in cells if c) < 2:
        return None
    ident = "|".join(cells).rstrip("|")
    return hashlib.sha256(ident.encode("utf-8")).hexdigest()


def candidate_fingerprints(grid: list[list[str]]) -> list[tuple[int, str]]:
    """(row_index, fingerprint) for each of the first few rows — the header's position isn't
    known before a schema exists, so lookup tries every early candidate."""
    out: list[tuple[int, str]] = []
    for i, row in enumerate(grid[:_MAX_HEADER_SCAN_ROWS]):
        fp = fingerprint_row(row)
        if fp:
            out.append((i, fp))
    return out


def find_profile(grid: list[list[str]]) -> dict | None:
    """The saved profile matching any early-row fingerprint of this grid, or None."""
    try:
        store = StatementFormatStore()
        for _, fp in candidate_fingerprints(grid):
            hit = store.find_by_fingerprint(fp)
            if hit:
                return hit
    except Exception:  # memory is an optimization — its failure must never block a parse
        logger.exception("format-profiles: lookup failed")
    return None


def learn(grid: list[list[str]], schema: dict, *, origin: str = "inferred") -> dict | None:
    """Remember a schema that just parsed-and-validated, keyed by ITS header row's
    fingerprint. Returns the stored profile (or None when the header can't fingerprint)."""
    try:
        header_idx = int(schema.get("header_row", 0))
        # Refuse a header the lookup window can't reach: a profile keyed past
        # _MAX_HEADER_SCAN_ROWS would be unfindable by find_profile/_instruction_fingerprints
        # — write-only memory. No profile beats broken profile.
        if header_idx >= len(grid) or header_idx >= _MAX_HEADER_SCAN_ROWS:
            return None
        fp = fingerprint_row(grid[header_idx])
        if not fp:
            return None
        label = " • ".join(c for c in (str(x).strip() for x in grid[header_idx]) if c)[:120]
        return StatementFormatStore().upsert(
            fp, kind="tabular", schema=schema, source_label=label, origin=origin
        )
    except Exception:
        logger.exception("format-profiles: learn failed")
        return None


def record_use(profile: dict, *, account_ids: list[str] | None = None) -> None:
    try:
        StatementFormatStore().record_use(profile["id"], account_ids=account_ids)
    except Exception:
        logger.exception("format-profiles: record_use failed")


def remember_accounts(profile_id: str, account_ids: list[str]) -> None:
    """Persist the Choose step's account chips onto this source's format profile, so the next
    import of the same layout pre-selects them.

    Stamped at PREVIEW, not at commit — unlike ``account_id``, which ``_stamp_profile_account``
    only writes once rows actually booked. The chip set is a UI prefill, and the case that
    most needs it is the one a commit gate loses: a monthly statement whose rows all
    de-duplicate, previewed and then closed. A wrong pick self-corrects, since re-previewing
    with the corrected chips overwrites the set. Best-effort — never blocks a preview."""
    try:
        StatementFormatStore().set_accounts(profile_id, account_ids)
    except Exception:
        logger.exception("format-profiles: remember_accounts failed")


def _infer_success_values(
    grid: list[list[str]], header_idx: int, status_col: int | None
) -> list[str]:
    """The manual-mapping teach flow has no UI for enumerating which status values mean
    "completed" (unlike the LLM tier, which is prompted for it) — so a mapped status column
    infers it from the data itself: for a bank/wallet export the SUCCESSFUL case is
    overwhelmingly the majority value (e.g. "Thành công" vs. a handful of "Thất bại" rows),
    so the single most-common non-blank value is taken as the one success value. Every other
    distinct value (failed/reversed/pending/...) then gets skipped by tier1_tabular's status
    guard. A column with only one distinct value is a no-op (nothing to skip)."""
    if status_col is None:
        return []
    counts: dict[str, int] = {}
    for row in grid[header_idx + 1 :]:
        if status_col >= len(row):
            continue
        val = (row[status_col] or "").strip()
        if val:
            counts[val] = counts.get(val, 0) + 1
    if not counts:
        return []
    return [max(counts, key=counts.get)]


def schema_from_manual_mapping(
    content: bytes, fmt: str, mapping: dict, *, password: str | None = None
) -> tuple[list, dict] | None:
    """Convert a user's name-based column mapping into ``(grid, schema)`` — the deterministic
    core behind the teach flow. None when the mapped column names don't line up with any
    early header row (the caller decides whether that's best-effort or a loud 422).

    ``password`` unlocks an encrypted xlsx — without it this re-derivation would fail
    silently (caught by ``learn_from_manual_mapping``'s best-effort try/except) and
    mis-report as "the column mapping didn't line up," masking the real password problem."""
    from backend.services.statements.parser import to_upload
    from backend.services.statements.tiers import parse_grid

    grid = parse_grid(to_upload(content, fmt, password=password).get("text") or "")
    wanted = {k: _normalize_cell(v) for k, v in mapping.items() if v}
    if not wanted.get("date"):
        return None
    header_idx, cols = None, {}
    for i, row in enumerate(grid[:_MAX_HEADER_SCAN_ROWS]):
        normalized = [_normalize_cell(c) for c in row]
        hits = {
            field: normalized.index(label) for field, label in wanted.items() if label in normalized
        }
        if "date" in hits and len(hits) >= 2:
            header_idx, cols = i, hits
            break
    if header_idx is None:
        return None
    status_col = cols.get("status")
    schema: dict = {
        "header_row": header_idx,
        "date_col": cols["date"],
        "desc_col": cols.get("description"),
        "payee_col": None,
        "id_col": None,
        "balance_col": None,
        "status_col": status_col,
        "category_col": cols.get("category"),
        "success_values": _infer_success_values(grid, header_idx, status_col),
        "currency": None,
        "date_order": None,
    }
    if "amount" in cols:
        schema |= {"debit_col": cols["amount"], "credit_col": cols["amount"], "signed": True}
    elif "inflow" in cols or "outflow" in cols:
        schema |= {
            "debit_col": cols.get("outflow", cols.get("inflow")),
            "credit_col": cols.get("inflow", cols.get("outflow")),
            "signed": False,
        }
    else:
        return None
    return grid, schema


def learn_from_manual_mapping(
    content: bytes, fmt: str, mapping: dict, *, password: str | None = None
) -> dict | None:
    """ "Manual maps are memory too" (RFC D3): a user who hand-mapped a file's columns has
    TAUGHT us this source — convert the name-based mapping into a column schema and save it,
    so the next import of the same layout is one-click Tier 0. Best-effort, never blocks."""
    try:
        derived = schema_from_manual_mapping(content, fmt, mapping, password=password)
        if derived is None:
            return None
        grid, schema = derived
        return learn(grid, schema, origin="manual")
    except Exception:
        logger.exception("format-profiles: learn_from_manual_mapping failed")
        return None


# ── Upload-time instructions (SI-67) — owner guidance remembered per source ──────────────
# Same memory as mappings: keyed by the header-row fingerprint when the source is tabular.
# A document source (pdf, structured exports) has no header row, so it keys by a TEMPLATE
# fingerprint — the digit-stripped, normalized head of its extracted text (bank-statement
# headers are template-stable modulo account numbers/dates). A scanned PDF with no
# extractable text keys by its MASTHEAD IMAGE instead (`page_hash`): a perceptual hash of the
# rendered top of page one, looked up by a unique nearest key within a small bit distance.

MAX_INSTRUCTIONS_CHARS = 20_000  # the prompt-side fence caps to the same bound

_DOC_FINGERPRINT_LINES = 12


def _is_template_line(line: str) -> bool:
    """Is this line stable TEMPLATE text (masthead, headings, column headers)?

    A line is rejected when it carries a digit — dates, periods, account numbers, figures —
    OR any NON-PRINTABLE character. That second rule is not paranoia: a Meridian Bank
    consolidated statement draws its issue date in a custom font that maps digits to
    control-character glyph codes (``\x03\x04\x01\x01 \x02\x04...``), so ``str.isdigit`` sees
    no digit and the date sailed straight into the "digit-free" hash — giving the same source a
    DIFFERENT fingerprint every month, and re-teaching it as a brand-new format each time
    (reported case: "when importing june, it doesn't show the accounts"). Real
    template text is printable; a run of control codes never is.
    """
    return not any(c.isdigit() or not c.isprintable() for c in line)


def _document_fingerprint(content: bytes, fmt: str, *, legacy: bool = False) -> str | None:
    """Template fingerprint: the first DIGIT-FREE lines of the extracted text (bank
    masthead, headings, column headers). Any line carrying a digit — dates, periods,
    account numbers, figures, street addresses — is excluded WHOLESALE, so next month's
    statement of the same source hashes identically."""
    return _fingerprint_text(_template_text(content, fmt), legacy=legacy)


def _template_text(content: bytes, fmt: str) -> str:
    """The document's text in READING ORDER (``layout=False``) — deliberately NOT the
    layout-preserving rendering the parse uses. This hash is an IDENTITY, and an identity
    must not move when the parser's rendering changes. It did once: switching PDF extraction
    to ``extraction_mode="layout"`` (to recover the withdrawal/deposit columns) rewrote every
    line, so every stored fingerprint stopped matching. Every remembered PDF source silently
    became unreachable — accounts, instructions and taught schema stranded on an address
    nothing computed any more — and the next import minted a fresh empty profile beside it.
    Re-importing could not heal it: the old profile was no longer addressable (reported case:
    a Meridian Bank source whose three chosen accounts were lost this way). Reading order is
    the STABLE rendering: it is what every existing profile was keyed by, and it does not
    care where a column sits.

    A PDF goes through ``pdf_text`` — extraction only. ``to_upload`` would render every page
    of a scan to an image the fingerprint never reads, on the event loop, on every file pick.
    """
    from backend.services.statements.parser import pdf_text, to_upload

    if fmt == "pdf":
        return pdf_text(content, layout=False)
    return to_upload(content, fmt, layout=False).get("text") or ""


def _fingerprint_text(text: str, *, legacy: bool = False) -> str | None:
    """``legacy`` reproduces the OLD digit-only rule so a profile stored under it is still
    findable — see `_instruction_fingerprints`, which returns both and upgrades on hit."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = _normalize_cell(raw)
        keep = (not any(c.isdigit() for c in line)) if legacy else _is_template_line(line)
        if not line or not keep:
            continue
        lines.append(line)
        if len(lines) >= _DOC_FINGERPRINT_LINES:
            break
    head = "|".join(lines)
    if len(head) < 40:
        return None
    return "doc:" + hashlib.sha256(head.encode("utf-8")).hexdigest()


def _instruction_fingerprints(content: bytes, fmt: str, text: str | None = None) -> list[str]:
    """Every fingerprint this file can be keyed by, best-first: the early header-row
    candidates (tabular sources — the SAME keys mappings/schemas use), else the document
    template fingerprint, else — a PDF with no text at all — its masthead image key."""
    if fmt in ("csv", "xlsx"):
        from backend.services.statements.parser import to_upload
        from backend.services.statements.tiers import parse_grid

        grid = parse_grid(to_upload(content, fmt).get("text") or "")
        return [fp for _, fp in candidate_fingerprints(grid)]
    # STRICT first, then the LEGACY key. A stored profile must stay reachable across a change
    # to the hashing rule: when the rule tightened, every existing profile silently became
    # unaddressable and the next import minted an empty one beside it, stranding the owner's
    # accounts and instructions (2026-08-09). Callers that MINT use ``[0]``; callers that LOOK
    # UP walk the list, so an old profile is found and then upgraded onto the strict key.
    if text is None:
        text = _template_text(content, fmt)
    out = [_fingerprint_text(text), _fingerprint_text(text, legacy=True)]
    seen: list[str] = []
    for fp in out:
        if fp and fp not in seen:
            seen.append(fp)
    if not seen and fmt == "pdf":
        image_key = page_hash.masthead_fingerprint(content)
        if image_key:
            seen.append(image_key)
    return seen


def _lookup(store: StatementFormatStore, fp: str) -> dict | None:
    """Exact key first; an image key also resolves to one uniquely nearest stored image key
    within `page_hash.MAX_DISTANCE` bits. A tied nearest match is ambiguous and fails closed,
    so a masthead that shifts by a pixel or carries a date still lands on the remembered profile
    without allowing storage order to choose a different source. A stored key that is malformed
    or too sparse to be an identity (`page_hash.is_image_key`) is never a hit — nearness to
    nothing is not a match."""
    hit = store.find_by_fingerprint(fp)
    if hit or not page_hash.is_image_key(fp):
        return hit
    best: tuple[int, dict] | None = None
    ambiguous = False
    for row in store.all_profiles():
        key = str(row.get("fingerprint") or "")
        if not page_hash.is_image_key(key):
            continue
        bits = page_hash.distance(key, fp)
        if bits > page_hash.MAX_DISTANCE:
            continue
        if best is None or bits < best[0]:
            best = (bits, row)
            ambiguous = False
        elif bits == best[0]:
            ambiguous = True
    return best[1] if best and not ambiguous else None


def find_instructions(content: bytes, fmt: str) -> str:
    """The remembered instructions for this source, or ''. Best-effort — memory failure
    never blocks a parse (same contract as find_profile)."""
    try:
        store = StatementFormatStore()
        for fp in _instruction_fingerprints(content, fmt):
            hit = _lookup(store, fp)
            if hit and (hit.get("instructions") or "").strip():
                return str(hit["instructions"]).strip()
    except Exception:  # noqa: BLE001
        logger.exception("format-profiles: instructions lookup failed")
    return ""


def find_match_policy(content: bytes, fmt: str, instructions_hash: str | None) -> dict | None:
    """This source's CACHED match-policy extraction (RFC 2026-07-29 §D5), or None.

    Served ONLY while the profile's stored hash equals the hash of the instructions this run is
    actually applying — edited notes miss and re-derive, which is the whole point of storing the
    two together. None also means "never extracted" (a csv/xlsx that hasn't learned a profile
    yet, or a PDF too short to fingerprint), so the caller derives and pays once. Best-effort:
    a memory failure re-derives rather than blocking the preview."""
    if not instructions_hash:
        return None
    try:
        store = StatementFormatStore()
        for fp in _instruction_fingerprints(content, fmt):
            hit = _lookup(store, fp)
            if not hit or hit.get("match_policy") is None:
                continue
            if hit.get("match_policy_instructions_hash") == instructions_hash:
                return dict(hit["match_policy"])
    except Exception:  # noqa: BLE001
        logger.exception("format-profiles: match-policy lookup failed")
    return None


def remember_match_policy(
    content: bytes, fmt: str, policy: dict, instructions_hash: str | None
) -> None:
    """Cache the extraction on this source's format profile so the second import of a known
    source costs ZERO (§D5). Writes onto an EXISTING profile only — minting one here would key
    a policy to a file whose format was never learned. A source with no profile simply
    re-derives every preview (R6's correction: that path is real for csv/xlsx and short PDFs,
    which is why the extractor's instructions slice is capped well below the parse prompt's).
    Best-effort: a failed write leaves both fields unset, never a half-written pair."""
    try:
        store = StatementFormatStore()
        for fp in _instruction_fingerprints(content, fmt):
            hit = _lookup(store, fp)
            if hit:
                store.set_match_policy(hit["id"], policy, instructions_hash)
                return
    except Exception:  # noqa: BLE001
        logger.exception("format-profiles: remember_match_policy failed")


def peek_source(content: bytes, fmt: str) -> dict:
    """Cheap recognition probe for the choose step (SI-67 prefill): fingerprint-only — ZERO
    LLM calls, zero writes. ``known`` = this source has a saved format profile;
    ``instructions`` = the remembered guidance (first hit carrying text, the same rule
    ``find_instructions`` applies); ``format_label`` = the profile's display label;
    ``account_ids`` = the accounts this source was last imported into, ANCHOR FIRST, so the
    Choose step can restore the chip set instead of making the owner re-pick the same wallets
    every month. The ids are echoed RAW — the caller filters them against the live account
    list, so a deleted account simply drops out of the prefill. Any failure reads as an
    unknown source — a probe must never block a file pick.

    A DOCUMENT also answers from its own printed account number (``_printed_account``), which
    leads the chip set: the fingerprint is an exact hash of the first template lines, so a
    month that adds one conditional line (a DBS card prints an FX-fee notice only when there
    was a foreign charge) misses its profile outright — the printed card number does not move."""
    out: dict = {"known": False, "instructions": "", "format_label": None, "account_ids": []}
    try:
        text = None if fmt in ("csv", "xlsx") else _template_text(content, fmt)
        store = StatementFormatStore()
        for fp in _instruction_fingerprints(content, fmt, text):
            hit = _lookup(store, fp)
            if not hit:
                continue
            if not out["known"]:
                out["known"] = True
                out["format_label"] = str(hit.get("source_label") or "") or None
            if not out["account_ids"]:
                out["account_ids"] = store.accounts_for(hit)
            saved = str(hit.get("instructions") or "").strip()
            if saved:
                out["instructions"] = saved
                break
        printed = _printed_account(text)
        if printed:
            out["account_ids"] = [printed] + [a for a in out["account_ids"] if a != printed]
    except Exception:  # noqa: BLE001
        logger.exception("format-profiles: source peek failed")
    return out


def _printed_account(text: str | None) -> str | None:
    """The live account a document's printed number unambiguously names (SI-25 layer 1,
    deterministic — zero LLM). None for a tabular source, no match, or an ambiguous one."""
    if not text:
        return None
    from backend.services.statements.decisions import printed_account_match
    from backend.services.statements.tiers import harvest_text_last4s

    account_id, _ = printed_account_match(harvest_text_last4s(text))
    return account_id


def ensure_source_profile(content: bytes, fmt: str, *, source_label: str = "") -> dict | None:
    """Make sure this DOCUMENT source has a profile row, creating an empty one if needed.

    The profile is the source's IDENTITY, not a container for instructions — account chips,
    the format label and every future per-source memory hang off its id, and
    ``_remember_source_accounts`` bails outright when the preview carries no profile id.
    Creation used to happen only as a side effect of ``remember_instructions``, which returns
    early on empty text, so a PDF source the owner never typed guidance for could never
    remember anything: re-imported every month for a year, it re-asked for its accounts each
    time (reported case, Meridian Visa Infinite). The statement whose rows all
    de-duplicate — the case ``remember_accounts``' own docstring calls out as needing this
    most — is exactly the one nobody types instructions for.

    Never touches ``instructions``: an existing profile is returned untouched, so this can be
    called on every preview without clearing guidance the owner saved earlier. Tabular sources
    are excluded — their profile is minted by the mapping/schema learn, keyed the same way.
    Best-effort, never blocks a preview."""
    try:
        if fmt in ("csv", "xlsx"):
            return None
        store = StatementFormatStore()
        fps = _instruction_fingerprints(content, fmt)
        if not fps:
            return None
        for index, fp in enumerate(fps):
            hit = _lookup(store, fp)
            if not hit:
                continue
            if index > 0:
                # Matched on a LEGACY key. Re-key it now: leaving it there would work this
                # once and fail next month, because the legacy hash is the month-unstable one.
                upgraded = store.rekey(hit["id"], fps[0])
                logger.info("format-profiles: re-keyed %s onto the current fingerprint", hit["id"])
                return upgraded or hit
            return hit
        return store.upsert(
            fps[0],
            kind="document",
            schema={},
            source_label=(source_label or "").strip()[:120],
            instructions="",
        )
    except Exception:  # noqa: BLE001
        logger.exception("format-profiles: ensure_source_profile failed")
        return None


def remember_instructions(
    content: bytes, fmt: str, text: str, *, source_label: str = ""
) -> dict | None:
    """Persist the owner's upload-time instructions onto this source's format profile
    (empty ``text`` clears them). A tabular source with NO profile yet stores nothing — the
    caller re-remembers after the parse learns the profile, so instructions always land on
    the same row the schema lives on. A document source mints its ``kind="document"``
    profile on the spot. Best-effort, never blocks."""
    try:
        clean = (text or "").strip()[:MAX_INSTRUCTIONS_CHARS]
        store = StatementFormatStore()
        fps = _instruction_fingerprints(content, fmt)
        for fp in fps:
            hit = _lookup(store, fp)
            if hit:
                store.set_instructions(hit["id"], clean)
                return store.find_by_id(hit["id"])
        if not clean or not fps or fmt in ("csv", "xlsx"):
            return None
        return store.upsert(
            fps[0],
            kind="document",
            schema={},
            source_label=(source_label or "").strip()[:120],
            instructions=clean,
        )
    except Exception:  # noqa: BLE001
        logger.exception("format-profiles: remember_instructions failed")
        return None
