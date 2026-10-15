// One-shot UI-Quality-Gate evidence for BUD-FRESH-1..3 ("Start fresh from <this month>…").
//
//   node qa/shoot-budget-fresh-start.mjs
//
// Boots a DISPOSABLE demo-seeded instance on port 8093 (never 8080, ONW_QA_MODE via
// buildQaChildEnv), reads the preview + month payload straight off the API so the screenshots
// can be checked against real numbers, then drives scripts/shoot.mjs at 1280w and 390w through
// the three states: the "…" menu open, the confirm dialog with its previewed Ready to Assign,
// and the post-confirm state line + undo pill. Artifacts land in
// qa/browser-use/artifacts/budget-fresh-start/. Always tears the instance down; always leaves
// the disposable book with `rollover_start` cleared, so a re-run starts from the same state.
import { spawnSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { bootDisposableInstance } from './boot.mjs'

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..')
const OUT_DIR = join(FRONTEND, '..', 'qa', 'browser-use', 'artifacts', 'budget-fresh-start')
const PORT = 8093

const month = new Date().toISOString().slice(0, 7)

// Minutes of screenshotting pass between calls, so uvicorn will have dropped the pooled
// keep-alive connection undici held — one retry turns that ECONNRESET into a non-event.
async function api(baseUrl, path, body, method = 'GET', attempt = 0) {
  let res
  try {
    res = await fetch(`${baseUrl}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json', Connection: 'close' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (err) {
    if (attempt > 0) throw err
    return api(baseUrl, path, body, method, attempt + 1)
  }
  const text = await res.text()
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status} ${text.slice(0, 300)}`)
  return text ? JSON.parse(text) : null
}

function shoot(args) {
  const res = spawnSync('node', ['scripts/shoot.mjs', ...args], { cwd: FRONTEND, stdio: 'inherit' })
  if (res.status !== 0) console.log(`[shoot] FAILED: ${args.join(' ')}`)
  return res.status === 0
}

// The three steps that reach each state. The kebab's accessible name differs by width: desktop
// keeps the two page-level actions as buttons and the "…" carries only the Budget action;
// a phone folds all three into one "Plan settings" menu.
const KEBAB = (mobile) =>
  `button.kebab-trigger[aria-label="${mobile ? 'Plan settings' : 'Budget options'}"]`
const MENU_ITEM = '[role="menuitem"]:has-text("Start fresh from")'
const CONFIRM = '.ui-modal__footer button:has-text("Start fresh")'
const UNDO = '.pl-undo button:has-text("Undo")'

mkdirSync(OUT_DIR, { recursive: true })
// boot.mjs's ensureDist() only builds when dist/ is MISSING — a stale bundle would silently
// screenshot the previous UI. Always rebuild before shooting evidence.
console.log('[shoot] rebuilding dist…')
if (spawnSync('npm', ['run', 'build'], { cwd: FRONTEND, stdio: 'ignore' }).status !== 0) {
  throw new Error('frontend build failed')
}
/** A CASH spend in a category linked to no envelope drains Ready to Assign immediately and earns
 *  no ledger row of its own: an ENVELOPE holds a hole until month-end, nothing else does (ADR
 *  2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category). Seeded deterministically
 *  so the demo book's "after" figure is never a no-op: a category minted now gets no envelope
 *  (envelope ⊥ category), so spending in it is unbudgeted. Cash, deliberately — an unfunded CARD
 *  swipe is RTA-neutral whether or not an envelope is linked to it, so it would move nothing. */
async function seedSpendOutsideEnvelopes(baseUrl) {
  const accounts = await api(baseUrl, '/api/accounts')
  const cash = (accounts.accounts ?? accounts).find((a) => a.type === 'cash' && !a.archived)
  if (!cash) throw new Error('demo book has no on-budget cash account to spend from')
  const cat = await api(baseUrl, '/api/categories', { label: 'Hobbies', kind: 'expense' }, 'POST')
  await api(
    baseUrl,
    '/api/transactions',
    {
      kind: 'expense',
      date: `${month}-07`,
      payee: 'Kinokuniya',
      category: cat.id,
      from_account_id: cash.id,
      from_amount: 207.68,
      from_currency: cash.currency,
    },
    'POST',
  )
}

/** Σ Assigned across every envelope row this month — the figure a fresh start must zero and Undo
 *  must bring back. Read off the month payload rather than the DB so it is the same number the
 *  screenshots show. */
async function assignedTotal(baseUrl) {
  const body = await api(baseUrl, `/api/planning/month?ym=${month}`)
  return Math.round(body.totals.assigned * 100) / 100
}

/** A fresh start is only interesting on a month that HAS assignments. The demo book budgets the
 *  current month already; assert that rather than assume it, so a silent seed change can't turn
 *  the evidence into a screenshot of nothing happening. */
async function requireAssignments(baseUrl) {
  const total = await assignedTotal(baseUrl)
  if (total <= 0)
    throw new Error(`demo book has nothing assigned in ${month} — the evidence would prove nothing`)
  return total
}

const inst = await bootDisposableInstance({ port: PORT })
try {
  await api(inst.baseUrl, '/api/planning/budget-config', { rollover_start: null }, 'PUT')
  await seedSpendOutsideEnvelopes(inst.baseUrl)
  const assignedBefore = await requireAssignments(inst.baseUrl)
  // Each pass below must start from the same book, and clearing the floor alone no longer resets
  // anything now that a fresh start also empties the month's Assigned column. The undo TOKEN
  // belongs to whichever fresh start armed it — the UI's, in every pass below — and is one-shot,
  // so the runner cannot borrow it. Snapshot the funded rows here instead and put them back
  // through the ordinary assign route.
  const funded = (await api(inst.baseUrl, `/api/planning/month?ym=${month}`)).groups
    .flatMap((g) => g.categories)
    .filter((c) => c.assigned)
    .map((c) => [c.key, c.assigned])
  const reset = async () => {
    await api(inst.baseUrl, '/api/planning/budget-config', { rollover_start: null }, 'PUT')
    for (const [envelope_id, amount] of funded)
      await api(inst.baseUrl, '/api/planning/assign', { month, envelope_id, amount }, 'POST')
  }
  await reset()
  if ((await assignedTotal(inst.baseUrl)) !== assignedBefore)
    throw new Error('the reset does not restore the book — every pass below would drift')
  const before = await api(inst.baseUrl, `/api/planning/month?ym=${month}`)
  const preview = await api(inst.baseUrl, `/api/planning/fresh-start-preview?ym=${month}`)
  console.log(
    `[api] rta now = ${before.ready_to_assign} · rollover_start = ${before.rollover_start}` +
      ` · assigned this month = ${assignedBefore}`,
  )
  console.log(
    `[api] preview: after = ${preview.ready_to_assign_after} · cash on hand = ${preview.cash_now}` +
      ` · held in envelopes = ${preview.held_in_envelopes}` +
      ` · card spending not covered = ${preview.card_spend_not_covered}` +
      ` · baseline = ${preview.baseline}` +
      (preview.assigned_in_future !== undefined
        ? ` · assigned in future = ${preview.assigned_in_future}`
        : ''),
  )
  // The whole point of the ledger is that it ADDS UP EXACTLY — the SAME identity the RTA popover
  // renders, no residual term — assert it in the evidence run itself, so a screenshot can never
  // quietly show a sum that doesn't. The terms are the restored YNAB ones (ADR
  // 2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category): `held_in_envelopes` is
  // the SIGNED envelope sum, so it can be negative and ADD here, and `card_spend_not_covered` is
  // this month's uncovered card ACTIVITY, never a card balance. (The retired 2026-09-08 names,
  // `set_aside` / `unfunded_card_debt`, read as `undefined` and made this sum NaN — the crash
  // that stopped this script before it ever reached a screenshot.) No assigned-this-month term:
  // the write clears it.
  const ledgerSum =
    Math.round(
      (preview.cash_now -
        preview.baseline -
        preview.held_in_envelopes -
        preview.card_spend_not_covered -
        (preview.assigned_in_future ?? 0)) *
        100,
    ) / 100
  if (ledgerSum !== preview.ready_to_assign_after)
    throw new Error(`ledger does not add up: ${ledgerSum} != ${preview.ready_to_assign_after}`)
  console.log(`[api] ledger adds up to ${ledgerSum} ✓`)
  // The nav bound moves with the floor — the disclosed side effect, worth asserting in the log
  // beside the figures rather than only in the screenshots.
  console.log(`[api] before: nav.can_go_back = ${before.nav.can_go_back}`)

  const plan = `${inst.baseUrl}/plan?sub=budget`
  const desktop = ['--w', '1280', '--h', '1000']
  const phone = ['--w', '390', '--h', '844']

  for (const [name, size, mobile] of [
    ['desktop-1280', desktop, false],
    ['phone-390', phone, true],
  ]) {
    shoot([
      '--url',
      plan,
      '--out',
      join(OUT_DIR, `${name}-hero.png`),
      '--selector',
      '.pl-summary',
      ...size,
    ])
    shoot([
      '--url',
      plan,
      '--out',
      join(OUT_DIR, `${name}-menu-open.png`),
      '--click',
      KEBAB(mobile),
      ...size,
    ])
    shoot([
      '--url',
      plan,
      '--out',
      join(OUT_DIR, `${name}-dialog.png`),
      '--click',
      KEBAB(mobile),
      '--click',
      MENU_ITEM,
      '--selector',
      '.ui-modal-card',
      ...size,
    ])
    // Confirming writes; the next shot must therefore run against the ALREADY-fresh book, so
    // clear the floor again after each pass to keep every state reproducible.
    shoot([
      '--url',
      plan,
      '--out',
      join(OUT_DIR, `${name}-confirmed.png`),
      '--click',
      KEBAB(mobile),
      '--click',
      MENU_ITEM,
      '--click',
      CONFIRM,
      ...size,
    ])
    const after = await api(inst.baseUrl, `/api/planning/month?ym=${month}`)
    const assignedAfter = await assignedTotal(inst.baseUrl)
    console.log(
      `[api] ${name}: after confirm rta = ${after.ready_to_assign} (preview said ${preview.ready_to_assign_after})` +
        ` · rollover_start = ${after.rollover_start} · nav.can_go_back = ${after.nav.can_go_back}` +
        ` · assigned this month = ${assignedAfter} (was ${assignedBefore})`,
    )
    if (after.ready_to_assign !== preview.ready_to_assign_after)
      throw new Error(
        `the promise did not hold: ${after.ready_to_assign} != ${preview.ready_to_assign_after}`,
      )
    if (assignedAfter !== 0)
      throw new Error(`fresh start left ${assignedAfter} assigned in ${month} — it must clear it`)
    shoot([
      '--url',
      plan,
      '--out',
      join(OUT_DIR, `${name}-state-line.png`),
      '--selector',
      '.pl-summary',
      ...size,
    ])
    // Each UI-driven confirm must be undone before the next pass, or the kebab reads "Started
    // fresh from …" and none of the selectors below match.
    await reset()
    // Undo restores BOTH halves. Drive the pill in the same session that armed it (it lives 7s),
    // then prove from the API that the assignments came back — the claim the screenshots make.
    shoot([
      '--url',
      plan,
      '--out',
      join(OUT_DIR, `${name}-undone.png`),
      '--click',
      KEBAB(mobile),
      '--click',
      MENU_ITEM,
      '--click',
      CONFIRM,
      '--click',
      UNDO,
      ...size,
    ])
    const undone = await api(inst.baseUrl, `/api/planning/month?ym=${month}`)
    const assignedUndone = await assignedTotal(inst.baseUrl)
    console.log(
      `[api] ${name}: after undo rta = ${undone.ready_to_assign} (was ${before.ready_to_assign})` +
        ` · rollover_start = ${undone.rollover_start} · assigned this month = ${assignedUndone}` +
        ` (was ${assignedBefore})`,
    )
    if (assignedUndone !== assignedBefore)
      throw new Error(`undo restored ${assignedUndone}, expected ${assignedBefore}`)
    if (undone.ready_to_assign !== before.ready_to_assign)
      throw new Error(
        `undo left rta at ${undone.ready_to_assign}, expected ${before.ready_to_assign}`,
      )
    await reset()
  }
} finally {
  inst.stop()
}
