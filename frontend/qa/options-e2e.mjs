// Deterministic E2E for the options write flow (options-e2e lane).
// Boots a hardened DISPOSABLE demo instance (never :8080/live data), drives the
// real UI with Playwright: empty state → Record option → row appears → Close
// with buyback → back to empty; asserts the ledger legs via the API.
// Run: node qa/options-e2e.mjs [--out-dir /tmp/onw-options-e2e]
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from 'playwright'
import { bootDisposableInstance } from './boot.mjs'

const outDir = process.argv.includes('--out-dir')
  ? process.argv[process.argv.indexOf('--out-dir') + 1]
  : '/tmp/onw-options-e2e'
mkdirSync(outDir, { recursive: true })

const fail = (msg) => {
  throw new Error(`ASSERT FAIL: ${msg}`)
}
const results = []
const step = (name, ok, extra = '') => {
  results.push({ name, ok })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? ' — ' + extra : ''}`)
  if (!ok) fail(name)
}

let inst
let browser
try {
  inst = await bootDisposableInstance({ port: 8094 })
  const base = inst.baseUrl

  // Seed a deterministic underlying (manual price source — no network on the
  // disposable box) and make sure at least one asset account + wallet exist.
  const resolveRes = await (
    await fetch(base + '/api/instruments/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: 'AAPL', price_source: 'manual', price_source_id: 'AAPL' }),
    })
  ).json()
  if (!resolveRes.resolved) fail('could not seed the AAPL instrument')
  const seedAccounts = await (await fetch(base + '/api/accounts')).json()
  if (!seedAccounts.some((a) => a.kind === 'asset' && !a.archived)) {
    for (const name of ['IBKR', 'IBKR USD']) {
      await fetch(base + '/api/accounts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, kind: 'asset', type: 'brokerage', currency: 'USD' }),
      })
    }
  }

  browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await ctx.addInitScript(() => {
    localStorage.setItem('wealthTab', 'investments')
    localStorage.setItem('chat-open', 'false')
  })
  const page = await ctx.newPage()
  const pageErrors = []
  page.on('pageerror', (err) => pageErrors.push(String(err.message || err)))

  await page.goto(base + '/', { waitUntil: 'domcontentloaded', timeout: 20000 })
  await page.waitForTimeout(1200)

  // → Options sub-tab
  await page.getByRole('tab', { name: 'Options' }).or(page.getByText('Options', { exact: true }).first()).first().click()
  await page.waitForTimeout(800)

  // 1. Empty state with working CTAs
  const emptyTitle = await page.getByText('No options positions').count()
  step('empty state renders', emptyTitle > 0)
  await page.screenshot({ path: join(outDir, '1-empty-state.png') })

  // 2. Record option modal
  await page.getByRole('button', { name: 'Record option' }).first().click()
  await page.waitForSelector('#opt-underlying')
  const underlyings = await page.$$eval('#opt-underlying option', (os) =>
    os.map((o) => ({ value: o.value, label: o.textContent })).filter((o) => o.value),
  )
  step('underlying select populated from real instruments', underlyings.length > 0)
  const accounts = await page.$$eval('#opt-account option', (os) =>
    os.map((o) => ({ value: o.value, label: o.textContent })).filter((o) => o.value),
  )
  step('account select populated from real accounts', accounts.length > 0)

  await page.selectOption('#opt-underlying', underlyings[0].value)
  await page.selectOption('#opt-account', accounts[0].value)
  await page.fill('#opt-strike', '150')
  await page.fill('#opt-expiry', '2026-09-18')
  await page.fill('#opt-contracts', '1')
  await page.fill('#opt-premium', '2.5')
  await page.selectOption('#opt-premium-account', accounts[0].value)
  const preview = await page.textContent('.opt-form-preview')
  step('live premium preview computes 250', /250/.test(preview || ''), preview?.trim())
  await page.screenshot({ path: join(outDir, '2-record-modal.png') })

  const footer = page.locator('.ui-modal-card, [role="dialog"]').last()
  await footer.getByRole('button', { name: 'Record option' }).click()
  await page.waitForTimeout(1200)

  // 3. Row appears in Income overlays
  const rowCount = await page.locator('.opt-tbl tbody tr').count()
  step('contract row appears after record', rowCount === 1)
  const income = await page.getByText('Income overlays').count()
  step('short covered call lands in Income overlays', income > 0)
  await page.screenshot({ path: join(outDir, '3-open-position.png') })

  // API: contract + premium leg
  const book = await (await fetch(base + '/api/options')).json()
  step('GET /api/options shows 1 open position', book.positions.length === 1)
  const txns1 = await (await fetch(base + '/api/transactions?limit=200')).json()
  const list1 = Array.isArray(txns1) ? txns1 : txns1.transactions || txns1.items || []
  const premiumLeg = list1.filter((t) => (t.external_id || '').startsWith('option-premium:'))
  step('premium income leg booked (external_id option-premium:)', premiumLeg.length === 1)

  // 4. Close with buyback
  await page.getByRole('button', { name: 'Close', exact: true }).first().click()
  await page.waitForSelector('#opt-close-pps')
  await page.fill('#opt-close-pps', '1')
  await page.selectOption('#opt-close-wallet', accounts[0].value)
  await page.screenshot({ path: join(outDir, '4-close-modal.png') })
  const closeFooter = page.locator('.ui-modal-card, [role="dialog"]').last()
  await closeFooter.getByRole('button', { name: 'Close position' }).click()
  await page.waitForTimeout(1200)

  const emptyAgain = await page.getByText('No options positions').count()
  step('closed contract leaves the open book (empty state back)', emptyAgain > 0)
  await page.screenshot({ path: join(outDir, '5-after-close.png') })

  const book2 = await (await fetch(base + '/api/options')).json()
  step('GET /api/options open positions back to 0', book2.positions.length === 0)
  const txns2 = await (await fetch(base + '/api/transactions?limit=200')).json()
  const list2 = Array.isArray(txns2) ? txns2 : txns2.transactions || txns2.items || []
  const closeLeg = list2.filter((t) => (t.external_id || '').startsWith('option-close:'))
  step('buyback expense leg booked (external_id option-close:)', closeLeg.length === 1)
  step(
    'buyback leg is a 100-dollar expense',
    closeLeg[0].kind === 'expense' && Math.abs((closeLeg[0].from_amount || 0) - 100) < 0.01,
    `kind=${closeLeg[0].kind} amount=${closeLeg[0].from_amount}`,
  )

  step('no page errors during the whole flow', pageErrors.length === 0, pageErrors.join(' | '))

  console.log(`\nOPTIONS-E2E: ${results.length}/${results.length} steps passed · screenshots → ${outDir}`)
} catch (e) {
  console.log('\nOPTIONS-E2E FAILED:', e.message)
  process.exitCode = 1
} finally {
  if (browser) await browser.close()
  if (inst) inst.stop()
}
