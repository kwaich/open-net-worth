// pl-catmgr-table-scroll-touch.mjs — REAL touch-emulated verification that the categories
// table INSIDE Manage categories (a nested scrollable region, .pl-catmgr-table, independent
// from .pl-modal-body) scrolls natively on BOTH axes under a real touch pipeline — a sideways
// pan at the top included — and that the modal around it still drag-dismisses from its own
// chrome.
import { mkdirSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { chromium } from 'playwright'
import { bootDisposableInstance } from './boot.mjs'

const outputDir = process.env.ONW_EVIDENCE || '/tmp/onw-catmgr-table-touch'
const port = Number(process.env.ONW_PORT || 8796)
mkdirSync(outputDir, { recursive: true })
const emptyDataDir = mkdtempSync(join(tmpdir(), 'onw-catmgr-data-'))
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

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

// A sideways pan with a real finger's vertical wobble: every step lands ±2px off the line,
// the FIRST one downward — the exact shape that could read as "pull from the top" if the
// hook judged the axis per event instead of per gesture.
async function touchPan(cdp, { xStart, xEnd, y, steps = 20, stepDelayMs = 16 }) {
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: xStart, y, id: 1 }] })
  await sleep(60)
  for (let i = 1; i <= steps; i += 1) {
    const x = xStart + (xEnd - xStart) * (i / steps)
    const wobble = i % 2 === 1 ? 2 : -2
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y: y + wobble, id: 1 }] })
    await sleep(stepDelayMs)
  }
  await sleep(60)
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await sleep(60)
}

const failures = []
function check(cond, message) {
  if (!cond) failures.push(message)
  console.log(`${cond ? 'PASS' : 'FAIL'} — ${message}`)
}

const app = await bootDisposableInstance({ port, dataDir: emptyDataDir })
const browser = await chromium.launch({ headless: true })

try {
  const seedRes = await fetch(`${app.baseUrl}/api/demo/seed`, { method: 'POST' })
  if (!seedRes.ok) throw new Error(`demo seed failed: ${seedRes.status}`)

  const context = await browser.newContext({ viewport: { width: 390, height: 760 }, hasTouch: true, isMobile: true })
  const page = await context.newPage()
  const cdp = await context.newCDPSession(page)
  await page.goto(app.baseUrl, { waitUntil: 'domcontentloaded' })

  await page.getByRole('button', { name: 'More', exact: true }).click()
  await page.getByRole('button', { name: 'Plan', exact: true }).click()
  await page.waitForTimeout(400)
  // On a phone "Manage categories" sits in the page's overflow ActionMenu (a kebab sheet).
  await page.getByRole('button', { name: 'Plan settings' }).click()
  await page.getByRole('menuitem', { name: /Manage categories/i }).click()
  const table = page.locator('.pl-catmgr-table')
  await table.waitFor()
  // The rows arrive async after the modal mounts — wait for the content, not a fixed delay.
  await page.locator('.pl-catmgr-row:not(.pl-catmgr-addrow)').nth(10).waitFor({ timeout: 10_000 })
  await page.waitForTimeout(450)

  const scrollable = await table.evaluate((el) => el.scrollHeight > el.clientHeight)
  check(scrollable, `.pl-catmgr-table has scrollable content (scrollHeight > clientHeight) — demo seeded ${await table.evaluate((el) => el.scrollHeight)}px of content`)

  const box = await table.boundingBox()
  const wide = await table.evaluate((el) => el.scrollWidth > el.clientWidth)
  check(wide, `.pl-catmgr-table overflows sideways on a phone (scrollWidth ${await table.evaluate((el) => el.scrollWidth)} > clientWidth ${await table.evaluate((el) => el.clientWidth)})`)

  // A sideways pan at scrollTop 0 — the one state where a stray downward pixel could be
  // mistaken for "pull from the top" — must scroll the table horizontally, not drag the modal.
  await touchPan(cdp, { xStart: box.x + box.width - 30, xEnd: box.x + 30, y: box.y + box.height / 2 })
  const scrollLeftAfter = await table.evaluate((el) => el.scrollLeft)
  check(scrollLeftAfter > 0, `a sideways pan (with vertical wobble) at the top scrolled the table horizontally (scrollLeft=${scrollLeftAfter})`)
  check((await table.evaluate((el) => el.scrollTop)) === 0, 'the sideways pan did not scroll the table vertically')
  const modalTransformAfterPan = await page.locator('.pl-modal').evaluate((el) => el.style.transform)
  check(modalTransformAfterPan === '', `the sideways pan never moved the modal (inline transform "${modalTransformAfterPan}")`)
  await page.screenshot({ path: join(outputDir, 'catmgr-table-panned.png') })

  // Drag starting INSIDE the table, a modest distance — the browser scrolls it (the sheet root's
  // `touch-action: none` does not reach a descendant scroll container), the modal stays put.
  await touchDrag(cdp, { x: box.x + box.width / 2, yStart: box.y + box.height - 30, yEnd: box.y + box.height - 130 })
  const scrollTopAfter = await table.evaluate((el) => el.scrollTop)
  check(scrollTopAfter > 0, `dragging inside the table scrolled it cleanly (scrollTop=${scrollTopAfter})`)
  check((await table.count()) === 1, 'table (and modal) still present after the scroll drag')
  await page.screenshot({ path: join(outputDir, 'catmgr-table-scrolled.png') })

  // Confirm the modal itself is still fully functional: a continuous drag from the modal's own
  // top area (not inside the table) still dismisses it normally.
  const modal = page.locator('.pl-modal')
  const modalBox = await modal.boundingBox()
  await touchDrag(cdp, { x: modalBox.x + modalBox.width / 2, yStart: modalBox.y + 40, yEnd: modalBox.y + modalBox.height + 200, steps: 30 })
  await modal.waitFor({ state: 'detached', timeout: 3000 }).catch(() => {})
  check((await modal.count()) === 0, 'the modal still dismisses normally via a drag from outside the table')

  await context.close()
} finally {
  await browser.close()
  app.stop()
}

console.log(`\nEvidence: ${outputDir}`)
if (failures.length > 0) {
  console.error(`\npl-catmgr-table-scroll-touch: FAIL (${failures.length})`)
  process.exit(1)
}
console.log('\npl-catmgr-table-scroll-touch: PASS')
