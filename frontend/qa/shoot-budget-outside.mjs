// One-shot UI-Quality-Gate evidence for BUD-OUTSIDE-1..3 (spending outside envelopes).
//
//   node qa/shoot-budget-outside.mjs
//
// Boots a DISPOSABLE demo-seeded instance on port 8093 (never 8080, ONW_QA_MODE via
// buildQaChildEnv), seeds the two states the banner needs that the demo book lacks —
// an unlinked category with spending, and one blank-category expense — then drives
// scripts/shoot.mjs at 390w and 1280w. Artifacts land in
// qa/browser-use/artifacts/budget-outside/. Always tears the instance down.
import { spawnSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { bootDisposableInstance } from './boot.mjs'

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..')
const OUT_DIR = join(FRONTEND, '..', 'qa', 'browser-use', 'artifacts', 'budget-outside')
const PORT = 8093

const month = new Date().toISOString().slice(0, 7)
const today = `${month}-15`

async function api(baseUrl, path, body, method = 'POST') {
  const res = await fetch(`${baseUrl}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await res.text()
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status} ${text.slice(0, 300)}`)
  return text ? JSON.parse(text) : null
}

/** The demo book has no labeled-but-unlinked spending, so the banner's second half never
 *  renders. A brand-new category mints NO envelope (envelope ⊥ category full split), which is
 *  exactly the `unlinked` state; one expense against it and one blank-category expense give
 *  the "both" case. */
async function seedOutsideSpending(baseUrl) {
  const accounts = await api(baseUrl, '/api/accounts', undefined, 'GET')
  const list = Array.isArray(accounts) ? accounts : (accounts.accounts ?? [])
  const cash = list.find((a) => a.type === 'cash' && !a.archived)
  if (!cash) throw new Error('demo book has no cash account to spend from')

  const gifts = await api(baseUrl, '/api/categories', {
    label: 'Gifts',
    kind: 'expense',
    color: '#c98a3a',
  })
  const giftsId = gifts.id ?? gifts.key
  console.log(`[seed] unlinked category ${giftsId} (Gifts) — no envelope minted`)

  await api(baseUrl, '/api/transactions', {
    kind: 'expense',
    date: today,
    from_amount: 207.68,
    from_account_id: cash.id,
    category: giftsId,
    payee: 'Gift Shop',
    note: 'QA: unlinked-category spending',
  })
  await api(baseUrl, '/api/transactions', {
    kind: 'expense',
    date: today,
    from_amount: 5,
    from_account_id: cash.id,
    payee: 'Kiosk',
    note: 'QA: uncategorized spending',
  })
  console.log('[seed] booked S$207.68 in Gifts (no envelope) + S$5.00 uncategorized')
}

function shoot(args) {
  const res = spawnSync('node', ['scripts/shoot.mjs', ...args], {
    cwd: FRONTEND,
    stdio: 'inherit',
  })
  if (res.status !== 0) console.log(`[shoot] FAILED: ${args.join(' ')}`)
  return res.status === 0
}

mkdirSync(OUT_DIR, { recursive: true })
// boot.mjs's ensureDist() only builds when dist/ is MISSING — a stale bundle would silently
// screenshot the previous UI. Always rebuild before shooting evidence.
console.log('[shoot] rebuilding dist…')
if (spawnSync('npm', ['run', 'build'], { cwd: FRONTEND, stdio: 'ignore' }).status !== 0) {
  throw new Error('frontend build failed')
}
const inst = await bootDisposableInstance({ port: PORT })
try {
  await seedOutsideSpending(inst.baseUrl)
  const totals = await api(inst.baseUrl, `/api/planning/month?month=${month}`, undefined, 'GET')
  console.log(`[api] totals.available = ${totals.totals.available}`)
  console.log(
    `[api] unbudgeted group = ${JSON.stringify(
      totals.groups.find((g) => g.unbudgeted)?.categories ?? [],
    )}`,
  )

  const plan = `${inst.baseUrl}/plan?sub=budget`
  const desktop = ['--w', '1280', '--h', '1000']
  const phone = ['--w', '390', '--h', '844']

  shoot(['--url', plan, '--out', join(OUT_DIR, 'desktop-1280-budget.png'), ...desktop])
  shoot([
    '--url',
    plan,
    '--out',
    join(OUT_DIR, 'desktop-1280-banner.png'),
    '--selector',
    '.pl-uncat',
    ...desktop,
  ])
  shoot([
    '--url',
    plan,
    '--out',
    join(OUT_DIR, 'desktop-1280-summary-hover.png'),
    '--hover',
    '.pl-sum-stat[title]',
    '--selector',
    '.pl-summary',
    ...desktop,
  ])
  shoot([
    '--url',
    plan,
    '--out',
    join(OUT_DIR, 'desktop-1280-picker-open.png'),
    '--click',
    'button.pl-uncat-link',
    ...desktop,
  ])
  shoot(['--url', plan, '--out', join(OUT_DIR, 'phone-390-budget.png'), ...phone])
  shoot([
    '--url',
    plan,
    '--out',
    join(OUT_DIR, 'phone-390-banner.png'),
    '--selector',
    '.pl-uncat',
    ...phone,
  ])
  shoot([
    '--url',
    plan,
    '--out',
    join(OUT_DIR, 'phone-390-picker-open.png'),
    '--click',
    'button.pl-uncat-link',
    ...phone,
  ])
} finally {
  inst.stop()
}
