// txn-drawer-nested-modals.mjs — real-Chromium check of every dialog opened FROM the transaction
// drawer while the drawer itself sits over the review cards (owner report 2026-09-26, iPhone:
// tapping a receipt in review mode opened its viewer BEHIND the drawer, and the drawer then slid
// down when touched). Four things are checked, each against the served working tree:
//   1. stacking — the drawer paints over the review; a receipt viewer / delete confirm opened
//      from the drawer paints over the drawer;
//   2. gesture — dragging the receipt viewer never moves (or dismisses) the drawer behind it;
//   3. Escape — on desktop, Escape in the receipt viewer closes the viewer, not the drawer too;
//   4. host dialogs — the delete confirm and add-category modal, rendered BESIDE the drawer,
//      stack on it without closing it: Cancel returns to the drawer, an edit survives, and a
//      create the server refuses is said in the drawer (these surfaces show no toast bar).
//
// Runs the disposable backend (throwaway data dir, demo seed) plus the Vite DEV server proxied to
// it, so the working tree is what's tested and `frontend/dist` — which a live instance may be
// serving — is never rebuilt. Usage: `node qa/txn-drawer-nested-modals.mjs` from frontend/.
import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync } from 'node:fs'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { bootDisposableInstance } from './boot.mjs'

function portIsFree(port) {
  return new Promise((resolve) => {
    const probe = createServer()
    probe.once('error', () => resolve(false))
    probe.listen(port, '127.0.0.1', () => probe.close(() => resolve(true)))
  })
}

/** Free ports in the QA range — other sessions run their own disposable instances there too. */
async function freeQaPorts(count) {
  const found = []
  for (let port = 8090; port <= 8099 && found.length < count; port++) {
    if (await portIsFree(port)) found.push(port)
  }
  if (found.length < count) throw new Error(`need ${count} free ports in 8090–8099`)
  return found
}

const outputDir = process.env.ONW_EVIDENCE || '/tmp/onw-txn-drawer-nested-modals'
const [apiPort, webPort] = process.env.ONW_PORT
  ? [Number(process.env.ONW_PORT), Number(process.env.ONW_PORT) + 1]
  : await freeQaPorts(2)
mkdirSync(outputDir, { recursive: true })
const dataDir = mkdtempSync(join(tmpdir(), 'onw-drawer-nested-data-'))
const frontendDir = join(dirname(fileURLToPath(import.meta.url)), '..')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const failures = []
const pageErrors = []
function check(cond, message) {
  if (!cond) failures.push(message)
  console.log(`${cond ? 'PASS' : 'FAIL'} — ${message}`)
}

async function startViteDev(apiBase) {
  const child = spawn(
    'npx',
    ['vite', '--port', String(webPort), '--strictPort', '--host', '127.0.0.1'],
    {
      cwd: frontendDir,
      env: { ...process.env, VITE_DEV_API_TARGET: apiBase },
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: true,
    },
  )
  child.stderr.on('data', (d) => process.stderr.write(`[vite] ${d}`))
  let exited = false
  child.on('exit', () => (exited = true))
  const stop = () => {
    try {
      process.kill(-child.pid, 'SIGTERM')
    } catch {
      /* already gone */
    }
  }
  const url = `http://127.0.0.1:${webPort}`
  for (let i = 0; i < 120 && !exited; i++) {
    try {
      // OUR dev server, not whatever else answers on the port: it serves the Vite client.
      const res = await fetch(url)
      if (res.ok && (await res.text()).includes('/@vite/client')) return { url, stop }
    } catch {
      /* not up yet */
    }
    await sleep(500)
  }
  stop()
  throw new Error(`vite dev server did not come up on ${webPort}${exited ? ' (it exited)' : ''}`)
}

