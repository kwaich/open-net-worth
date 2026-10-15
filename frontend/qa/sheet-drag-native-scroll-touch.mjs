// sheet-drag-native-scroll-touch.mjs — REAL touch-emulated verification of the mobile bottom
// sheet gesture contract (useSheetDragToDismiss, ADR 2026-09-03-sheet-gesture-native-scroll):
//
//   1. content inside a sheet scrolls NATIVELY — a drag over it moves scrollTop, not the sheet;
//   2. a gesture that started over SCROLLED content never becomes a dismiss, even when it drags
//      the content all the way back to its top in the same motion;
//   3. the NEXT pull, from content that is at its top, dismisses — which also proves the
//      non-passive touchmove veto holds in a real compositor: without it Chromium would start a
//      (contained) scroll gesture, fire pointercancel, and the drag would die at its first pixel.
//
// Why this can't be a jsdom/vitest test: `touch-action`, passive-listener semantics and
// pointercancel-on-scroll are real-browser input-pipeline concepts — jsdom fires synthetic
// pointer events with no notion of them. This script boots a REAL disposable instance of the
// app and drives it with Chromium's own touch input pipeline (CDP Input.dispatchTouchEvent, not
// page.evaluate'd DOM events) via a `hasTouch: true, isMobile: true` context. Safari/iOS is
// out of reach here — a phone remains the final check for WebKit.

import { mkdirSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { chromium } from 'playwright'
import { bootDisposableInstance } from './boot.mjs'

const outputDir = process.env.ONW_SHEET_DRAG_EVIDENCE || '/tmp/onw-sheet-drag-native-scroll-touch'
const port = Number(process.env.ONW_SHEET_DRAG_PORT || 8795)
mkdirSync(outputDir, { recursive: true })

const emptyDataDir = mkdtempSync(join(tmpdir(), 'onw-sheet-drag-data-'))
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const SETTLE_MS = 450
// Longer than the hook's SCROLL_LOCK_TIMEOUT_MS (100) plus any native scroll settling.
const BETWEEN_GESTURES_MS = 400

async function touchDrag(cdp, { x, yStart, yEnd, steps = 20, stepDelayMs = 16 }) {
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y: yStart, id: 1 }] })
  await sleep(60)
  for (let i = 1; i <= steps; i += 1) {
    const y = yStart + (yEnd - yStart) * (i / steps)
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y, id: 1 }] })
    await sleep(stepDelayMs)
  }
  await sleep(60)
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await sleep(60)
}

const failures = []
const consoleErrors = []
const pageErrors = []
function check(cond, message) {
  if (!cond) failures.push(message)
  console.log(`${cond ? 'PASS' : 'FAIL'} — ${message}`)
}

/** The three-part contract, run against one sheet family. `sheet` is the element the gesture
 * handlers live on; `body` is its scroll container (the same element for TxnDetail, whose sticky
 * grab bar + title row sit INSIDE the scroller as opted-in drag surfaces — `contentInset` keeps
 * the content pulls below them). */
