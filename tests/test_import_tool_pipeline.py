"""The CHAT import path — `import_transactions` — through the ONE decision pipeline.

`test_import_pipeline_unification.py` pins the SERVICE layer (`decision_preview` /
`decision_commit`). This file pins the AI TOOL that chat actually calls, which is where the
drift lived: only PDF and unmapped CSV/XLSX reached the decision pipeline, while the
structured formats (OFX/QIF/MT940/CAMT) and hand-mapped CSV/XLSX fell through to the legacy
`commit_import` wrappers — no statement record, no import batch (so no rollback), no match
bands, and flagged rows imported regardless. Acceptance SI-18 claimed chat/UI parity; it was
true only for the PDF branch.

Also pins the two-call contract: a commit naming a statement whose parse is cached NEVER
re-parses (on a PDF that was a second vision/LLM bill, and it discarded every per-row verdict
the preview computed), and a one-shot commit parses exactly once.

Zero LLM anywhere — the seam is left unstubbed on purpose so an accidental model call fails
loudly.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.core import llm_seam
from backend.services import ledger as ledger_service


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    import backend.db.statement_store as stmt_mod
    from backend.core import fx

    # PayeeStore is DB-backed now (plan 4d task 2) — db_isolated redirects the process-wide
    # singleton, so payee_registry._store resolves against this fixture's isolated database
    # with no per-instance path-sync needed.
    monkeypatch.setattr(stmt_mod, "DATA_DIR", tmp_path)
    files_dir = tmp_path / "statements"
    files_dir.mkdir()
    # Fix 3 (security review round 2, consolidate-derived-data-storage): every consumer of
    # the statements blob directory now reads through
    # backend.core.paths.statements_files_dir(), a bare accessor resolved at call time —
    # there is exactly ONE binding left to patch, not one per consumer module.
    import backend.core.paths as core_paths_mod

    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", files_dir)
    # _resolve_bytes reads the statement's bytes back through backend.paths.DATA_DIR, a
    # DIFFERENT module reference from the store's own — both must point at tmp_path or a
    # statement_id source reads from the real data dir.
    import backend.paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(ledger_service, "_fx_load_rates", lambda: {"SGD": 1.0, "EUR": 1.5})
    monkeypatch.setattr(fx, "_load_rates", lambda: {"SGD": 1.0, "EUR": 1.5})
    monkeypatch.setattr(llm_seam, "_complete", None)
    ledger_service.accounts.insert(
        {"id": "acc", "name": "Checking", "kind": "asset", "currency": "SGD"}
    )
    ledger_service.accounts.insert(
        {"id": "eur", "name": "EUR Wallet", "kind": "asset", "currency": "EUR"}
    )


_OFX = b"""OFXHEADER:100
<OFX>
<CURDEF>SGD
<BANKACCTFROM><ACCTID>ACC-1</ACCTID></BANKACCTFROM>
<BANKTRANLIST><DTSTART>20260201<DTEND>20260228
<STMTTRN><DTPOSTED>20260202<TRNAMT>-4.50<FITID>F1<NAME>Coffee</STMTTRN>
<STMTTRN><DTPOSTED>20260203<TRNAMT>2500.00<FITID>F2<NAME>Paycheck</STMTTRN>
</BANKTRANLIST>
</OFX>
"""

_QIF = b"""!Type:Bank
D02/02/2026
T-4.50
PCoffee
^
D02/03/2026
T2500.00
PPaycheck
^
"""

_MT940 = b""":20:STMT001
:25:DE1234567890
:28C:1/1
:60F:C260201EUR1000,00
:61:2602020202D4,50NTRFNONREF//ref1
:86:COFFEE SHOP BERLIN
:61:2602030203C2500,00NTRFNONREF//ref2
:86:SALARY FEBRUARY
:62F:C260228EUR3495,50
"""

_CAMT = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
 <BkToCstmrStmt><Stmt>
  <Acct><Id><IBAN>DE00123456780000000000</IBAN></Id></Acct>
  <FrToDt><FrDtTm>2026-02-01T00:00:00</FrDtTm><ToDtTm>2026-02-28T23:59:59</ToDtTm></FrToDt>
  <Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp><Amt Ccy="EUR">1000.00</Amt><CdtDbtInd>CRDT</CdtDbtInd></Bal>
  <Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy="EUR">3495.50</Amt><CdtDbtInd>CRDT</CdtDbtInd></Bal>
  <Ntry><Amt Ccy="EUR">4.50</Amt><CdtDbtInd>DBIT</CdtDbtInd>
   <BookgDt><Dt>2026-02-02</Dt></BookgDt>
   <NtryDtls><TxDtls><Refs><AcctSvcrRef>R1</AcctSvcrRef></Refs>
    <RmtInf><Ustrd>COFFEE BERLIN</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
  <Ntry><Amt Ccy="EUR">2500.00</Amt><CdtDbtInd>CRDT</CdtDbtInd>
   <BookgDt><Dt>2026-02-03</Dt></BookgDt>
   <NtryDtls><TxDtls><Refs><AcctSvcrRef>R2</AcctSvcrRef></Refs>
    <RmtInf><Ustrd>SALARY</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
 </Stmt></BkToCstmrStmt>
</Document>
"""

