// What each asset category asks, and how its steps and sections are worded. Shared by the
// create wizard and the asset page's section editors, so a create step and the editor built
// from it can never disagree about a category.

/** A guided detail: its label (also the saved fact's label), a stable key, a placeholder. */
export type FactPrompt = [label: string, key: string, placeholder: string]

export const COMMON_FACTS: FactPrompt[] = [
  ['Where it’s kept', 'kept', 'Home safe / bank vault / on the wall'],
]

// Per-category prompts. Each saves as a fact on the asset. The last two rows are the ones an
// insurer or executor asks first, so every category gets them.
export const CATEGORY_FACTS: Record<string, FactPrompt[]> = {
  property: [
    ['Address or unit', 'address', '#14-02, 88 Marina Rise'],
    ['Tenure', 'tenure', 'Freehold / 99-yr'],
    ['Floor area', 'area', '1,184 sqft'],
    ['Tenanted', 'tenant', 'No / S$3,800 pm'],
  ],
  vehicle: [
    ['Plate number', 'plate', 'SLA 4417 X'],
    ['COE expires', 'coe', 'Feb 2032'],
    ['Mileage', 'odo', '41,200 km'],
  ],
  watch: [
    ...COMMON_FACTS,
    ['Brand & reference', 'ref', 'Rolex 124060'],
    ['Serial number', 'serial', 'optional'],
    ['Box & papers', 'papers', 'Yes / No'],
  ],
  art: [
    ...COMMON_FACTS,
    ['Artist', 'artist', 'Tan Swie Hian'],
    ['Medium & year', 'medium', 'Ink on paper, 1998'],
    ['Provenance', 'prov', 'Gallery invoice 2021'],
  ],
  metal: [
    ...COMMON_FACTS,
    ['Weight', 'weight', '300 g'],
    ['Purity', 'purity', '999.9'],
    ['Form & serial', 'form', '3 × 100g PAMP bars'],
  ],
  policy: [
    ['Insurer', 'insurer', 'Great Eastern'],
    ['Policy number', 'polno', 'GE-4471-2093'],
    ['Sum assured', 'sumass', 'S$60,000'],
    ['Premium', 'premium', 'S$3,600 / yr'],
  ],
  other: [...COMMON_FACTS, ['Detail', 'detail', 'optional']],
}

/** A category's guided detail prompts — its own, else just where it's kept. */
export function guidedFactsFor(category: string | null): FactPrompt[] {
  return category ? (CATEGORY_FACTS[category] ?? COMMON_FACTS) : COMMON_FACTS
}

const NAME_PLACEHOLDERS: Record<string, string> = {
  property: 'e.g. 88 Marina Rise',
  vehicle: 'e.g. 2022 Volvo XC60',
  watch: 'e.g. Rolex Submariner',
  art: 'e.g. Tan Swie Hian painting',
  metal: 'e.g. Gold bars',
  policy: 'e.g. Great Eastern Endowment Plan',
  other: 'e.g. Vintage camera collection',
}

export function namePlaceholder(category: string): string {
  return NAME_PLACEHOLDERS[category] ?? 'Enter a recognizable name'
}

function isCustom(category: string): boolean {
  return category.startsWith('c_')
}

/** Portable things get a "Where it's kept" — never a building, a car or a policy. */
export function canHaveStorageLocation(category: string): boolean {
  return ['watch', 'art', 'metal', 'other'].includes(category) || isCustom(category)
}

/** Only things a loan is typically secured against ask "Any loan against it?". */
export function canHaveLoan(category: string): boolean {
  return category === 'property' || category === 'vehicle' || isCustom(category)
}

/** "Is this insured" means nothing for a policy — it IS the insurance. */
export function showsInsured(category: string): boolean {
  return category !== 'policy'
}

export function valueLabel(category: string): string {
  return category === 'policy' ? 'Cash value today' : 'Worth today'
}

export function valueHelp(category: string): string {
  return category === 'policy'
    ? 'The surrender value on your latest statement — not the sum assured.'
    : 'Your best estimate is fine'
}

// A value's note is usually where the figure came from — the usual source, by category.
const NOTE_PLACEHOLDERS: Record<string, string> = {
  property: 'e.g. Bank valuation',
  vehicle: 'e.g. Dealer quote',
  watch: 'e.g. Chrono24 median',
  art: 'e.g. Auction estimate',
  metal: 'e.g. Spot price',
  policy: 'e.g. Annual statement',
}

export function notePlaceholder(category: string): string {
  return NOTE_PLACEHOLDERS[category] ?? 'e.g. Where the figure came from'
}

// An asset's attachments are the paperwork that goes with it — not evidence for its value.
const ATTACHMENT_HINTS: Record<string, string> = {
  property: 'the purchase agreement, title deed, receipts and photos',
  vehicle: 'registration, insurance, service records and photos',
  watch: 'box and papers, certificates, the receipt and photos',
  art: 'certificate of authenticity, invoice, provenance and photos',
  metal: 'purchase invoices, assay certificates and storage statements',
  policy: 'the policy contract and each annual statement',
}

/** The empty Documents tab's line: the documents this kind of asset usually comes with. */
export function attachmentsHint(category: string): string {
  const kinds = ATTACHMENT_HINTS[category] ?? 'contracts, receipts, warranties and photos'
  return `Keep its documents here — ${kinds}.`
}

export interface PurchaseCopy {
  /** The purchase step's question — its subtitle reads "Step 4 of 4 · {question}". */
  question: string
  intro: string
  costLabel: string
  dateLabel: string
  /** The asset page's Purchase group: its title, and its "+" chip while nothing is recorded. */
  cardTitle: string
  chipLabel: string
  /** The step's intro when an existing asset's Purchase is edited — no "Optional". */
  editIntro: string
  /** The value chart's dashed line at `cost`. */
  chartLabel: string
}

const PURCHASE: PurchaseCopy = {
  question: 'How much did you pay?',
  intro: 'Optional — adds your gain and yearly return.',
  costLabel: 'Bought for',
  dateLabel: 'Bought on',
  cardTitle: 'Purchase',
  chipLabel: 'What you paid',
  editIntro: 'Adds your gain and yearly return.',
  chartLabel: 'Bought for',
}

// A policy's `cost` is premiums paid so far and its `cost_date` the day it started — there is
// no purchase point, so none of this copy mentions one.
const PREMIUMS: PurchaseCopy = {
  question: 'How much have you paid in?',
  intro: "Optional. Shows whether the cash value is above what you've paid.",
  costLabel: 'Premiums paid so far',
  dateLabel: 'Policy started on',
  cardTitle: 'Premiums paid',
  chipLabel: 'What you’ve paid in',
  editIntro: "Shows whether the cash value is above what you've paid.",
  chartLabel: 'Premiums paid',
}

export function purchaseCopy(category: string): PurchaseCopy {
  return category === 'policy' ? PREMIUMS : PURCHASE
}

/** Drops blank rows (both sides trimmed empty) — a free-typed label/value editor accumulates
 *  cruft (an added-then-abandoned row) that shouldn't reach the wire. */
export function sanitizeFacts(facts: [string, string][]): [string, string][] {
  return facts.map(([k, v]) => [k.trim(), v.trim()] as [string, string]).filter(([k, v]) => k && v)
}
