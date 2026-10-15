// Browser-level check for SI-100: the Booked screen NAMES rows the ledger refused as
// duplicates.
//
//   node qa/import-refused-duplicates.mjs            # self-boots a disposable instance
//   node qa/import-refused-duplicates.mjs --base-url http://localhost:8090
//
// Why this exists as a browser check when a vitest already covers the component
// ------------------------------------------------------------------------------
// The vitest renders ImportModal in jsdom with a MOCKED onDecisionCommit, so it proves the
// component's logic and nothing about the two things only a real browser can answer:
//
//   1. Does the real AccountsTab mapper actually hand `refused_duplicates` to the modal?
//      jsdom tests inject the prop directly and skip that seam entirely.
//   2. Does the banner actually PAINT — non-zero box, inside the viewport, not clipped by
//      the Done screen's own overflow? "It's in the DOM" is not "the user can read it".
//
// Why the endpoints are stubbed rather than driven for real
// ---------------------------------------------------------
// Reaching the Done screen honestly needs a statement parse, which is an LLM call billed per
// run. Stubbing preview+commit at the network boundary keeps the ENTIRE client path real —
// the built bundle, the real fetch, the real mapper, the real CSS — while making the run free
// and deterministic. What is stubbed is precisely the part this criterion does not test.

import { chromium } from 'playwright'
import { bootDisposableInstance } from './boot.mjs'

const argv = process.argv.slice(2)
const arg = (flag) => {
  const i = argv.indexOf(flag)
  return i >= 0 ? argv[i + 1] : undefined
}

const STATEMENT_ID = 'stmt_qa_refused'
const REFUSED = [
  { date: '2026-06-05', description: 'ANTHROPIC SAN FRANCISCO USA', duplicate_of: 't_1', reason: 'exact match' },
  { date: '2026-06-06', description: 'GRAB A-9IOHTB HA NOI', duplicate_of: 't_2', reason: 'near duplicate' },
]

// A REAL one-page PDF. /api/upload is deliberately NOT stubbed — it parses the bytes, so
// fake ones fail there before the flow ever reaches the screen under test. Only the two
// import endpoints are stubbed; the upload path stays real.
const MINIMAL_PDF_B64 =
  'JVBERi0xLjcKJcK1wrYKCjEgMCBvYmoKPDwvVHlwZS9DYXRhbG9nL1BhZ2VzIDIgMCBSPj4KZW5kb2JqCgoyIDAgb2JqCjw8L1R5cGUvUGFnZXMvQ291bnQgMS9LaWRzWzQgMCBSXT4+CmVuZG9iagoKMyAwIG9iago8PC9Gb250PDwvaGVsdiA1IDAgUj4+Pj4KZW5kb2JqCgo0IDAgb2JqCjw8L1R5cGUvUGFnZS9NZWRpYUJveFswIDAgNTk1IDg0Ml0vUm90YXRlIDAvUmVzb3VyY2VzIDMgMCBSL1BhcmVudCAyIDAgUi9Db250ZW50c1s2IDAgUl0+PgplbmRvYmoKCjUgMCBvYmoKPDwvVHlwZS9Gb250L1N1YnR5cGUvVHlwZTEvQmFzZUZvbnQvSGVsdmV0aWNhL0VuY29kaW5nL1dpbkFuc2lFbmNvZGluZz4+CmVuZG9iagoKNiAwIG9iago8PC9MZW5ndGggMTA0L0ZpbHRlci9GbGF0ZURlY29kZT4+CnN0cmVhbQp42hXJOwrDQAyE4V6n0A2ix8hrQ0gRSJMuoM643MVFUrjx+SMzzfD9dNAzSVlqys24NeH80W3v35NVOQevdyh6AHMYRggWuEn4pSaYSuvBsZQ5RrW4qomrw7rP3h5bvumV9KE/ml8ZIQplbmRzdHJlYW0KZW5kb2JqCgp4cmVmCjAgNwowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwMTYgMDAwMDAgbiAKMDAwMDAwMDA2MiAwMDAwMCBuIAowMDAwMDAwMTE0IDAwMDAwIG4gCjAwMDAwMDAxNTUgMDAwMDAgbiAKMDAwMDAwMDI2MiAwMDAwMCBuIAowMDAwMDAwMzUxIDAwMDAwIG4gCgp0cmFpbGVyCjw8L1NpemUgNy9Sb290IDEgMCBSL0lEWzxDMkJGM0Q0M0MzOTYzMTNGMzI0MzJCQzM5MUMyQTEyMD48MTQzRTQxOUFGMzQ2MzI1MkVCREFGM0Y5MjQ1NkI0OTM+XT4+CnN0YXJ0eHJlZgo1MjQKJSVFT0YK'

