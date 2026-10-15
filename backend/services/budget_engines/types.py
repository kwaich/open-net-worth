"""The paradigm-neutral ``ViewModel`` every budget engine folds into (RFC
``2026-08-22-pluggable-budget-engines.md``, Decision C: "one level of nesting" —
``groups[].rows[]``, never deeper).

Three invariants apply to every dataclass below, inherited from CLAUDE.md and restated here
because this module is the one place a future community engine reads to know the contract:

* **All amounts are base currency, server-computed.** An engine's ``fold()`` receives base
  amounts from the shared context and never does its own FX — the historical-rate lock is
  upheld upstream (``ledger.txn_base_amount``), exactly as ``reports.py`` already does.
* **Opaque ids always ship with their label.** A ``Row``/``Group``/``Descriptor`` id is never
  rendered or returned bare — ``label``/``name`` rides alongside it on the same object.
* **No caller-chosen projection.** These shapes are server-shaped in full; there is no
  ``fields=``/``include=`` parameter anywhere in this package, on any read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MeasureTone = Literal["positive", "negative", "neutral", "warning"]
HeadlineUnit = Literal["money", "percent", "count"]
RowsShape = Literal["rows", "proportion-bars", "breakdown", "gauges"]
HealthVerdict = Literal["good", "attention", "bad", "unknown"]
EngineKind = Literal["derived", "configured"]
RenderMode = Literal["generic", "custom"]


@dataclass(frozen=True)
class Measure:
    """One labeled figure on a ``Row``/``Group`` — e.g. envelope's "assigned" column, or
    50/30/20's "% of income". Exactly one of ``amount_base``/``percent`` is populated; both
    ``None`` is invalid but not enforced here (engines are trusted first-party code, not
    validated input)."""

    key: str
    label: str
    amount_base: float | None = None
    percent: float | None = None
    tone: MeasureTone = "neutral"


@dataclass(frozen=True)
class Row:
    """One line inside a ``Group`` — an envelope, a bucket-category, a capped category.

    ``drilldown`` is an opaque, engine-defined ref (e.g. the underlying category ids) a
    future UI can use to open the transactions behind this row — the host never interprets
    it, only round-trips it."""

    id: str
    label: str
    color: str | None
    measures: list[Measure]
    status: str
    drilldown: dict | None = None


@dataclass(frozen=True)
class Group:
    """A bucket of rows — envelope's category group, 50/30/20's needs/wants/savings bucket."""

    id: str
    label: str
    rows: list[Row]
    subtotal: list[Measure]
    status: str = "ok"


@dataclass(frozen=True)
class Headline:
    """The one number the paradigm wants the user to look at first — envelope's Ready to
    Assign, 50/30/20's savings rate."""

    label: str
    value: float
    unit: HeadlineUnit
    status: str
    help: str | None = None


@dataclass(frozen=True)
class Health:
    """A one-glance verdict + the reasons behind it. Mirrors the shape of
    ``planning.budget_health()``, generalized past envelope so every engine can answer
    "am I OK" the same way."""

    verdict: HealthVerdict
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Unmapped:
    """An engine's account of unmapped spend for the period — ``category=None``
    ("Uncategorized") spend, plus (for an engine whose OWN state can mark a real category as
    "not part of this fold" — e.g. 50/30/20's ``skip`` mapping) spend in a category the
    user explicitly excluded, or one the engine's config simply never mentions.

    Required on every ``ViewModel`` (never an afterthought): ``category`` is OPTIONAL in
    this ledger and there is no ``uncategorized`` sink, so every paradigm needs an answer for
    spend with no category — envelope's is the synthetic ``__unsorted__`` group
    (``planning.py:1676``); a bare category-less row can never become a ``Row`` itself
    (``Row.id`` is an opaque id, and ``None`` is not one).

    ``amount_base`` is the TOTAL (uncategorized + skipped) — kept for a paradigm like
    envelope that only ever has the uncategorized half and has no notion of "skipped".
    ``uncategorized_amount_base``/``skipped_amount_base`` split that total for a paradigm
    that has both halves and must keep them distinguishable (50/30/20; acceptance D5) —
    they default to 0.0 and are simply unused by an engine with no "skipped" concept.

    ``rows`` (2026-09-25, the envelope-based 50/30/20 redesign) optionally carries the
    INDIVIDUAL rows behind the aggregate — 50/30/20's skip/unsorted-bucketed envelopes,
    each ``Row.status`` set to its own bucket ("skip"/"unsorted"), so Lucius can see every
    envelope's own activity instead of only the rolled-up total. Empty by default; an
    engine with no per-row concept for its unmapped spend (envelope, zero_based) simply
    never populates it."""

    label: str
    amount_base: float
    tone: MeasureTone = "warning"
    uncategorized_amount_base: float = 0.0
    uncategorized_count: int = 0
    skipped_amount_base: float = 0.0
    skipped_count: int = 0
    rows: list[Row] = field(default_factory=list)


@dataclass(frozen=True)
class ViewModel:
    """The paradigm-neutral fold of one engine over one period — what a generic renderer,
    a dashboard widget, or Lucius reads instead of a paradigm-specific payload."""

    engine_id: str
    period: str
    base_currency: str
    headline: Headline
    groups: list[Group]
    rows_shape: RowsShape
    health: Health
    unmapped: Unmapped | None = None


@dataclass(frozen=True)
class Descriptor:
    """An engine's self-description — what Lucius/the UI show BEFORE folding anything.

    ``kind`` distinguishes a derived lens (zero owned state, e.g. 50/30/20) from a
    configured one (owns real state, e.g. zero-based). ``render`` is a declared property,
    not a special case: ``"custom"`` means the engine ships its own panel (zero-based's rich
    Plan-tab UI); ``"generic"`` means it renders through the host's four ``rows_shape``s. A
    community engine may only ever declare ``"generic"``.

    ``description``/``model_note`` split (owner, 2026-08-23 — the bug that forced it: this
    field used to carry BOTH a user-facing subtitle AND a "NOTE FOR MODELS" disambiguation
    paragraph in one string, and the frontend empty state renders ``description`` verbatim
    as body copy — so the model-facing paragraph was landing on screen, unreadable and
    naming internal verb/engine ids). ``description`` is now STRICTLY user-facing — safe to
    render anywhere, never a model instruction. ``model_note`` carries the SAME disambiguation
    substance for Lucius alone (zero_based's note says popular "envelope budgeting" IS
    zero-based here) — wired into the
    ``list_budget_engines`` AI tool's own output, deliberately NOT into
    ``services.budget_engines.service.list_engines()`` (the REST/frontend surface), so a
    frontend consumer cannot render it even by accident: the field is simply never on that
    wire."""

    id: str
    name: str
    description: str
    question: str
    kind: EngineKind
    render: RenderMode = "generic"
    model_note: str | None = None


@dataclass(frozen=True)
class Verb:
    """One declared write operation an engine exposes, so Lucius can operate any engine with
    no per-engine tool code (RFC Decision F — mirrors the existing
    ``apply_budget_change`` "one tool, many op types" precedent). ``params`` maps each
    param name to a short human-readable hint (type/requiredness), not a full JSON
    schema — Phase 3 wires ``BudgetEngine.apply_verb`` to actually execute these; Phase 0
    only declares them."""

    id: str
    label: str
    params: dict[str, str] = field(default_factory=dict)
