"""Budget + rollover schemas (P2-BU1) + Planning / envelope-budgeting schemas (PLAN).

``models/`` is a leaf layer (stdlib + pydantic + ``backend.core`` only). These types
describe the *shape* of ``profile.monthly_budgets`` / ``profile.budget_config`` so the
PUT body + the normalizer output in ``services.budgets`` are typed — no logic here.

A category budget moved from a bare number (``"food": 800``) to an object
(``"food": {"limit": 800, "rollover": false}``); the service normalizer accepts both.

The Planning (YNAB envelope-budgeting) primitives below are NEW and distinct from the
legacy budgets above and from the deadline ``targets.json`` ("save X by date Y") store.
All planning math (rollover/available/ready-to-assign/needed-this-month) lives in
``services.planning``; none here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import field_validator

from backend.models.base import ONBaseModel, _blank_to_none


class CategoryBudget(ONBaseModel):
    """A per-category monthly cap, optionally with rollover (carryover) enabled."""

    limit: float = 0.0
    rollover: bool = False


class BudgetConfig(ONBaseModel):
    """Top-level budget config. ``rollover_start`` (YYYY-MM) anchors the carryover fold
    so we never sum infinite / pre-adoption history."""

    rollover_start: Optional[str] = None


# ── Planning (envelope budgeting) ───────────────────────────────────────────────

# MVP target types (Phase-2 adds weekly|yearly|set_aside + repeat/snooze).
TARGET_TYPES = ("monthly", "by_date")


class CategoryTarget(ONBaseModel):
    """A per-category funding target — stored shape (``profile.category_targets[cat]``)."""

    type: str = "monthly"  # monthly | by_date
    amount: float = 0.0
    date: Optional[str] = None  # ISO date — required for by_date

    _blank_date = field_validator("date", mode="before")(_blank_to_none)


class RolloverStartBody(ONBaseModel):
    """PUT /api/planning/budget-config body — move (or clear) the carry-walk floor.

    ``rollover_start`` is REQUIRED but nullable: ``null`` (or ``""``) resumes carrying all
    history; a YYYY-MM month starts the budget fresh there. Deliberately the only settable
    field — ``pre_budget_baseline`` is a cutover capture, not a user control."""

    rollover_start: Optional[str]

    _blank_start = field_validator("rollover_start", mode="before")(_blank_to_none)


class FreshStartBody(ONBaseModel):
    """POST /api/planning/fresh-start body — the month to start fresh from. Validated by
    ``budgets._validate_anchor_month``, the same rule the single writer applies."""

    ym: str


class FreshStartUndoBody(ONBaseModel):
    """POST /api/planning/fresh-start/undo body — the one-shot ``token`` the fresh-start response
    returned, and NOTHING else.

    It deliberately carries no month, no floor and no assignment map. An earlier revision
    round-tripped the whole snapshot through the client, which made this route an unauthenticated
    bulk assign: it replayed caller-supplied amounts with the negative guard relaxed (a forged
    ``{assignments: {env: -5000}}`` moved displayed Ready to Assign) and cleared the carry floor
    through a defaulted null. The before-image now lives server-side; the token only names it."""

    token: str


class AssignBody(ONBaseModel):
    """POST /api/planning/assign body — set Assigned for one (month, envelope)."""

    month: str  # YYYY-MM
    envelope_id: str
    amount: float


class MoveMoneyBody(ONBaseModel):
    """POST /api/planning/move body — the YNAB move between two envelopes in one month.
    Either end may be the ``__rta`` sentinel (Ready to Assign), never both."""

    month: str  # YYYY-MM
    from_envelope_id: str
    to_envelope_id: str
    amount: float


class TargetBody(ONBaseModel):
    """PUT /api/planning/targets/{envelope_id} body — set/clear a budget row's target.

    ``type=None`` (or amount<=0) clears the target. ``snooze_until`` (YYYY-MM) suspends an
    existing target's funding through that month; sending it with no ``type`` snoozes the
    saved target without re-specifying it (a blank ``snooze_until`` un-snoozes).
    ``needs_whole_amount=True`` (default) = "set aside X" (measures contributions/Assigned);
    ``needs_whole_amount=False`` = "refill up to X" / "have a balance of X" (measures Available).

    Target deadlines (handoff delta #13, display-only — no effect on the math except
    ``due_weekday``, which picks the weekly multiplier's weekday): ``due_weekday`` (0-6,
    Monday=0) is valid on a ``weekly`` target only; ``due_day`` (1-28, or -1 = last day of the
    month) is valid on a ``monthly`` target only; ``date`` is valid on ``yearly`` (the annual
    deadline) or ``by_date`` (required there). A field paired with the wrong type is refused."""

    type: Optional[str] = None  # monthly | weekly | yearly | by_date | None (clear)
    amount: float = 0.0
    date: Optional[str] = None
    due_weekday: Optional[int] = None  # 0-6 (Monday=0) — weekly targets only
    due_day: Optional[int] = None  # 1-28, or -1 = last day — monthly targets only
    snooze_until: Optional[str] = None  # YYYY-MM; suspend funding through this month
    needs_whole_amount: bool = True

    _blank = field_validator("type", "date", "snooze_until", mode="before")(_blank_to_none)


class GroupCreateBody(ONBaseModel):
    """POST /api/planning/groups body."""

    name: str


class GroupRenameBody(ONBaseModel):
    """PATCH /api/planning/groups/{id} body — rename and/or collapse."""

    name: Optional[str] = None
    collapsed: Optional[bool] = None

    _blank = field_validator("name", mode="before")(_blank_to_none)


class GroupReorderBody(ONBaseModel):
    """POST /api/planning/groups/reorder body — full ordered list of group ids."""

    order: List[str]


class MoveCategoryBody(ONBaseModel):
    """POST /api/planning/groups/move-category body. ``category`` is the envelope id of the
    spending row to move (or a category linked to one); an income-kind category is refused —
    income belongs to no group."""

    category: str
    group_id: str  # destination group id
    index: Optional[int] = None  # insert position within the destination group (None = append)


class ReorderCategoriesBody(ONBaseModel):
    """POST /api/planning/groups/{id}/reorder-categories body — full ordered member list."""

    categories: List[str]


class AutoAssignPreviewBody(ONBaseModel):
    """POST /api/planning/auto-assign-preview body — compute what auto-assign would do."""

    month: str  # YYYY-MM
    mode: str  # underfunded | assigned_last_month | spent_last_month | average_assigned |
    #            average_spent | reset_assigned | cover_overspending


class NoteBody(ONBaseModel):
    """PUT /api/planning/categories/{envelope_id}/note body — set or clear a budget row's note."""

    note: str = ""