async function runContract({ page, cdp, label, prefix, sheet, body, contentInset = 30 }) {
  // -1 once the sheet is gone, so a wrongly-dismissed sheet reads as a FAIL, not a hang.
  const scrollTop = async () => ((await body.count()) === 0 ? -1 : body.evaluate((el) => el.scrollTop))
  check(await body.evaluate((el) => el.scrollHeight > el.clientHeight), `${label}: body has scrollable content`)

  // 1. Short upward swipe over content near its bottom → native scroll, sheet untouched.
  const bodyBox = await body.boundingBox()
  const x = bodyBox.x + bodyBox.width / 2
  const contentY = bodyBox.y + contentInset
  await touchDrag(cdp, { x, yStart: bodyBox.y + bodyBox.height - 40, yEnd: bodyBox.y + bodyBox.height - 160 })
  await sleep(BETWEEN_GESTURES_MS)
  const scrolled = await scrollTop()
  check(scrolled > 0, `${label}: a swipe over content scrolled it natively (scrollTop=${scrolled})`)
  check((await sheet.count()) === 1, `${label}: sheet still open after a content scroll`)
  check((await sheet.evaluate((el) => el.style.transform)) === '', `${label}: sheet transform never written during a scroll`)
  await page.screenshot({ path: join(outputDir, `${prefix}-1-scrolled.png`) })

  // 2. Long pull that STARTS over scrolled content → the content scrolls back toward its top,
  //    the sheet stays open. Even once the content hits the top mid-gesture, it must not hand
  //    off into a dismiss.
  const sheetBox = await sheet.boundingBox()
  await touchDrag(cdp, { x, yStart: contentY, yEnd: bodyBox.y + sheetBox.height + 200, steps: 30 })
  await sleep(BETWEEN_GESTURES_MS)
  const afterPull = await scrollTop()
  check((await sheet.count()) === 1, `${label}: a pull that began over SCROLLED content did not dismiss (scrollTop ${scrolled} → ${afterPull})`)
  check(afterPull < scrolled, `${label}: …but it did scroll the content back up natively`)
  await page.screenshot({ path: join(outputDir, `${prefix}-2-pulled-not-dismissed.png`) })

  // 3. Make sure the content really is at its top, then a NEW pull from the content dismisses.
  for (let i = 0; i < 3 && (await scrollTop()) > 0; i += 1) {
    await touchDrag(cdp, { x, yStart: contentY, yEnd: contentY + 270, steps: 15 })
    await sleep(BETWEEN_GESTURES_MS)
  }
  check((await scrollTop()) === 0, `${label}: content is at its top before the dismissing pull`)
  await touchDrag(cdp, { x, yStart: contentY, yEnd: bodyBox.y + sheetBox.height + 200, steps: 30 })
  await sheet.waitFor({ state: 'detached', timeout: 3000 }).catch(() => {})
  check((await sheet.count()) === 0, `${label}: the NEXT pull from at-top content dismissed the sheet`)
}

const app = await bootDisposableInstance({ port, dataDir: emptyDataDir })
const browser = await chromium.launch({ headless: true })

