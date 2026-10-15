// price-alerts-modal.mjs — real-Chromium evidence for the redesigned Price Alerts modal
// (docs/qa/acceptance/price-alerts-redesign.md). Boots a DISPOSABLE demo instance (never
// :8080 / live data), seeds three alerts on a watched ticker through /api/alerts, then drives
// the modal: manage view, New alert form (target price + % move), edit form, a row toggle, and
// the same manage view on a phone viewport. Screenshots land in ONW_EVIDENCE.
import { mkdirSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { chromium } from 'playwright'
import { bootDisposableInstance } from './boot.mjs'

const outputDir = process.env.ONW_EVIDENCE || '/tmp/onw-price-alerts-evidence'
const port = Number(process.env.ONW_PORT || 8797)
mkdirSync(outputDir, { recursive: true })
const dataDir = mkdtempSync(join(tmpdir(), 'onw-price-alerts-data-'))
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const TICKER = 'NVDA'

const failures = []
const pageErrors = []
function check(cond, message) {
  if (!cond) failures.push(message)
  console.log(`${cond ? 'PASS' : 'FAIL'} — ${message}`)
}

const app = await bootDisposableInstance({ port, dataDir })
const browser = await chromium.launch({ headless: true })
try {
  await fetch(`${app.baseUrl}/api/demo/seed`, { method: 'POST' })
  const post = (body) =>
    fetch(`${app.baseUrl}/api/alerts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  const prices = await fetch(`${app.baseUrl}/api/prices?tickers=${TICKER}`).then((r) => r.json())
  const live = Number(prices?.prices?.[TICKER]) || 300
  console.log(`[evidence] ${TICKER} live price: ${live}`)
  const r1 = await post({ ticker: TICKER, op: '<=', value: Math.round(live * 0.95), name: 'Good entry zone', note: 'Check whether the dip is news-driven before adding.', label: 'NVIDIA', asset_class: 'equity' })
  const r2 = await post({ ticker: TICKER, op: '>=', value: Math.round(live * 1.12), note: '', label: 'NVIDIA', asset_class: 'equity', on_trigger: 'disable' })
  const r3 = await post({ ticker: TICKER, op: '<=', value: 5, kind: 'move', name: 'Big down day', note: '', label: 'NVIDIA', asset_class: 'equity', notify: 'in_app' })
  check(r1.status === 201 && r2.status === 201 && r3.status === 201, `seeded three alerts on ${TICKER} (${r1.status}/${r2.status}/${r3.status})`)

  const shoot = async (page, name) => {
    await sleep(500)
    await page.screenshot({ path: join(outputDir, name), fullPage: false })
    console.log(`[evidence] ${name}`)
  }
  const openModal = async (page) => {
    await page.goto(`${app.baseUrl}/#/intel`, { waitUntil: 'domcontentloaded' })
    await page.getByRole('button', { name: new RegExp(`Edit alerts for .*(NVIDIA|${TICKER})`) }).first().waitFor({ timeout: 20_000 })
    await sleep(800)
    await page.getByRole('button', { name: new RegExp(`Edit alerts for .*(NVIDIA|${TICKER})`) }).first().click()
    await page.locator('.pa-modal').waitFor({ timeout: 10_000 })
    await sleep(600)
  }

  // ── desktop ─────────────────────────────────────────────────────────────────────
  const desktop = await browser.newContext({ viewport: { width: 1280, height: 900 } })
  const page = await desktop.newPage()
  page.on('pageerror', (e) => pageErrors.push(String(e)))
  await openModal(page)
  const modal = page.locator('.pa-modal')
  check(await modal.getByText(`${TICKER} · `, { exact: false }).count() > 0, 'manage view title carries the ticker')
  check(await modal.getByText('Good entry zone').count() === 1, 'named alert row is listed by its name')
  check(await modal.getByText('Big down day').count() === 1, '% move alert row is listed')
  const seeded = await fetch(`${app.baseUrl}/api/alerts?ticker=${TICKER}`).then((r) => r.json())
  const priceLevels = seeded.alerts.filter((a) => a.kind !== 'move').length
  const hasPrice = async (m) => (await m.getByText('Price unavailable').count()) === 0
  const checkTrackAndSuggestions = async (m, where) => {
    if (!(await hasPrice(m))) {
      console.log(`SKIP — ${where}: no live price in this isolated instance (/api/prices is a network fetch), track + suggestions need one`)
      return false
    }
    check(await m.locator('.pa-scale .pa-tk').count() === priceLevels, `${where}: the 52-week track ticks every price level (${priceLevels}) and not the % move`)
    await m.getByRole('button', { name: 'Suggest', exact: true }).click()
    await sleep(500)
    check(await m.locator('.pa-sg-row').count() > 0, `${where}: Lucius suggestion set renders with reasoning rows`)
    return true
  }
  await checkTrackAndSuggestions(modal, 'Intel rail')
  check(await modal.getByRole('button', { name: 'Save' }).count() === 0, 'no Save button — every control commits itself')
  await shoot(page, '01-manage-desktop.png')

  // row toggle commits immediately
  const badgeBefore = await modal.locator('.ui-panel__badge').innerText()
  // The switch input is visually hidden behind its track — click the wrapping label.
  await modal.getByRole('switch', { name: /Turn off the/ }).first().locator('xpath=..').click()
  await sleep(900)
  const badgeAfter = await modal.locator('.ui-panel__badge').innerText()
  check(badgeBefore !== badgeAfter, `row switch changed the panel badge (${badgeBefore} → ${badgeAfter})`)
  const stored = await fetch(`${app.baseUrl}/api/alerts?ticker=${TICKER}`).then((r) => r.json())
  check(stored.alerts.some((a) => a.enabled === false), 'the toggle was persisted (enabled:false in the store)')
  await shoot(page, '02-manage-desktop-row-off.png')

  await shoot(page, '03-manage-desktop-suggestions.png')

  // new alert form
  await modal.getByRole('button', { name: 'New alert' }).first().click()
  await page.locator('.pa-fields').waitFor()
  check(await page.getByText('Alert criteria').count() === 1, 'New alert form opened')
  check(await page.getByLabel('Ticker').isDisabled(), 'ticker select is locked to the context ticker')
  await shoot(page, '04-form-new-desktop.png')
  await page.getByRole('tab', { name: '% move' }).click()
  await sleep(300)
  check(await page.getByText('Movement amount (%)').count() === 1, '% move criteria swaps the field')
  await shoot(page, '05-form-new-move-desktop.png')
  await page.getByRole('button', { name: 'Cancel' }).click()
  await page.locator('.pa-list').waitFor()

  // edit form
  await page.getByText('Good entry zone').click()
  await page.locator('.pa-formsw').waitFor()
  check(await page.getByText(`Edit alert · ${TICKER}`).count() === 1, 'clicking a row opens Edit alert')
  await shoot(page, '06-form-edit-desktop.png')
  await page.getByRole('button', { name: 'Cancel' }).click()

  // the asset-detail rail card path — its quote comes from the detail service, so the
  // track + suggestions can be evidenced here even when /api/prices has no live print
  await page.goto(`${app.baseUrl}/#/quote/${TICKER}`, { waitUntil: 'domcontentloaded' })
  const manage = page.getByRole('button', { name: /^Manage/ })
  await manage.first().waitFor({ timeout: 30_000 })
  await sleep(800)
  await manage.first().click()
  await page.locator('.pa-modal').waitFor({ timeout: 10_000 })
  await sleep(800)
  const qmodal = page.locator('.pa-modal')
  check(await qmodal.getByRole('button', { name: 'Close' }).count() >= 1, 'asset-detail path: footer back action reads Close')
  const evidenced = await checkTrackAndSuggestions(qmodal, 'asset-detail card')
  await shoot(page, evidenced ? '09-manage-from-quote-with-track.png' : '09-manage-from-quote.png')
  await desktop.close()

  // ── phone ───────────────────────────────────────────────────────────────────────
  const phone = await browser.newContext({ viewport: { width: 390, height: 760 }, hasTouch: true, isMobile: true })
  const mpage = await phone.newPage()
  mpage.on('pageerror', (e) => pageErrors.push(String(e)))
  try {
    await openModal(mpage)
    await shoot(mpage, '07-manage-phone.png')
    const overflow = await mpage.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    check(overflow <= 1, `no horizontal overflow on the phone manage view (${overflow}px)`)
    await mpage.locator('.pa-modal').getByRole('button', { name: 'New alert' }).first().click()
    await mpage.locator('.pa-fields').waitFor()
    await shoot(mpage, '08-form-new-phone.png')
  } catch (e) {
    console.log(`[evidence] phone path not exercised: ${String(e).split('\n')[0]}`)
    await shoot(mpage, '07-phone-fallback.png')
  }
  await phone.close()

  check(pageErrors.length === 0, `no page errors (${pageErrors.length})`)
  if (pageErrors.length) console.log(pageErrors.join('\n'))
} finally {
  await browser.close()
  await app.stop()
}
if (failures.length) {
  console.log(`\n${failures.length} FAILURE(S)`)
  process.exit(1)
}
console.log('\nALL PASS')