# ── Envelope lifecycle (P4 merge/split/rename, RFC envelope-not-category.md; create, RFC
#    envelope-category-full-split.md) ──────────────────────────────────────────────────


class EnvelopeCreateBody(ONBaseModel):
    """POST /api/planning/envelopes body — create a standalone envelope with NO categories.

    ``color`` (handoff delta #5 — envelope owns its display colour) is optional and
    hex-validated (6-digit, like ``services.categories.set_category_color``); omit for the
    default ""."""

    name: str
    group_id: Optional[str] = None
    color: Optional[str] = None


class EnvelopeMergeBody(ONBaseModel):
    """POST /api/planning/envelopes/merge body — fold ``source`` into ``target``."""

    source_envelope_id: str
    target_envelope_id: str


class EnvelopeSplitBody(ONBaseModel):
    """POST /api/planning/envelopes/{id}/split body — detach one category into a new envelope."""

    category_id: str


class EnvelopeRenameBody(ONBaseModel):
    """PATCH /api/planning/envelopes/{id} body — rename and/or recolor the envelope directly
    (handoff delta #5 widens this from name-only to {name?, color?} — at least one is
    required). ``color`` is hex-validated (6-digit) like
    ``services.categories.set_category_color``."""

    name: Optional[str] = None
    color: Optional[str] = None


class EnvelopeAddCategoryBody(ONBaseModel):
    """POST /api/planning/envelopes/{id}/add-category body — link an UNLINKED category to
    this envelope (contract addendum, RFC envelope-category-full-split.md)."""

    category_id: str


class EnvelopeDeleteUndoBody(ONBaseModel):
    """POST /api/planning/envelopes/undo-delete body — the one-shot ``token`` the DELETE
    response returned, and NOTHING else (mirrors ``FreshStartUndoBody``'s exact reasoning: the
    before-image lives server-side under the token; a client-supplied snapshot would make this
    route an unauthenticated bulk write wearing an "undo" label)."""

    token: str


# ── Pluggable budget engines — the write surface (RFC
#    2026-08-22-pluggable-budget-engines.md, Decisions A/E/F, Phase 2/3) ───────────────


class HomeEngineBody(ONBaseModel):
    """POST /api/planning/home-engine body — switch the book's home budget-engine lens.
    Decision A: switching never touches any OTHER engine's own state; validated against
    the real ``ENGINES`` registry by ``services.budget_engines.base.set_home_engine_id``
    (404s naming the real engines for an unknown id)."""

    engine_id: str


class ApplyVerbBody(ONBaseModel):
    """POST /api/planning/verb body — execute one declared write verb on any budget
    engine: ``{engine_id, verb_id, params}``. Mirrors ``apply_budget_change``'s "one
    route, many op types" shape, generalized across engines (Decision F) — ``params`` is
    whatever that ``verb_id`` declares (see ``BudgetEngine.verbs``); an unknown engine or
    verb 404s, naming the real ones either way."""

    engine_id: str
    verb_id: str
    params: Dict[str, Any] = {}