try {
  console.log('[seed] building the demo book over HTTP (/api/demo/seed)…')
  const seedRes = await fetch(`${app.baseUrl}/api/demo/seed`, { method: 'POST' })
  if (!seedRes.ok) throw new Error(`demo seed failed: ${seedRes.status} ${await seedRes.text()}`)

  const context = await browser.newContext({ viewport: { width: 390, height: 760 }, hasTouch: true, isMobile: true })
  const page = await context.newPage()
  page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()) })
  page.on('pageerror', (err) => pageErrors.push(String(err)))
  const cdp = await context.newCDPSession(page)

  await page.goto(app.baseUrl, { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: 'More' }).waitFor()

  console.log('\n=== Scenario 1: MobileTabBar More sheet (.mb-sheet / .mb-sheet-body) ===')
  await page.getByRole('button', { name: 'More' }).click()
  const moreSheet = page.locator('.mb-sheet')
  await moreSheet.waitFor()
  await sleep(SETTLE_MS)
  await runContract({ page, cdp, label: 'More sheet', prefix: '1-more', sheet: moreSheet, body: page.locator('.mb-sheet-body') })

  console.log('\n=== Scenario 2: TxnDetail bottom sheet (.acct-detail.sheet is its own scroll container) ===')
  await page.getByRole('button', { name: 'Accounts' }).click()
  const txnCard = page.locator('.acct-card').first()
  await txnCard.waitFor({ timeout: 10000 })
  const openTxnDetail = async () => {
    await txnCard.click()
    await page.locator('.acct-detail.sheet').waitFor()
    await sleep(SETTLE_MS)
  }
  await openTxnDetail()
  const txnSheet = page.locator('.acct-detail.sheet')
  // The sticky grab bar + kicker/✕ row are the top ~70px of the scroller; content pulls start
  // well below them so they exercise the scroll-position rule, not the drag-surface opt-in.
  const txnChromeBottom = await page.locator('.acct-detail-top').evaluate((el) => el.getBoundingClientRect().bottom)
  const txnSheetTop = (await txnSheet.boundingBox()).y
  await runContract({ page, cdp, label: 'TxnDetail', prefix: '2-txndetail', sheet: txnSheet, body: txnSheet, contentInset: txnChromeBottom - txnSheetTop + 40 })

  // Its grab bar lives INSIDE the scroller (position: sticky) yet must drag like any handle.
  console.log('\n=== Scenario 2b: TxnDetail — a grab-bar drag dismisses even while the sheet is scrolled ===')
  await openTxnDetail()
  {
    const box = await txnSheet.boundingBox()
    await touchDrag(cdp, { x: box.x + box.width / 2, yStart: box.y + box.height - 40, yEnd: box.y + box.height - 200 })
    await sleep(BETWEEN_GESTURES_MS)
    check((await txnSheet.evaluate((el) => el.scrollTop)) > 0, 'TxnDetail: sheet scrolled before the grab-bar drag')
    const grab = await page.locator('.acct-detail-grab').boundingBox()
    await touchDrag(cdp, { x: grab.x + grab.width / 2, yStart: grab.y + grab.height / 2, yEnd: grab.y + box.height + 200, steps: 30 })
    await txnSheet.waitFor({ state: 'detached', timeout: 3000 }).catch(() => {})
    check((await txnSheet.count()) === 0, 'TxnDetail: dragging the grab bar dismissed it while scrolled')
  }

  console.log('\n=== Scenario 3: Manage categories (PlanningModal .pl-modal / .pl-modal-body) ===')
  await page.getByRole('button', { name: 'More', exact: true }).click()
  await page.getByRole('button', { name: 'Plan', exact: true }).click()
  await page.waitForTimeout(400)
  // On a phone "Manage categories" sits in the page's overflow ActionMenu (itself a kebab
  // sheet), not as a standalone masthead button.
  const openManageCategories = async () => {
    const overflow = page.getByRole('button', { name: 'Plan settings' })
    await overflow.waitFor({ timeout: 10000 })
    await overflow.click()
    const item = page.getByRole('menuitem', { name: /Manage categories/i })
    await item.waitFor({ timeout: 5000 })
    await item.click()
    await page.locator('.kebab-sheet').waitFor({ state: 'detached', timeout: 3000 }).catch(() => {})
  }
  await openManageCategories()
  const catModal = page.locator('.pl-modal')
  await catModal.waitFor()
  await sleep(SETTLE_MS)
  // The scroll container here is the categories TABLE nested inside .pl-modal-body — a
  // scrollable region two levels below the sheet root, the "nested scroller" case.
  await runContract({ page, cdp, label: 'Manage categories', prefix: '3-managecats', sheet: catModal, body: page.locator('.pl-catmgr-table') })

  // Non-scrolling chrome drags regardless of scroll state: reopen, scroll, pull the handle.
  console.log('\n=== Scenario 4: a handle drag dismisses even while the body is scrolled ===')
  await openManageCategories()
  await catModal.waitFor()
  await sleep(SETTLE_MS)
  {
    const body = page.locator('.pl-catmgr-table')
    const bodyBox = await body.boundingBox()
    await touchDrag(cdp, { x: bodyBox.x + bodyBox.width / 2, yStart: bodyBox.y + bodyBox.height - 40, yEnd: bodyBox.y + bodyBox.height - 160 })
    await sleep(BETWEEN_GESTURES_MS)
    check((await body.evaluate((el) => el.scrollTop)) > 0, 'Manage categories: body scrolled before the handle drag')
    const modalBox = await catModal.boundingBox()
    await touchDrag(cdp, { x: modalBox.x + modalBox.width / 2, yStart: modalBox.y + 12, yEnd: modalBox.y + modalBox.height + 200, steps: 30 })
    await catModal.waitFor({ state: 'detached', timeout: 3000 }).catch(() => {})
    check((await catModal.count()) === 0, 'Manage categories: dragging the handle dismissed it while the body was scrolled')
  }

  check(consoleErrors.length === 0, `no console errors during any of the above (${consoleErrors.length} captured)`)
  if (consoleErrors.length) console.log('console errors:', consoleErrors)
  check(pageErrors.length === 0, `no uncaught page errors during any of the above (${pageErrors.length} captured)`)
  if (pageErrors.length) console.log('page errors:', pageErrors)

  await context.close()
} finally {
  await browser.close()
  app.stop()
}

console.log(`\nEvidence: ${outputDir}`)
if (failures.length > 0) {
  console.error(`\nsheet-drag-native-scroll-touch: FAIL (${failures.length} failure(s))`)
  for (const f of failures) console.error(` - ${f}`)
  process.exit(1)
}
console.log('\nsheet-drag-native-scroll-touch: PASS')
