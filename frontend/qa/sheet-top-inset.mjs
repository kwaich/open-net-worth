// sheet-top-inset.mjs — real-Chromium verification that every phone bottom sheet stops at the
// shared ceiling (`--sheet-max-height`: a fixed gap under the status bar, iOS's large-detent
// rule) with the scrim visible above it, that the two deliberate full-screen surfaces are
// untouched, and — via a token override, since headless Chromium reports no
// env(safe-area-inset-top) — what the ceiling looks like on a Dynamic-Island phone in
// standalone mode. The gap on a REAL notch is still the owner's phone to confirm.
import { mkdirSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { chromium } from 'playwright'
import { bootDisposableInstance } from './boot.mjs'

const outputDir = process.env.ONW_EVIDENCE || '/tmp/onw-sheet-top-inset'
const port = Number(process.env.ONW_PORT || 8797)
mkdirSync(outputDir, { recursive: true })
const emptyDataDir = mkdtempSync(join(tmpdir(), 'onw-sheet-inset-data-'))
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const VIEWPORT = { width: 390, height: 760 }
const GAP_PX = 12
const CEILING = `${VIEWPORT.height - GAP_PX}px`
const NOTCH_PX = 59 // iOS status bar on a Dynamic-Island phone, standalone mode

const failures = []
const consoleErrors = []
const pageErrors = []
function check(cond, message) {
  if (!cond) failures.push(message)
  console.log(`${cond ? 'PASS' : 'FAIL'} — ${message}`)
}

/** Geometry a sheet root must satisfy: where its top edge sits, what the browser finds just
 * above it (the scrim, never the sheet itself), and the computed ceiling. */
const measure = (sheet) =>
  sheet.evaluate((el) => {
    const r = el.getBoundingClientRect()
    const cs = getComputedStyle(el)
    const probe = document.elementFromPoint(
      Math.round(r.left + r.width / 2),
      Math.max(0, Math.round(r.top) - 4),
    )
    return {
      top: Math.round(r.top),
      bottom: Math.round(r.bottom),
      maxHeight: cs.maxHeight,
      height: cs.height,
      radius: cs.borderTopLeftRadius,
      aboveIsSheet: probe ? el.contains(probe) : null,
    }
  })

async function assertCapped({ page, sheet, label, prefix, radius }) {
  const m = await measure(sheet)
  check(m.top >= GAP_PX, `${label}: top edge ${m.top}px sits at/below the ${GAP_PX}px gap`)
  check(m.bottom === VIEWPORT.height, `${label}: docked to the bottom edge (bottom=${m.bottom})`)
  check(
    m.maxHeight === CEILING,
    `${label}: computed max-height is the shared ceiling (${m.maxHeight})`,
  )
  check(
    m.aboveIsSheet === false,
    `${label}: the point just above the sheet is the scrim, not the sheet`,
  )
  if (radius) check(m.radius === radius, `${label}: rounded top corners (${m.radius})`)
  await page.screenshot({ path: join(outputDir, `${prefix}.png`) })
  return m
}

async function dismiss(page, sheet) {
  await page.keyboard.press('Escape')
  await sheet.waitFor({ state: 'detached', timeout: 2000 }).catch(async () => {
    await page
      .getByRole('button', { name: 'Close' })
      .first()
      .click()
      .catch(() => {})
    await sheet.waitFor({ state: 'detached', timeout: 2000 }).catch(() => {})
  })
  await sleep(300)
}

const app = await bootDisposableInstance({ port, dataDir: emptyDataDir })
const browser = await chromium.launch({ headless: true })

try {
  const seedRes = await fetch(`${app.baseUrl}/api/demo/seed`, { method: 'POST' })
  if (!seedRes.ok) throw new Error(`demo seed failed: ${seedRes.status}`)

  const context = await browser.newContext({ viewport: VIEWPORT, hasTouch: true, isMobile: true })
  const page = await context.newPage()
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  })
  page.on('pageerror', (err) => pageErrors.push(String(err)))
  await page.goto(app.baseUrl, { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: 'More', exact: true }).waitFor()

  // Computed values have env() already resolved (to 0px here), so read the DECLARED tokens
  // off the stylesheet to prove the ceiling is anchored to the status bar, not a viewport share.
  const tokens = await page.evaluate(() => {
    const declared = {}
    for (const sheet of document.styleSheets) {
      let rules
      try {
        rules = sheet.cssRules
      } catch {
        continue
      }
      const walk = (list) => {
        for (const rule of list) {
          // A style rule carries BOTH (nesting gives it an empty cssRules) — read, then descend.
          // Only the token block itself counts: a reset rule's `all: unset` reports every
          // property, custom ones included, as "unset".
          if (rule.style && rule.selectorText === ':root') {
            for (const name of ['--sheet-top-inset', '--sheet-max-height']) {
              const v = rule.style.getPropertyValue(name).trim()
              if (v) declared[name] = v
            }
          }
          if (rule.cssRules) walk(rule.cssRules)
        }
      }
      walk(rules)
    }
    return declared
  })
  // The CSSOM re-serializes calc() (drops the space after the comma) — match loosely.
  check(
    /env\(safe-area-inset-top,\s*0px\)\s*\+\s*12px/.test(tokens['--sheet-top-inset'] ?? ''),
    `--sheet-top-inset is the status bar + ${GAP_PX}px (${tokens['--sheet-top-inset']})`,
  )
  check(
    /100dvh - var\(--sheet-top-inset\)/.test(tokens['--sheet-max-height'] ?? ''),
    `--sheet-max-height derives from the inset (${tokens['--sheet-max-height']})`,
  )

  console.log('\n=== 1. Settings (workspace-tier modal that is a sheet on phones) ===')
  await page.getByRole('button', { name: 'More', exact: true }).click()
  await page.locator('.mb-sheet').waitFor()
  await sleep(350)
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  const settings = page.locator('.ui-modal-card--workspace.onw-settings')
  await settings.waitFor()
  await sleep(450)
  await assertCapped({
    page,
    sheet: settings,
    label: 'Settings',
    prefix: '1-settings',
    radius: '18px',
  })
  await dismiss(page, settings)

  console.log('\n=== 2. Command search stays full-screen (deliberate exception) ===')
  await page.getByRole('button', { name: 'More', exact: true }).click()
  await page.locator('.mb-sheet').waitFor()
  await sleep(350)
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  const cmdk = page.locator('.ui-modal-card:has(.cmdk)')
  await cmdk.waitFor()
  await sleep(350)
  const cm = await measure(cmdk)
  check(
    cm.top === 0 && cm.maxHeight === 'none',
    `command search is untouched: top=${cm.top}, max-height=${cm.maxHeight}`,
  )
  await dismiss(page, cmdk)

  console.log('\n=== 3. Manage payees (was 100dvh full-screen) ===')
  await page.getByRole('button', { name: 'Accounts', exact: true }).click()
  await page.locator('.acct-card').first().waitFor({ timeout: 10_000 })
  const openPayees = async () => {
    await page.getByRole('button', { name: 'More actions' }).click()
    await page.getByRole('button', { name: 'Manage payees' }).click()
    await page.locator('.ui-modal-card:has(.pm-body)').waitFor()
    await page
      .locator('.pm-list button, .pm-list [role="button"]')
      .first()
      .waitFor({ timeout: 10_000 })
    await sleep(450)
  }
  await openPayees()
  const payees = page.locator('.ui-modal-card:has(.pm-body)')
  const pm = await assertCapped({
    page,
    sheet: payees,
    label: 'Manage payees',
    prefix: '3-payees',
    radius: '14px',
  })
  check(
    pm.height === CEILING,
    `Manage payees keeps a definite height at the ceiling for its sliding panes (${pm.height})`,
  )
  const headPad = await payees
    .locator('.ui-modal__head')
    .evaluate((el) => getComputedStyle(el).paddingTop)
  check(
    headPad === '10px',
    `Manage payees header no longer pads under the status bar (padding-top ${headPad})`,
  )
  await page.locator('.pm-list button, .pm-list [role="button"]').first().click()
  await sleep(500)
  const panes = await payees.evaluate((card) => {
    const c = card.getBoundingClientRect()
    const main = card.querySelector('.pm-main')?.getBoundingClientRect()
    return main
      ? {
          fits: main.top >= c.top - 1 && main.bottom <= c.bottom + 1,
          mainTop: Math.round(main.top),
          cardTop: Math.round(c.top),
        }
      : null
  })
  check(
    panes?.fits === true,
    `Manage payees detail pane fits inside the capped card (pane top ${panes?.mainTop}, card top ${panes?.cardTop})`,
  )
  await page.screenshot({ path: join(outputDir, '3-payees-detail.png') })
  await dismiss(page, payees)

  console.log(
    `\n=== 4. Standalone-mode preview: --sheet-top-inset overridden to ${NOTCH_PX}px + ${GAP_PX}px ===`,
  )
  const notch = await page.addStyleTag({
    content: `:root{--sheet-top-inset: calc(${NOTCH_PX}px + ${GAP_PX}px)}`,
  })
  await openPayees()
  const pn = await measure(payees)
  check(
    pn.top >= NOTCH_PX + GAP_PX,
    `with a ${NOTCH_PX}px status bar the sheet starts at ${pn.top}px (≥ ${NOTCH_PX + GAP_PX})`,
  )
  check(
    pn.maxHeight === `${VIEWPORT.height - NOTCH_PX - GAP_PX}px`,
    `ceiling follows the inset (${pn.maxHeight})`,
  )
  await page.screenshot({ path: join(outputDir, '4-payees-notch-preview.png') })
  await dismiss(page, payees)
  await notch.evaluate((el) => el.remove())

  console.log('\n=== 5. TxnDetail sheet ===')
  await page.locator('.acct-card').first().click()
  const txn = page.locator('.acct-detail.sheet')
  await txn.waitFor()
  await sleep(450)
  await assertCapped({
    page,
    sheet: txn,
    label: 'TxnDetail',
    prefix: '5-txndetail',
    radius: '18px',
  })
  await dismiss(page, txn)

  console.log('\n=== 6. Kebab sheet untouched, PlanningModal capped ===')
  await page.getByRole('button', { name: 'More', exact: true }).click()
  await page.getByRole('button', { name: 'Plan', exact: true }).click()
  await page.waitForTimeout(400)
  await page.getByRole('button', { name: 'Plan settings' }).click()
  const kebab = page.locator('.kebab-sheet')
  await kebab.waitFor()
  await sleep(350)
  const km = await measure(kebab)
  check(
    km.maxHeight === `${Math.round(VIEWPORT.height * 0.78 * 100) / 100}px` && km.top > GAP_PX,
    `kebab sheet keeps its own content cap (max-height ${km.maxHeight}, top ${km.top})`,
  )
  await page.getByRole('menuitem', { name: /Manage categories/i }).click()
  const plModal = page.locator('.pl-modal')
  await plModal.waitFor()
  await page.locator('.pl-catmgr-row:not(.pl-catmgr-addrow)').nth(10).waitFor({ timeout: 10_000 })
  await sleep(450)
  await assertCapped({
    page,
    sheet: plModal,
    label: 'PlanningModal',
    prefix: '6-planmodal',
    radius: '14px',
  })
  await dismiss(page, plModal)

  check(consoleErrors.length === 0, `no console errors (${consoleErrors.length})`)
  check(pageErrors.length === 0, `no page errors (${pageErrors.length})`)
  if (consoleErrors.length) console.log(consoleErrors.slice(0, 5).join('\n'))
  if (pageErrors.length) console.log(pageErrors.slice(0, 5).join('\n'))

  await context.close()
} finally {
  await browser.close()
  app.stop()
}

console.log(`\nEvidence: ${outputDir}`)
if (failures.length > 0) {
  console.error(`\nsheet-top-inset: FAIL (${failures.length})`)
  process.exit(1)
}
console.log('\nsheet-top-inset: PASS')