_CSV = b"""Date,Memo,Amount,Category
2026-02-02,Coffee,-4.50,Transport
2026-02-03,Paycheck,2500.00,
"""
_MAPPING = {"date": "Date", "description": "Memo", "amount": "Amount", "category": "Category"}


def _call(**kwargs) -> dict:
    """Invoke the real AI tool handler and decode its JSON reply."""
    from backend.ai.tools.imports import import_transactions

    payload, _ = asyncio.run(import_transactions(kwargs))
    return json.loads(payload)


def _live_rows():
    return [t for t in ledger_service.transactions.all() if not t.get("deleted")]


@pytest.fixture
def no_legacy_wrappers(monkeypatch):
    """Any call into a legacy per-format wrapper is a routing regression — fail loudly."""
    from backend.services import (
        import_camt,
        import_csv,
        import_mt940,
        import_ofx,
        import_qif,
        import_xlsx,
    )

    def boom(*_a, **_k):
        raise AssertionError("legacy commit_import/preview_import wrapper was reached")

    for mod in (import_csv, import_xlsx, import_ofx, import_qif, import_mt940, import_camt):
        monkeypatch.setattr(mod, "commit_import", boom)
        monkeypatch.setattr(mod, "preview_import", boom)


# ── FINDING 2: every format reaches the decision pipeline ──────────────────────────


@pytest.mark.parametrize(
    "content,fmt,account_id,mapping",
    [
        (_OFX, "ofx", "acc", None),
        (_QIF, "qif", "acc", None),
        (_MT940, "mt940", "eur", None),
        (_CAMT, "camt", "eur", None),
        # Unmapped CSV is deliberately absent: it needs LLM schema inference, and it was
        # already on the decision pipeline before this fix — it is not what regressed.
        (_CSV, "csv", "acc", _MAPPING),  # hand-mapped — used to fall through to import_csv
    ],
)
def test_every_format_previews_through_the_decision_pipeline(
    content, fmt, account_id, mapping, no_legacy_wrappers
):
    out = _call(
        content=content.decode("utf-8"),
        format=fmt,
        account_id=account_id,
        **({"mapping": mapping} if mapping else {}),
    )
    assert out["ok"] is True, out
    assert out["committed"] is False
    # A statement_id only ever comes from the decision pipeline — the legacy preview
    # wrappers returned a bare PreviewResult with no statement record at all.
    assert out["statement_id"], out
    assert out["result"]["statement_id"] == out["statement_id"]
    rows = out["result"]["rows"]
    assert rows, out
    # The decision payload's per-row review contract — the legacy PreviewResult had none of
    # this: no per-row index the model can decide on, no needs-eyes verdict, no match flags.
    assert all("index" in r for r in rows), rows
    assert all("needs_eyes" in r for r in rows), rows
    assert all("flags" in r for r in rows), rows
    assert all("duplicate" in r for r in rows), rows
    assert "reconcile" in out["result"]
    assert "needs_eyes" in out["result"]
    assert "ledger_check" in out["result"]
    assert not _live_rows(), "preview must write nothing"