const results = []
const check = (name, ok, detail = '') => {
  results.push({ name, ok, detail })
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`)
}

async function main() {
  let booted = null
  let baseUrl = arg('--base-url')
  if (!baseUrl) {
    booted = await bootDisposableInstance({ port: 8766 })
    baseUrl = booted.baseUrl
  }
  console.log(`\n== SI-100 browser check against ${baseUrl} ==\n`)

  const browser = await chromium.launch()
  // The app is a single-page shell with no server-side routes — the tab is seeded through
  // localStorage, exactly as scripts/visual-qa.mjs does it. Navigating to /accounts 404s.
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  await ctx.addInitScript(() => {
    localStorage.setItem('wealthTab', 'accounts')
    localStorage.setItem('chat-open', 'false')
  })
  const page = await ctx.newPage()
  const consoleErrors = []
  page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()))

  try {
    // Stub ONLY the two import endpoints. Everything else — the bundle, the mapper, the CSS —
    // is the real shipped app.
    await page.route('**/api/import/preview*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          statement_id: STATEMENT_ID,
          rows: [
            {
              date: '2026-06-05', amount: 14.87, direction: 'OUT', payee: 'Anthropic',
              description: 'ANTHROPIC SAN FRANCISCO USA', needs_eyes: false, dup: false,
              flags: [], row: 0, currency: 'SGD', kind: 'expense',
            },
          ],
          total_rows: 1, importable: 1, duplicates: 0, skipped: [], needs_eyes: 0,
          tier: 'text', meta: { currency: 'SGD' }, account_suggestion: null,
          reconcile: { status: 'ok' }, anomaly: {
            near_duplicate: 0, transfer_pair: 0, intra_batch_duplicate: 0,
            amount_anomaly: 0, flagged_rows: 0,
          },
          ledger_check: { period: { start: '2026-06-01', end: '2026-06-30' }, rows: [], boundary: [] },
        }),
      }),
    )
    await page.route('**/api/import/commit*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        // The exact shape backend/services/statements/decisions.py returns.
        body: JSON.stringify({
          created: 1, merged: 0, duplicates: 0, skipped: 0, transfers: 0,
          batch_id: 'imp_qa', statement_record_id: STATEMENT_ID, deleted: 0,
          merge_errors: [], delete_errors: [], refused_duplicates: REFUSED,
        }),
      }),
    )

    await page.goto(baseUrl + '/', { waitUntil: 'domcontentloaded', timeout: 20000 })
    await page.waitForTimeout(1200)

    const opened = await openImportModal(page)
    check('the Import modal opens from the Accounts tab', opened)
    if (!opened) return

    await uploadAndCommit(page)

    const done = page.locator('.imp3-done')
    await done.waitFor({ state: 'visible', timeout: 20000 })
    check('the Booked screen renders', await done.isVisible())

    // 1. The mapper seam: refused_duplicates must survive AccountsTab -> modal prop.
    const banner = page.locator('.imp3-done-facts .imp-heads', {
      hasText: 'already in your ledger',
    })
    const bannerCount = await banner.count()
    check('the refusal banner reached the DOM through the real mapper', bannerCount === 1,
      `found ${bannerCount}`)
    if (bannerCount !== 1) return

    const text = (await banner.first().innerText()).replace(/\s+/g, ' ')
    check('it states the count and the reason', /2 rows not imported — already in your ledger/.test(text), text.slice(0, 90))
    for (const r of REFUSED) {
      check(`it NAMES "${r.description.slice(0, 24)}…"`, text.includes(r.description))
      check(`it carries the date ${r.date}`, text.includes(r.date))
    }

    // 2. It actually PAINTS — the thing jsdom cannot answer.
    const box = await banner.first().boundingBox()
    check('the banner has a real, non-zero box', !!box && box.width > 50 && box.height > 5,
      box ? `${Math.round(box.width)}x${Math.round(box.height)}` : 'no box')
    const vp = page.viewportSize()
    check('it sits inside the viewport, not clipped away',
      !!box && box.x >= 0 && box.y >= 0 && box.x + box.width <= vp.width + 1,
      box ? `x=${Math.round(box.x)} right=${Math.round(box.x + box.width)} vw=${vp.width}` : '')

    // ...and is not painted over by something else (the Done screen is inside a modal).
    const onTop = await banner.first().evaluate((el) => {
      const r = el.getBoundingClientRect()
      const hit = document.elementFromPoint(r.left + Math.min(20, r.width / 2), r.top + r.height / 2)
      return !!hit && (el.contains(hit) || hit.contains(el))
    })
    check('nothing paints on top of it', onTop)

    // 3. No horizontal overflow introduced by a long refusal list.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    )
    check('the long refusal list causes no horizontal overflow', overflow <= 1, `${overflow}px`)

    check('no console errors during the flow', consoleErrors.length === 0,
      consoleErrors.slice(0, 2).join(' | '))

    await page.screenshot({ path: '/tmp/si100-booked-screen.png', fullPage: false })
    console.log('\n  screenshot: /tmp/si100-booked-screen.png')
  } finally {
    await browser.close()
    if (booted) await booted.stop()
  }
}

async function openImportModal(page) {
  // The Accounts overview masthead carries a plain "Import" button (Overview.tsx) which sets
  // importState and mounts ImportModal. Waiting on `.imp3-modal` rather than on the existence
  // of a file input: the page has one regardless, so that check passes without the modal.
  // Navigate the way a user does. The localStorage `wealthTab` seed that scripts/visual-qa.mjs
  // uses did not survive here (the app lands on Overview), and a nav click is the honest path
  // anyway — it proves the tab reaches the Import affordance from a cold load.
  await page.getByRole('button', { name: /^Accounts$/ }).first().click()
  await page.waitForTimeout(800)
  const btn = page.getByRole('button', { name: /^Import$/ }).first()
  await btn.waitFor({ state: 'visible', timeout: 20000 })
  await btn.click()
  const modal = page.locator('.imp3-modal, .imp3-wrap, [class^="imp3-"]').first()
  await modal.waitFor({ state: 'visible', timeout: 10000 })
  return (await page.locator('input[type="file"]').count()) > 0
}

async function uploadAndCommit(page) {
  // Scope to the modal's OWN input — the page carries other file inputs, and setting one of
  // those uploads the bytes (a 200) while the modal never leaves the Choose step.
  const modal = page.locator('[class*="imp3-modal"], [class*="imp3-wrap"], [role="dialog"]').first()
  const fileInput = modal.locator('input[type="file"]').first()
  await fileInput.setInputFiles({
    name: 'june.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from(MINIMAL_PDF_B64, 'base64'),
  })
  // "Read the statement" is disabled until a destination account is chosen
  // (`disabled={!acctIds.length}`), so pick the first one from the account popover.
  const pick = page.locator('.imp3-achip.add').first()
  await pick.waitFor({ state: 'visible', timeout: 15000 })
  await pick.click()
  await page.locator('.imp3-lpick-pop [role="option"]').first().click()

  const read = page.getByRole('button', { name: /read the statement/i }).first()
  await read.waitFor({ state: 'visible', timeout: 10000 })
  await read.click()
  const confirm = page.getByRole('button', { name: /^Confirm/ }).first()
  try {
    await confirm.waitFor({ state: 'visible', timeout: 15000 })
  } catch (e) {
    const dump = await page.locator('[class*="imp3-modal"], [class*="imp3-wrap"], [role="dialog"]').first().innerText().catch(() => '(no modal)')
    console.log('\n--- MODAL STATE WHEN Confirm WAS NOT FOUND ---\n' + dump.slice(0, 1200) + '\n---')
    await page.screenshot({ path: '/tmp/si100-debug.png' })
    throw e
  }
  await confirm.click()
}

main()
  .then(() => {
    const failed = results.filter((r) => !r.ok)
    console.log(`\n${results.length - failed.length}/${results.length} checks passed\n`)
    process.exit(failed.length ? 1 : 0)
  })
  .catch((e) => {
    console.error('\nHARNESS ERROR:', e.message)
    process.exit(2)
  })
