"""StatementFormatStore — learned statement-import format memory (RFC statement-import-unified D3).

A profile remembers HOW to read one source's export (the column schema the LLM inferred, a
manual mapping the user confirmed, or a taught statement skill) keyed by a content
FINGERPRINT of its header row — so a repeat import parses deterministically with ZERO LLM
calls, and a bank changing its layout simply misses the fingerprint and re-learns (version
bump), never silently mis-parses.

Storage-format-only CRUD (matching/learning logic lives in
``services/statements/profiles.py``). User knowledge → checkpoint-INCLUDED, portable.
Profiles are FORGETTABLE by design (the GnuCash lesson): ``delete`` fully removes one and the
next import just re-infers.

Row schema:
    id            : str   "fmt_<12hex>"
    fingerprint   : str   sha256 of the normalized header signature (lookup key, unique)
    kind          : str   "tabular" | "document"
    source_label  : str   human label (the header row / issuer) for the UI list
    origin        : str   "inferred" | "manual" | "taught"
    schema        : dict  the tier-1 column schema (tabular) / extraction hints (taught)
    instructions  : str   upload-time owner guidance for this source (SI-67) — threaded
                          FENCED into the LLM parse/hint prompts; "" = none
    match_policy  : dict|None  the DETERMINISTIC settings extracted from ``instructions``
                          (RFC 2026-07-29 §D5): ``{"fields": {...}, "note": str}``, where
                          ``fields`` holds account NAMES, never ids — resolution against the
                          statement's routed accounts happens per-preview in services. Absent
                          = never extracted; ``{"fields": {}}`` = extracted, notes state none.
    match_policy_instructions_hash : str|None  which ``instructions`` text the cached policy
                          was extracted from. Written in the SAME save as ``match_policy`` so
                          a failed write leaves BOTH unset and the next preview re-derives,
                          never a stale policy under a fresh hash (security review F7).
    account_ids   : list[str] every account the source was last routed into, anchor FIRST
                          (index 0) — a statement can cover several wallets, so the Choose
                          step's chips are restored as a set, not just one account.
    version       : int   bumped every re-learn after a format drift
    uses          : int
    created_at / updated_at / last_used_at : ISO 8601

PLAN 4D TASK 2: DB-backed (Tier-3, table ``statement_formats``) via ``PayloadTableStore``.
``find_by_id``/``delete`` are INHERITED unchanged from the base — the original's own hand-rolled
versions were already byte-identical to ``PayloadTableStore``'s. **``set_match_policy`` writes
``match_policy`` + ``match_policy_instructions_hash`` in a SINGLE ``payload`` column UPDATE
inside ONE base transaction** (security-review-critical, F7): a single-column single-row UPDATE
is atomic by construction, so a failed write can never land one field without the other — the
one outcome that must be impossible is a stale ``match_policy`` under a fresh
``match_policy_instructions_hash``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update

from backend.core.ids import new_id
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import statement_formats


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_accounts(account_ids: list[str] | None) -> list[str]:
    """The stored chip set: anchor first, de-duplicated, blanks dropped. Order is data —
    the Choose step restores ``[0]`` as the anchor and the rest as extra chips."""
    out: list[str] = []
    for value in account_ids or []:
        clean = str(value or "").strip()
        if clean and clean not in out:
            out.append(clean)
    return out


def _new_profile(
    fingerprint: str,
    *,
    kind: str,
    schema: dict,
    source_label: str,
    origin: str,
    accounts: list[str],
    instructions: str | None,
    now: str,
) -> dict:
    return {
        "id": new_id("fmt"),
        "fingerprint": fingerprint,
        "kind": kind,
        "source_label": source_label,
        "origin": origin,
        "schema": schema,
        "instructions": instructions or "",
        "account_ids": accounts,
        "version": 1,
        "uses": 0,
        "created_at": now,
        "updated_at": now,
        "last_used_at": None,
    }


def _merge_profile(
    existing: dict,
    *,
    kind: str,
    schema: dict,
    source_label: str,
    origin: str,
    accounts: list[str],
    instructions: str | None,
    now: str,
) -> dict:
    merged = dict(existing)
    if merged.get("schema") != schema:
        merged["schema"] = schema
        merged["version"] = int(merged.get("version") or 1) + 1
    rank = {"inferred": 0, "manual": 1, "taught": 2}
    if rank.get(origin, 0) >= rank.get(merged.get("origin") or "inferred", 0):
        merged["origin"] = origin
    if source_label:
        merged["source_label"] = source_label
    if accounts:
        merged["account_ids"] = accounts
    if instructions is not None:
        merged["instructions"] = instructions
    merged["updated_at"] = now
    return merged


class StatementFormatStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(statement_formats, id_field="id", engine=engine)

    def all_profiles(self) -> list[dict]:
        return self.all()

    def find_by_fingerprint(self, fingerprint: str) -> dict | None:
        matches = self.query(fingerprint=fingerprint)
        return matches[0] if matches else None

    def _row_by_fingerprint(self, conn, fingerprint: str) -> dict | None:
        for r in conn.execute(select(self._table)):
            if r.payload.get("fingerprint") == fingerprint:
                return dict(r._mapping)
        return None

    def rekey(self, profile_id: str, fingerprint: str) -> dict | None:
        """Move a profile onto a new ``fingerprint``, keeping everything else.

        The hashing rule is allowed to change (it tightened on 2026-08-09 to drop a date drawn
        in control-character glyphs). What must NOT happen is the owner's taught schema,
        accounts and instructions being stranded on an address nothing computes any more —
        that is silent data loss dressed up as "a new source". A caller that finds a profile
        by a legacy key re-keys it here so the NEXT file matches directly.

        A no-op when the target key already belongs to another profile: two rows may not share
        a fingerprint, and the newer one is the caller's own hit.
        """
        clash = next(
            (p for p in self.query(fingerprint=fingerprint) if p.get("id") != profile_id),
            None,
        )
        if clash is not None:
            return None
        # update_by_id patches in place ({**item, **changes}) and returns None when the id
        # doesn't exist — same contract as main's original find-then-None. (Storage plan 4d:
        # this store moved JsonStore -> PayloadTableStore; _load_items/_save_items are gone.)
        return self.update_by_id(profile_id, {"fingerprint": fingerprint, "updated_at": _now()})

    def upsert(
        self,
        fingerprint: str,
        *,
        kind: str,
        schema: dict,
        source_label: str = "",
        origin: str = "inferred",
        account_ids: list[str] | None = None,
        instructions: str | None = None,
    ) -> dict:
        """Create the profile for ``fingerprint``, or update it in place. A changed schema
        bumps ``version`` (the format drifted and was re-learned); a taught/manual origin is
        never silently downgraded back to "inferred" by a later automatic re-learn.
        ``instructions`` is only written when given (None = leave as-is). One base
        transaction — the fingerprint lookup, the insert-or-merge decision, and the write
        all happen under this table's lock."""
        now = _now()
        accounts = _clean_accounts(account_ids)
        with self._use_conn(None) as conn:
            existing_row = self._row_by_fingerprint(conn, fingerprint)
            if existing_row is None:
                row = _new_profile(
                    fingerprint,
                    kind=kind,
                    schema=schema,
                    source_label=source_label,
                    origin=origin,
                    accounts=accounts,
                    instructions=instructions,
                    now=now,
                )
                self._fire_hook(conn, [(row["id"], None)])
                seq = self._next_seq(conn)
                conn.execute(sa_insert(self._table).values(**self._insert_row(seq, row["id"], row)))
                return row
            merged = _merge_profile(
                existing_row["payload"],
                kind=kind,
                schema=schema,
                source_label=source_label,
                origin=origin,
                accounts=accounts,
                instructions=instructions,
                now=now,
            )
            self._fire_hook(conn, [(merged["id"], existing_row)])
            conn.execute(
                sa_update(self._table)
                .where(self._table.c.id == merged["id"])
                .values(payload=merged)
            )
        return merged

    def set_instructions(self, profile_id: str, text: str) -> None:
        """Write the profile's upload-time instructions ('' clears). No-op if missing."""
        self.update_by_id(profile_id, {"instructions": text, "updated_at": _now()})

    def set_match_policy(
        self, profile_id: str, policy: dict | None, instructions_hash: str | None
    ) -> None:
        """Cache this source's extracted match policy TOGETHER with the instructions hash it
        was extracted from — ONE ``payload`` UPDATE inside ONE base transaction, so the pair
        is atomic (security review F7). If this raises, neither field lands and the next
        preview re-derives; the one outcome that must be impossible is a stale
        ``match_policy`` sitting under a fresh ``match_policy_instructions_hash``, which would
        serve a policy the current notes never produced. ``policy=None`` clears both."""
        with self._use_conn(None) as conn:
            existing = self._row(conn, profile_id)
            if existing is None:
                return
            merged = dict(existing["payload"])
            if policy is None:
                merged.pop("match_policy", None)
                merged.pop("match_policy_instructions_hash", None)
            else:
                merged["match_policy"] = policy
                merged["match_policy_instructions_hash"] = instructions_hash
            merged["updated_at"] = _now()
            self._fire_hook(conn, [(profile_id, existing)])
            conn.execute(
                sa_update(self._table).where(self._table.c.id == profile_id).values(payload=merged)
            )

    def set_accounts(self, profile_id: str, account_ids: list[str]) -> None:
        """Replace the remembered chip set (anchor first). An EMPTY list is a no-op, not a
        clear: the Choose step always sends what it has, and a source with no selection yet
        must not wipe what a previous import taught."""
        accounts = _clean_accounts(account_ids)
        if not accounts:
            return
        self.update_by_id(profile_id, {"account_ids": accounts, "updated_at": _now()})

    def accounts_for(self, row: dict) -> list[str]:
        """The remembered chip set of a profile row, healing a row written before this
        field existed (carries only a legacy singular ``account_id``)."""
        ids = list(row.get("account_ids") or [])
        if not ids and row.get("account_id"):
            ids = [row["account_id"]]
        return _clean_accounts(ids)

    def record_use(self, profile_id: str, *, account_ids: list[str] | None = None) -> None:
        with self._use_conn(None) as conn:
            existing = self._row(conn, profile_id)
            if existing is None:
                return
            merged = dict(existing["payload"])
            merged["uses"] = int(merged.get("uses") or 0) + 1
            merged["last_used_at"] = _now()
            accounts = _clean_accounts(account_ids)
            if accounts:
                merged["account_ids"] = accounts
            self._fire_hook(conn, [(profile_id, existing)])
            conn.execute(
                sa_update(self._table).where(self._table.c.id == profile_id).values(payload=merged)
            )