def test_the_tool_gets_the_same_ledger_check_fields_the_ui_does(no_legacy_wrappers):
    """Owner directive, 2026-07-31: "please handle these on the backend, not client so the ai
    tool can get the same information". The origin / near-cut-off / description projection is
    computed in ``ledger_diff``, so it rides the SAME ``ledger_check`` payload the UI reads —
    this pins that ``import_transactions`` passes it through instead of projecting it away."""
    ledger_service.transactions.insert(
        {
            "id": "mine-1",
            "date": "2026-02-15",
            "kind": "expense",
            "from_account_id": "acc",
            "from_amount": 9.0,
            "from_currency": "SGD",
            "description": "HAND ENTERED LUNCH",
            "source": "manual",
        }
    )
    out = _call(content=_OFX.decode("utf-8"), format="ofx", account_id="acc")
    rows = (out["result"]["ledger_check"] or {}).get("rows") or []
    entry = next(r for r in rows if r["id"] == "mine-1")
    assert entry["note"] == "HAND ENTERED LUNCH"
    # Both KEYS are always present, so the model never has to guess whether an absence means
    # "not on a statement / not near an edge" or "the server didn't say". A hand-entered,
    # mid-window row is None for both.
    assert "on_statements" in entry and entry["on_statements"] is None
    assert "near_cutoff" in entry and entry["near_cutoff"] is None


@pytest.mark.parametrize(
    "content,fmt,account_id",
    [(_OFX, "ofx", "acc"), (_QIF, "qif", "acc"), (_MT940, "mt940", "eur"), (_CAMT, "camt", "eur")],
)
def test_structured_commit_files_a_statement_and_a_rollbackable_batch(
    content, fmt, account_id, no_legacy_wrappers
):
    """The legacy wrappers booked with no batch — nothing to roll back. The pipeline files
    both, so a chat import is as undoable as a UI one."""
    out = _call(content=content.decode("utf-8"), format=fmt, account_id=account_id, commit=True)
    assert out["ok"] is True and out["committed"] is True, out
    assert out["statement_id"], out
    assert len(_live_rows()) == 2, out
    # The legacy commit wrappers returned no batch id at all — nothing to roll back.
    batch_id = out["result"]["batch_id"]
    assert batch_id, out
    assert len(ledger_service.import_batches.all()) == 1

    from backend.ai.tools.statements import rollback_import_batch

    asyncio.run(rollback_import_batch({"batch_id": batch_id, "confirm": True}))
    assert not _live_rows(), "rollback must un-book every row the chat import created"


def test_hand_mapped_csv_teaches_the_profile_so_the_next_import_needs_no_mapping(
    no_legacy_wrappers,
):
    """The mapping now threads into decision_preview's TEACH flow instead of import_csv."""
    first = _call(content=_CSV.decode("utf-8"), format="csv", account_id="acc", mapping=_MAPPING)
    assert first["ok"] is True and first["statement_id"]

    from backend.services.statements import profiles

    assert profiles.find_profile is not None  # sanity: the profile module is the teach sink
    # Same layout, DIFFERENT bytes, NO mapping — the learned profile must carry it.
    other = b"Date,Memo,Amount,Category\n2026-03-09,Tea,-2.25,Transport\n"
    second = _call(content=other.decode("utf-8"), format="csv", account_id="acc")
    assert second["ok"] is True, second
    assert second["result"]["rows"], second


# ── FINDING 3: the two-call contract is real — a commit never re-parses ────────────


