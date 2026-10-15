// Lucius data-mention types (leaf module — no runtime deps).
//
// A widget's ✦ affordance emits a MentionRef. The composer stages it as a chip; on send it is
// shipped as a STRUCTURED side-channel array on /api/chat (NOT parsed out of free text). The
// backend resolver (services/mentions.py) owns interpretation of `ref`; the frontend only
// constructs it. `label` is presentation-only — it carries NO numbers, so a chip staged earlier
// still resolves to current data and the UI's amount-hiding can never leak through.

export type MentionType =
  | 'net_worth' // ref: '' (singleton)
  | 'holding' // ref: position_id
  | 'instrument' // ref: instrument_id (the security, not a holding)
  | 'account' // ref: account_id
  | 'transaction' // ref: txn_id
  | 'category' // ref: category_id (envelope/spending category)
  | 'budget_month' // ref: 'YYYY-MM'
  | 'target' // ref: target_id
  | 'allocation_slice' // ref: 'asset_class:equity' | 'currency:USD' | 'account:<id>'
  | 'returns' // ref: '<period>' e.g. '1Y' | 'YTD'
  | 'payee' // ref: payee_id
  | 'watchlist' // ref: watchlist_id
  | 'rule' // ref: rule_id
  | 'scheduled_task' // ref: task_id
  | 'automation' // ref: automation_id
  | 'skill' // ref: skill_id (talk-about chip; invoking uses the / verb, not a chip)
  // The registry grew past this union and the union did not follow — it sat at 16 of the
  // backend's 27 registered types, and the gap was only cosmetic while nothing constructed
  // a chip of a missing type. The write-receipt chip does exactly that (an envelope
  // assignment, a lot acquisition, a vest), so these are load-bearing now, not tidiness.
  // Source of truth: backend/services/mentions/registry.py::REGISTRY.
  | 'envelope' // ref: envelope_id (the Plan budget ROW identity — distinct from category)
  | 'commitment' // ref: commitment_id
  | 'notification' // ref: signal_id (the app's notification log — renamed 2026-08-20 from intel_signal)
  | 'alert' // ref: alert_id (a price alert)
  | 'income' // ref: income_event_id
  | 'lot' // ref: lot_id (one dated acquisition tranche)
  | 'disposal' // ref: realized_event_id (the sell side of the lot spine)
  | 'grant' // ref: grant_id (equity comp)
  | 'vest' // ref: vest_event_id
  | 'connector' // ref: connector_id
  | 'report' // ref: report type key
  | 'document' // ref: Documents item id '<kind>-<source id>' (metadata only — never contents)

export interface MentionRef {
  type: MentionType
  ref: string // opaque selector; '' for singletons
  label: string // chip text, e.g. 'AAPL holding'
  // Optional chip decorations — only set for transaction chips (Bug 2 fix).
  // Kept outside MentionType so non-transaction chips are unaffected and
  // the backend resolution authority (ref/type) is never polluted.
  chipDate?: string // e.g. '2024-06-15'
  chipAmt?: string // e.g. '-184.20 SGD' (already sign-prefixed from the sublabel)
  chipAmtNeg?: boolean // true → .chip-amt.neg (red), false → .chip-amt.pos (green)
  // The entity's own display color (#rrggbb or similar) — an account's user-chosen tint,
  // an envelope/category dot — applied to the glyph when set. Validated as a safe CSS
  // color before use; never set for transaction chips (they use direction tint instead).
  color?: string
}

// ── Chip ↔ "[type: id]" serialization ────────────────────────────────────────
// The contenteditable composer embeds each chip as a non-editable atom; on send it serializes
// chip atoms to the bracketed `[type: ref]` token so the user message text stays human-readable.
// The STRUCTURED MentionRef[] array (sent alongside) is the authority for resolution — the
// bracket token is for display + MessageBubble read-only rendering, never re-parsed server-side.

export function serializeMentionRef(m: MentionRef): string {
  // Carry the human label after a `|` so the read-only MessageBubble chip shows the entity NAME,
  // not just the type. Strip the few chars that would break the token grammar. The structured
  // MentionRef[] array stays the resolution authority — the backend never parses this token text.
  const label = m.label.replace(/[[\]|]/g, '').trim()
  return label ? `[${m.type}: ${m.ref}|${label}]` : `[${m.type}: ${m.ref}]`
}

// Factory: constructs a fresh /g regex for one rendering / search pass. Group 1 = type,
// group 2 = ref (possibly empty — singletons use ref='').
// A module-level /g regex is stateful via lastIndex and is NOT safe under React 18 concurrent
// rendering where two MentionText renders may interleave exec() calls on the same object.
// Always use makeMentionTokenRE() per render so no shared lastIndex state leaks.
export function makeMentionTokenRE(): RegExp {
  // group1 = type, group2 = ref (no '|'/']'), group3 = optional human label (display only).
  return /\[([a-z_]+):\s*([^\]|]*)(?:\|([^\]]*))?\]/g
}

// True when a string looks like it leads with / contains an entity-ref token — used to decide
// whether a prefill should activate the chip composer vs the plain textarea (backward compat).
export function hasMentionToken(text: string): boolean {
  return makeMentionTokenRE().test(text)
}