/** One demo expense, flagged for review with a receipt attached — the owner's exact row shape. */
async function prepareRow(apiBase, receiptPng) {
  const res = await fetch(`${apiBase}/api/transactions?kind=expense&limit=5`)
  const body = await res.json()
  const rows = Array.isArray(body) ? body : (body.transactions ?? body.items ?? [])
  const txn = rows[0]
  if (!txn) throw new Error(`no demo expense to review: ${JSON.stringify(body).slice(0, 200)}`)
  const put = await fetch(`${apiBase}/api/transactions/${txn.id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ needs_review: true }),
  })
  if (!put.ok) throw new Error(`flagging ${txn.id} failed: ${put.status} ${await put.text()}`)
  const form = new FormData()
  form.append('file', new Blob([receiptPng], { type: 'image/png' }), 'receipt.png')
  const up = await fetch(`${apiBase}/api/transactions/${txn.id}/receipts/upload`, {
    method: 'POST',
    body: form,
  })
  if (!up.ok) throw new Error(`receipt upload failed: ${up.status} ${await up.text()}`)
  const dup = await fetch(`${apiBase}/api/categories`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label: 'Duplicate probe', kind: 'expense', color: '#3b86dd' }),
  })
  if (!dup.ok) throw new Error(`seeding a category failed: ${dup.status} ${await dup.text()}`)
  return txn.id
}

/** Which surface PAINTS topmost at (x, y). Radix switches pointer events off on every layer but
 *  the top modal, and elementFromPoint skips `pointer-events: none` — so hit-testing is forced
 *  on for the probe and restored straight after. Returns 'drawer', a dialog's title, or a tag. */
function paintedAt(page, x, y) {
  return page.evaluate(
    ({ x, y }) => {
      const style = document.createElement('style')
      style.textContent = '* { pointer-events: auto !important; }'
      document.head.appendChild(style)
      const el = document.elementFromPoint(x, y)
      style.remove()
      if (!el) return 'nothing'
      if (el.closest('.acct-detail-wrap')) return 'drawer'
      const card = el.closest('.ui-modal-card')
      if (card)
        return card.querySelector('.ui-modal__title')?.textContent?.trim() || 'untitled modal'
      return (
        el.tagName.toLowerCase() + (el.className ? '.' + String(el.className).split(' ')[0] : '')
      )
    },
    { x, y },
  )
}

async function centreOf(locator) {
  const box = await locator.boundingBox()
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

// The Accounts tab's "Start review" — the whole flagged set. (`?review=transactions` opens the
// automation-only queue, which a row flagged by hand is not in.)
async function openDrawerFromReview(page, baseUrl, { phone }) {
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' })
  try {
    await page
      .getByRole('button', { name: 'Accounts', exact: true })
      .or(page.getByRole('link', { name: 'Accounts', exact: true }))
      .first()
      .click({ timeout: 60_000 })
    await page.locator('.acct-recon-start').first().click({ timeout: 60_000 })
    await page.getByRole('button', { name: 'Details' }).first().click({ timeout: 30_000 })
  } catch (e) {
    await page.screenshot({
      path: join(outputDir, `failed-to-open-review-${phone ? 'phone' : 'desktop'}.png`),
    })
    throw e
  }
  await page.locator('.acct-detail-wrap').waitFor()
  await page.locator('.acct-detail-receipt-name').first().waitFor({ timeout: 15_000 })
  await sleep(500)
}

async function openReceipt(page) {
  await page.locator('.acct-detail-receipt-name').first().click()
  await page.locator('.ui-modal__title', { hasText: 'receipt.png' }).waitFor()
  await sleep(600)
}

const app = await bootDisposableInstance({ port: apiPort, dataDir })
let browser = null
let vite = null
try {
  browser = await chromium.launch({ headless: true })
  await fetch(`${app.baseUrl}/api/demo/seed`, { method: 'POST' })
  vite = await startViteDev(app.baseUrl)

  const scratch = await browser.newPage()
  await scratch.setContent(
    '<div id="r" style="width:280px;height:380px;background:#fff;color:#111;font:18px monospace;padding:18px">7-ELEVEN<br>RECEIPT<br><br>TOTAL 63.80</div>',
  )
  const receiptPng = await scratch.locator('#r').screenshot()
  await scratch.close()
  const txnId = await prepareRow(app.baseUrl, receiptPng)
  console.log(`[setup] review row ${txnId} with receipt.png`)

  // ── Phone: stacking + gesture ────────────────────────────────────────────────
  const phone = await browser.newContext({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
  })
  const page = await phone.newPage()
  page.on('pageerror', (e) => pageErrors.push(String(e)))
  await openDrawerFromReview(page, vite.url, { phone: true })
  const sheet = page.locator('.acct-detail.sheet')
  const drawerCentre = await centreOf(sheet)
  check(
    (await paintedAt(page, drawerCentre.x, drawerCentre.y)) === 'drawer',
    'the drawer paints over the review cards it was opened from',
  )
  await page.screenshot({ path: join(outputDir, 'phone-1-drawer.png') })

  await openReceipt(page)
  const viewerCard = page.locator('.ui-modal-card', {
    has: page.locator('.ui-modal__title', { hasText: 'receipt.png' }),
  })
  const viewerCentre = await centreOf(viewerCard)
  const onTop = await paintedAt(page, viewerCentre.x, viewerCentre.y)
  check(onTop === 'receipt.png', `the receipt viewer paints over the drawer (topmost: ${onTop})`)
  await page.screenshot({ path: join(outputDir, 'phone-2-receipt.png') })

  // Drag the viewer down by its title row, the way a thumb dismisses a sheet: far enough to
  // commit on distance alone (DISMISS_DISTANCE_RATIO of its height), with the drawer's own
  // offset read partway, while the finger is still down.
  const title = await centreOf(viewerCard.locator('.ui-modal__head'))
  const pull = Math.ceil((await viewerCard.boundingBox()).height * 0.6)
  const cdp = await phone.newCDPSession(page)
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x: title.x, y: title.y }],
  })
  let drawerShift = null
  for (let dy = 10; dy <= pull; dy += 10) {
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x: title.x, y: title.y + dy }],
    })
    await sleep(16)
    if (drawerShift === null && dy >= 200) {
      drawerShift = await sheet.evaluate((el) => el.style.transform)
      await page.screenshot({ path: join(outputDir, 'phone-3-mid-drag.png') })
    }
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await sleep(700)
  check(
    !drawerShift || drawerShift === 'none',
    `dragging the receipt viewer leaves the drawer where it is (drawer transform: "${drawerShift}")`,
  )
  check(
    (await page.locator('.acct-detail-wrap').count()) === 1,
    'the drawer is still open after the receipt viewer is dragged away',
  )
  const viewerLeft =
    (await page.locator('.ui-modal__title', { hasText: 'receipt.png' }).count()) === 0
  check(viewerLeft, 'the drag dismissed the receipt viewer itself')
  await page.screenshot({ path: join(outputDir, 'phone-4-after-drag.png') })
  if (!viewerLeft) await viewerCard.locator('.ui-modal__close').click({ force: true })

  // A dialog the HOST opens for the drawer (not a React child of it): the delete confirm.
  if ((await page.locator('.acct-detail-wrap').count()) === 0)
    await openDrawerFromReview(page, vite.url, { phone: true })
  await page.locator('.acct-detail-del').click()
  const confirmCard = page.locator('.ui-modal-card', {
    has: page.locator('.ui-modal__title', { hasText: 'Delete transaction' }),
  })
  await confirmCard.waitFor()
  await sleep(500)
  const confirmCentre = await centreOf(confirmCard)
  const confirmTop = await paintedAt(page, confirmCentre.x, confirmCentre.y)
  check(
    confirmTop === 'Delete transaction',
    `the delete confirm is the topmost surface (topmost: ${confirmTop})`,
  )
  // The host renders this confirm BESIDE the drawer, not inside it; the drawer is a non-modal
  // Radix dialog, whose default closed it on the focus the confirm takes as it opens.
  check(
    (await page.locator('.acct-detail-wrap').count()) === 1,
    'the drawer stays open under the delete confirm',
  )
  await page.screenshot({ path: join(outputDir, 'phone-5-delete-confirm.png') })
  await confirmCard.getByRole('button', { name: 'Cancel' }).click()
  await sleep(500)
  const backTop = await paintedAt(page, drawerCentre.x, drawerCentre.y)
  check(backTop === 'drawer', `Cancel on the confirm returns to the drawer (topmost: ${backTop})`)

  // The host's add-category modal, from the drawer's own edit form: the edit must survive it.
  if ((await page.locator('.acct-detail-wrap').count()) === 0)
    await openDrawerFromReview(page, vite.url, { phone: true })
  await page.locator('.acct-detail-edit').click()
  await page.locator('.acct-detail-kicker', { hasText: 'Edit transaction' }).waitFor()
  await page
    .locator('.acct-edit-field', { has: page.locator('.acct-edit-label', { hasText: 'Category' }) })
    .locator('.acct-acctsel-btn')
    .click()
  await page.getByPlaceholder('Search categories…').fill('Stacked dialog probe')
  await page.getByRole('button', { name: /New category/ }).dispatchEvent('mousedown')
  const catCard = page.locator('.ui-modal-card', {
    has: page.locator('.ui-modal__title', { hasText: 'Add category' }),
  })
  await catCard.waitFor()
  await sleep(500)
  const catCentre = await centreOf(catCard)
  const catTop = await paintedAt(page, catCentre.x, catCentre.y)
  check(
    catTop === 'Add category',
    `the add-category modal is the topmost surface (topmost: ${catTop})`,
  )
  check(
    (await page.locator('.acct-detail-kicker', { hasText: 'Edit transaction' }).count()) === 1,
    'the drawer stays in its edit form under the add-category modal',
  )
  await page.screenshot({ path: join(outputDir, 'phone-6-add-category.png') })
  const created = page.waitForResponse(
    (r) => r.url().endsWith('/api/categories') && r.request().method() === 'POST',
  )
  await catCard.getByRole('button', { name: 'Save' }).click()
  const createdRes = await created
  console.log(
    `INFO — POST /api/categories → ${createdRes.status()} ${(await createdRes.text()).slice(0, 300)}`,
  )
  await page
    .locator('.acct-acctsel-btn', { hasText: 'Stacked dialog probe' })
    .waitFor({ timeout: 10_000 })
    .catch(() => {})
  check(
    (await page.locator('.acct-acctsel-btn', { hasText: 'Stacked dialog probe' }).count()) === 1 &&
      (await page.locator('.acct-detail-kicker', { hasText: 'Edit transaction' }).count()) === 1,
    'the new category lands in the edit that asked for it',
  )
  await page.screenshot({ path: join(outputDir, 'phone-7-category-landed.png') })

  // A refused create — the server's 409 for a name that exists — is said in the drawer as the
  // server's sentence, not a JSON envelope; these surfaces show no toast bar.
  await page
    .locator('.acct-edit-field', { has: page.locator('.acct-edit-label', { hasText: 'Category' }) })
    .locator('.acct-acctsel-btn')
    .click()
  await page.getByPlaceholder('Search categories…').fill('Duplicate probe')
  await page.getByRole('button', { name: /New category/ }).dispatchEvent('mousedown')
  await catCard.waitFor()
  const refusedRes = page.waitForResponse(
    (r) => r.url().endsWith('/api/categories') && r.request().method() === 'POST',
  )
  await catCard.getByRole('button', { name: 'Save' }).click()
  console.log(`INFO — POST /api/categories (duplicate) → ${(await refusedRes).status()}`)
  const saidIn = page.locator('.acct-detail-wrap [role="alert"]')
  await saidIn.waitFor({ timeout: 10_000 }).catch(() => {})
  const said = (await saidIn.count()) ? await saidIn.first().textContent() : null
  check(
    said === "A category named 'Duplicate probe' already exists",
    `a refused category create is said in the drawer (said: ${JSON.stringify(said)})`,
  )
  check((await page.locator('.acct-toast').count()) === 0, 'no toast bar under the drawer')
  await page.screenshot({ path: join(outputDir, 'phone-8-refused-create.png') })
  await phone.close()

  // ── Desktop: Escape ──────────────────────────────────────────────────────────
  const desk = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  const dpage = await desk.newPage()
  dpage.on('pageerror', (e) => pageErrors.push(String(e)))
  await openDrawerFromReview(dpage, vite.url, { phone: false })
  await openReceipt(dpage)
  const deskCard = dpage.locator('.ui-modal-card', {
    has: dpage.locator('.ui-modal__title', { hasText: 'receipt.png' }),
  })
  const deskCentre = await centreOf(deskCard)
  const deskTop = await paintedAt(dpage, deskCentre.x, deskCentre.y)
  check(
    deskTop === 'receipt.png',
    `desktop: the receipt viewer paints over the drawer (topmost: ${deskTop})`,
  )
  await dpage.screenshot({ path: join(outputDir, 'desktop-1-receipt.png') })
  await dpage.keyboard.press('Escape')
  await sleep(500)
  check(
    (await dpage.locator('.ui-modal__title', { hasText: 'receipt.png' }).count()) === 0,
    'desktop: Escape closes the receipt viewer',
  )
  check(
    (await dpage.locator('.acct-detail-wrap').count()) === 1,
    'desktop: the same Escape leaves the drawer open',
  )
  await dpage.screenshot({ path: join(outputDir, 'desktop-2-after-escape.png') })
  await desk.close()

  check(pageErrors.length === 0, `no page errors (${pageErrors.length})`)
  if (pageErrors.length) console.log(pageErrors.slice(0, 5).join('\n'))
} finally {
  await browser?.close().catch(() => {})
  vite?.stop()
  app.stop()
}

console.log(`\nEvidence: ${outputDir}`)
if (failures.length > 0) {
  console.error(`\ntxn-drawer-nested-modals: FAIL (${failures.length})`)
  process.exit(1)
}
console.log('\ntxn-drawer-nested-modals: PASS')