@pytest.fixture
def count_parses(monkeypatch):
    """Count real parses. decision_preview reuses a cached parse; a commit must not parse."""
    import backend.services.statements.decisions as dec_mod

    calls = {"n": 0}
    real = dec_mod.parse_statement_bytes

    def counted(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(dec_mod, "parse_statement_bytes", counted)
    return calls


def test_commit_with_statement_id_and_no_decisions_reads_the_cached_parse(count_parses):
    """The DOCUMENTED happy path: preview → show counts → user says yes → commit.

    This used to fall through to a blind re-parse (a second vision/LLM bill on a PDF) and
    threw away every per-row verdict the preview had computed."""
    prev = _call(content=_OFX.decode("utf-8"), format="ofx", account_id="acc")
    assert count_parses["n"] == 1
    sid = prev["statement_id"]

    out = _call(statement_id=sid, account_id="acc", commit=True)
    assert out["ok"] is True and out["committed"] is True, out
    assert out["statement_id"] == sid
    assert count_parses["n"] == 1, "commit re-parsed — the cached parse was discarded"
    assert len(_live_rows()) == 2


def test_one_shot_commit_parses_exactly_once(count_parses):
    """commit=true with raw bytes and no prior preview still parses ONCE — it previews
    internally (caching the parse) and commits against that same cache."""
    out = _call(content=_OFX.decode("utf-8"), format="ofx", account_id="acc", commit=True)
    assert out["ok"] is True and out["committed"] is True, out
    assert count_parses["n"] == 1, "one-shot commit parsed more than once"
    assert out["statement_id"], "a one-shot commit must still file a statement record"
    assert len(_live_rows()) == 2


def test_preview_commit_roundtrip_with_explicit_decisions(count_parses):
    """Full chat round trip: preview → read the rows → decide per row → commit."""
    prev = _call(content=_OFX.decode("utf-8"), format="ofx", account_id="acc")
    rows = prev["result"]["rows"]
    assert len(rows) == 2

    out = _call(
        statement_id=prev["statement_id"],
        account_id="acc",
        commit=True,
        decisions=[{"row": rows[0]["index"], "action": "skip"}],
        default_action="create",
    )
    assert out["ok"] is True and out["committed"] is True, out
    assert count_parses["n"] == 1
    assert len(_live_rows()) == 1, "the skipped row must not book"


def test_approved_low_confidence_row_accepts_preview_index_in_chat_decision():
    from backend.db.statement_store import StatementStore

    prev = _call(content=_OFX.decode("utf-8"), format="ofx", account_id="acc")
    sid = prev["statement_id"]
    record = StatementStore().find_by_id(sid)
    rows = record["import_rows"]
    rows[0]["confidence"] = 0.5
    StatementStore().update_by_id(sid, {"import_rows": rows})

    out = _call(
        statement_id=sid,
        account_id="acc",
        commit=True,
        decisions=[{"index": prev["result"]["rows"][0]["index"], "action": "create"}],
    )

    assert out["ok"] is True, out
    assert len(_live_rows()) == 2
    assert out["result"]["skipped"] == 0


@pytest.mark.parametrize(
    "decision",
    [
        {"index": 0, "row": 1, "action": "create"},
        {"index": 99, "action": "create"},
        {"action": "create"},
    ],
)
def test_malformed_chat_decision_is_refused_before_any_import(decision):
    prev = _call(content=_OFX.decode("utf-8"), format="ofx", account_id="acc")

    out = _call(
        statement_id=prev["statement_id"],
        account_id="acc",
        commit=True,
        decisions=[decision],
    )

    assert out["ok"] is False
    assert "decision" in out["error"].lower()
    assert not _live_rows()


def test_re_commit_of_the_same_statement_dedups_to_zero(count_parses):
    """Content-addressed external refs: committing twice never doubles the ledger."""
    prev = _call(content=_OFX.decode("utf-8"), format="ofx", account_id="acc")
    sid = prev["statement_id"]
    _call(statement_id=sid, account_id="acc", commit=True)
    assert len(_live_rows()) == 2
    _call(statement_id=sid, account_id="acc", commit=True)
    assert len(_live_rows()) == 2, "a re-commit duplicated rows"
    assert count_parses["n"] == 1


def test_decisions_without_commit_is_refused():
    out = _call(statement_id="stmt_x", account_id="acc", decisions=[{"row": 0, "action": "skip"}])
    assert out["ok"] is False
    assert "commit" in out["error"]


def test_second_byte_source_alongside_statement_id_is_refused_not_ignored():
    """The cached-commit short-circuit skips _resolve_bytes' own exclusivity check, so the
    check is hoisted — otherwise the extra source would be silently dropped."""
    prev = _call(content=_OFX.decode("utf-8"), format="ofx", account_id="acc")
    out = _call(
        statement_id=prev["statement_id"],
        content=_QIF.decode("utf-8"),
        account_id="acc",
        commit=True,
    )
    assert out["ok"] is False
    assert "exactly ONE" in out["error"]
    assert not _live_rows()


def test_explicit_mapping_on_commit_busts_the_cached_parse(count_parses):
    """A hand-mapping is the caller overriding HOW the file was read — it must re-read, not
    silently reuse the inferred parse the short-circuit would have returned."""
    prev = _call(content=_CSV.decode("utf-8"), format="csv", account_id="acc", mapping=_MAPPING)
    assert count_parses["n"] == 1
    out = _call(statement_id=prev["statement_id"], account_id="acc", commit=True, mapping=_MAPPING)
    assert out["ok"] is True and out["committed"] is True, out
    assert count_parses["n"] == 2, "an explicit mapping must force a re-read"
    assert len(_live_rows()) == 2


# ── FINDING 1: the pipeline entrance is reachable from where the model is told to go ──
# The skill-allowlist variant of this test was removed with _ALLOWED_TOOLS: a skill no
# longer narrows a turn's tool scope (skills are instructions, not tool policy), so
# reachability now depends only on the core tool offer, checked below.


def test_import_transactions_is_in_the_core_tool_offer():
    from backend.ai.tools._groups import CORE_TOOL_NAMES

    assert "import_transactions" in CORE_TOOL_NAMES


def test_statement_guidance_points_at_the_pipeline_not_hand_parsing():
    """The always-offered add_transactions description must not send statements to a
    hand-parsing playbook as the FIRST move."""
    from backend.ai.tools.registry import build_tool_schemas

    desc = next(t for t in build_tool_schemas() if t["name"] == "add_transactions")["description"]
    assert "import_transactions" in desc
    head = desc[desc.index("STATEMENT IMPORT") :]
    # Was an ordering check (import_transactions BEFORE activate_skill). c09ca799 removed the
    # activate_skill('statement-import') mention outright — the skill's allowlist did not contain
    # import_transactions, so instructing the model to load it first narrowed the turn to a
    # tool set where hand-parsing into add_transactions was the only reachable option (24 of
    # 289 sessions hand-booked 1,597 rows that way). The ordering assertion then raised
    # ValueError on the missing substring. Absent is strictly stronger than "mentioned
    # second", so assert the end state this test is really about.
    assert "activate_skill" not in head, "the statement block must not route through a skill first"
    assert "do NOT hand-parse" in head


# ── FINDING 4: the skill drives the pipeline, it does not re-do the server's job ──


def test_skill_body_drives_the_pipeline_instead_of_hand_parsing():
    """Every rule the old playbook asked the model to apply is now server-side: skip rules
    (guards), direction re-derivation (guards), leg mapping (importer._row_to_draft), FX
    pairing (decisions._pair_fx_transfers), categorization (enrich), external refs
    (importer.external_ref), matching (decisions._apply_batch_match). Rows come back uncapped,
    so the 20-25-row batching instruction described a limit that no longer exists."""
    from backend.services.statement_import_bundle import _BODY

    for stale in (
        "split into sequential calls",
        "import_session_id",
        "import_description",
        "direction=debit",
        "Transfer leg mapping",
        "to_account_id` to the destination",
    ):
        assert stale not in _BODY, f"manual-parse playbook text survived: {stale!r}"
    # The pipeline flow it should teach instead.
    for needed in (
        "import_transactions",
        "commit:false",
        "statement_id",
        "needs_eyes",
        "reconcile",
        "decisions",
        "batch_id",
    ):
        assert needed in _BODY, f"pipeline flow missing: {needed!r}"


def test_skill_description_does_not_gate_importing_on_loading_the_skill():
    """The description is what the model reads when deciding to auto-invoke. While it said
    'call this BEFORE parsing any statement', loading the skill — and thus being narrowed to
    its allowlist — was the model's first move on every import."""
    from backend.services.statement_import_bundle import _DESCRIPTION

    assert "before parsing" not in _DESCRIPTION.lower()
    assert "optional" in _DESCRIPTION.lower()
