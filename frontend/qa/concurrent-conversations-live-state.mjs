// concurrent-conversations-live-state.mjs — deterministic Playwright screenshot evidence for
// the concurrent-conversations "live dashboard" sidebar (acceptance E3/E6/E7, C2). See
// docs/qa/acceptance/concurrent-conversations.md and
// .claude/rfcs/concurrent-conversations-ui-spec.md.
//
//   node qa/concurrent-conversations-live-state.mjs            # self-boots a disposable instance
//   node qa/concurrent-conversations-live-state.mjs --base-url http://localhost:8090 --data-dir /tmp/x
//
// Why deterministic (no LLM) and how states are reached
// ------------------------------------------------------
// `make qa-e2e` (browser-use) is unusable right now — the Anthropic account is over its usage
// limit (resets 2026-09-01). This harness drives the REAL built app (frontend/dist) against a
// REAL disposable backend, and reaches each row state by seeding the exact stores/caches the
// backend and frontend already read from — never by faking a network response or a DOM node:
//
//   - `needs_you` / `done` / `failed` rows: `statusFor()` (frontend/src/state/luciusChat.tsx)
//     derives these from `GET /api/sessions`' `last_turn_status` field for a conversation this
//     browser has no local slice for (see resolveRowStatus's durable-fallback branch). That
//     field is written by `backend.ai.assistant.upsert_session_meta()` into
//     DATA_DIR/sessions/index.json — a plain JSON file. This script writes that file directly,
//     the same way the backend's own persistence does, then loads the app so the real
//     GET /api/sessions endpoint serves it and the real frontend renders it. What's under test
//     here is the RENDERING (does a bell glyph + "Needs you" actually paint, in priority order,
//     surviving the 5-item cap) — not whether the backend sets last_turn_status correctly,
//     which is a backend-lane concern proven elsewhere (this file's own acceptance evidence
//     section, tests/test_turn_supervisor.py, etc).
//   - the C2 pending chip: `Message.pending === 'queued'` is a plain field on a cached message
//     (frontend/src/utils/chatStore.ts, IndexedDB `lucius`/`conversations`). This script writes
//     a message with that field into the SAME IndexedDB store the app's own `loadMessages()`
//     reads, then reloads — the real MessageBubble/PendingChip renders it.
//   - `working`: NOT captured. It comes from `GET /api/sessions/live`, which is populated only
//     by `backend.ai.live_sessions`'s IN-PROCESS registry during a real turn — there is no JSON
//     file backing it, so it cannot be seeded without either a live model call or stubbing the
//     network response. The task brief is explicit that faking that would misrepresent what the
//     UI does, so this is an honest, reported gap — see docs/qa/acceptance/concurrent-conversations.md's
//     Evidence section.
//
// Safety: self-boots via qa/boot.mjs — disposable DATA_DIR/CACHE_DIR/LOGS_DIR under the system
// tmp dir, ONW_QA_MODE=1, never :8080. This script's own index.json writes target ONLY that
// disposable dataDir (asserted below), never the live ~/.open-net-worth tree.
//
// Output: qa/artifacts/concurrent-conversations-live-state/*.png (repo root, sibling to the
// existing qa/artifacts/statement-import-parity/ deterministic-capture convention).

import { chromium } from 'playwright'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { bootDisposableInstance, assertSafeBoot } from './boot.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const OUT_DIR = resolve(HERE, '../../qa/artifacts/concurrent-conversations-live-state')
mkdirSync(OUT_DIR, { recursive: true })

const argv = process.argv.slice(2)
const arg = (flag, def) => {
  const i = argv.indexOf(flag)
  return i >= 0 ? argv[i + 1] : def
}

const WIDTHS = [
  { label: '1440', w: 1440 },
  { label: '820', w: 820 },
  { label: '430', w: 430 },
]
const HEIGHT = 900

const ACTIVE_ID = 'qa_active_conv'

const results = []
const check = (name, ok, detail = '') => {
  results.push({ name, ok, detail })
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`)
}

function iso(minutesAgo) {
  return new Date(Date.now() - minutesAgo * 60_000).toISOString()
}

// Mirrors the shape backend.ai.assistant.upsert_session_meta() writes into
// DATA_DIR/sessions/index.json — see list_sessions()/GET /api/sessions.
function entry(id, title, minutesAgo, lastTurnStatus) {
  const at = iso(minutesAgo)
  const e = { id, title, created_at: at, updated_at: at, message_count: 4, model: '' }
  if (lastTurnStatus) {
    e.last_turn_status = lastTurnStatus
    e.last_turn_at = at
  }
  return e
}

const ACTIVE = entry(ACTIVE_ID, 'Rebalancing question', 0.05, undefined)

// Each scene rewrites the whole index — a fresh, self-contained sidebar state per shot rather
// than one big accreting dataset, so each screenshot is unambiguous about what it proves.
const SCENES = {
  'needs-you': [
    ACTIVE,
    entry('qa_needs_you', 'Statement reconcile', 2, 'awaiting_approval'),
    entry('qa_idle_yesterday_1', 'Grocery budget review', 60 * 26, undefined),
  ],
  done: [
    ACTIVE,
    entry('qa_done', 'Q3 rebalance plan', 3, 'ok'),
    entry('qa_idle_yesterday_2', 'Grocery budget review', 60 * 26, undefined),
  ],
  failed: [
    ACTIVE,
    entry('qa_failed', 'Tax scenario planning', 4, 'error'),
    entry('qa_idle_yesterday_3', 'Grocery budget review', 60 * 26, undefined),
  ],
  // Proves §5.1.1's two rules at once: needs_you sorts to the top of its bucket (rule 1) and
  // survives the 5-item cap (rule 2) despite being the OLDEST item in the set — without force-
  // inclusion, plain top-5-by-recency would be [ACTIVE, done, failed, idle_1, idle_2] and
  // needs_you (oldest, 20m) would be silently cut. idle_3..idle_6 ARE cut (expected/correct —
  // only needs_you/working are force-included, not done/failed).
  mixed: [
    ACTIVE,
    entry('qa_mixed_needs_you', 'Statement reconcile', 20, 'awaiting_approval'),
    entry('qa_mixed_done', 'Q3 rebalance plan', 1, 'ok'),
    entry('qa_mixed_failed', 'Tax scenario planning', 2, 'error'),
    entry('qa_mixed_idle_1', 'Grocery budget review', 3, undefined),
    entry('qa_mixed_idle_2', 'Vacation savings check-in', 4, undefined),
    entry('qa_mixed_idle_3', 'Mortgage refinance math', 5, undefined),
    entry('qa_mixed_idle_4', '1099 estimate', 6, undefined),
    entry('qa_mixed_idle_5', 'Emergency fund target', 7, undefined),
    entry('qa_mixed_idle_6', 'Dividend tax question', 8, undefined),
  ],
  'pending-chip': [ACTIVE],
}

function writeSessionIndex(dataDir, entries) {
  const dir = join(dataDir, 'sessions')
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'index.json'), JSON.stringify(entries))
}

async function newSeededContext(browser, width) {
  const ctx = await browser.newContext({ viewport: { width, height: HEIGHT } })
  await ctx.addInitScript((activeId) => {
    localStorage.setItem('chat-open', 'true')
    localStorage.setItem('lucius_active_conversation', activeId)
  }, ACTIVE_ID)
  const consoleErrors = []
  const page = await ctx.newPage()
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text())
  })
  page.on('pageerror', (e) => consoleErrors.push(String(e.message || e)))
  return { ctx, page, consoleErrors }
}

// Opens the Lucius panel (already open via the `chat-open` seed) and its conversation menu the
// way a real user does — clicking the hamburger — at whatever width the context was created
// with. Same component (ChatPanel.tsx) renders the floating side panel at >=640px and the
// full-tab surface below it (RouterShell's mobileNav==='lucius'), so this one path covers all
// three required widths with no special-casing.
async function openMenu(page, baseUrl) {
  await page.goto(baseUrl + '/', { waitUntil: 'domcontentloaded', timeout: 20000 })
  await page.waitForTimeout(1200)
  const burger = page.locator('.lx-side-burger')
  await burger.waitFor({ state: 'visible', timeout: 15000 })
  await burger.click()
  const menu = page.locator('.conv-menu')
  await menu.waitFor({ state: 'visible', timeout: 10000 })
  await page.waitForTimeout(250) // let the row status glyphs finish their mount paint
  return menu
}

async function overflowAndErrors(page, consoleErrors, label) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  )
  check(`${label}: no horizontal overflow`, overflow <= 1, `${overflow}px`)
  check(`${label}: no console errors`, consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '))
}

async function shootNeedsYou(browser, baseUrl, dataDir, widthLabel, width) {
  writeSessionIndex(dataDir, SCENES['needs-you'])
  const { ctx, page, consoleErrors } = await newSeededContext(browser, width)
  const label = `needs-you@${widthLabel}`
  try {
    const menu = await openMenu(page, baseUrl)
    const bell = menu.locator('.conv-row-status--needs-you')
    check(`${label}: bell glyph rendered (not a colour-only dot)`, (await bell.count()) === 1)
    check(`${label}: "Needs you" label visible`, (await menu.getByText('Needs you', { exact: true }).count()) >= 1)
    check(`${label}: names the conversation`, (await menu.getByText('Statement reconcile').count()) === 1)
    await overflowAndErrors(page, consoleErrors, label)
    await menu.screenshot({ path: join(OUT_DIR, `needs-you-${widthLabel}.png`) })
  } catch (e) {
    check(`${label}: scene loaded`, false, e.message)
  } finally {
    await ctx.close()
  }
}

async function shootDone(browser, baseUrl, dataDir, widthLabel, width) {
  writeSessionIndex(dataDir, SCENES.done)
  const { ctx, page, consoleErrors } = await newSeededContext(browser, width)
  const label = `done@${widthLabel}`
  try {
    const menu = await openMenu(page, baseUrl)
    check(`${label}: static "done" dot rendered`, (await menu.locator('.conv-row-status--done').count()) === 1)
    check(`${label}: "New reply" label visible`, (await menu.getByText('New reply', { exact: true }).count()) >= 1)
    check(`${label}: names the conversation`, (await menu.getByText('Q3 rebalance plan').count()) === 1)
    await overflowAndErrors(page, consoleErrors, label)
    await menu.screenshot({ path: join(OUT_DIR, `done-${widthLabel}.png`) })
  } catch (e) {
    check(`${label}: scene loaded`, false, e.message)
  } finally {
    await ctx.close()
  }
}

async function shootFailed(browser, baseUrl, dataDir, widthLabel, width) {
  writeSessionIndex(dataDir, SCENES.failed)
  const { ctx, page, consoleErrors } = await newSeededContext(browser, width)
  const label = `failed@${widthLabel}`
  try {
    const menu = await openMenu(page, baseUrl)
    const alert = menu.locator('.conv-row-status--failed')
    check(`${label}: alert glyph rendered (not a red dot)`, (await alert.count()) === 1)
    check(`${label}: "Failed" label visible`, (await menu.getByText('Failed', { exact: true }).count()) >= 1)
    check(`${label}: names the conversation`, (await menu.getByText('Tax scenario planning').count()) === 1)
    await overflowAndErrors(page, consoleErrors, label)
    await menu.screenshot({ path: join(OUT_DIR, `failed-${widthLabel}.png`) })
  } catch (e) {
    check(`${label}: scene loaded`, false, e.message)
  } finally {
    await ctx.close()
  }
}

async function shootMixed(browser, baseUrl, dataDir, widthLabel, width) {
  writeSessionIndex(dataDir, SCENES.mixed)
  const { ctx, page, consoleErrors } = await newSeededContext(browser, width)
  const label = `mixed@${widthLabel}`
  try {
    const menu = await openMenu(page, baseUrl)
    const rows = menu.locator('.conv-menu-sec:has-text("Chats") .conv-menu-row')
    const rowCount = await rows.count()
    check(`${label}: exactly 5 chat rows visible (cap holds)`, rowCount === 5, `saw ${rowCount}`)
    const firstRowIsNeedsYou =
      (await rows.first().locator('.conv-row-status--needs-you').count()) === 1
    check(
      `${label}: needs_you sorts FIRST despite being the oldest row (rule 1 + rule 2)`,
      firstRowIsNeedsYou,
    )
    const droppedText = await menu.getByText('Dividend tax question').count()
    check(`${label}: a boring older row (not needs_you) IS cut by the cap`, droppedText === 0)
    await overflowAndErrors(page, consoleErrors, label)
    await menu.screenshot({ path: join(OUT_DIR, `mixed-${widthLabel}.png`) })
  } catch (e) {
    check(`${label}: scene loaded`, false, e.message)
  } finally {
    await ctx.close()
  }
}

// C2 — seeds a message with pending:'queued' directly into the SAME IndexedDB store
// frontend/src/utils/chatStore.ts reads (loadMessages() prefers the local cache over a
// backend fetch). Written via page.evaluate AFTER the first load (not an initScript before
// navigation) so the write is guaranteed to land before the app's own hydration effect reads
// it — see the file header for why this is a legitimate rendering-only seed, not a fake.
async function shootPendingChip(browser, baseUrl, dataDir, widthLabel, width) {
  writeSessionIndex(dataDir, SCENES['pending-chip'])
  const { ctx, page, consoleErrors } = await newSeededContext(browser, width)
  const label = `pending-chip@${widthLabel}`
  try {
    await page.goto(baseUrl + '/', { waitUntil: 'domcontentloaded', timeout: 20000 })
    await page.waitForTimeout(1200)
    await page.evaluate(async (convId) => {
      const db = await new Promise((resolve_, reject) => {
        const req = indexedDB.open('lucius', 3)
        req.onupgradeneeded = () => {
          const d = req.result
          if (!d.objectStoreNames.contains('conversations')) d.createObjectStore('conversations')
        }
        req.onsuccess = () => resolve_(req.result)
        req.onerror = () => reject(req.error)
      })
      const messages = [
        {
          id: 'qa_pending_m1',
          role: 'assistant',
          text: 'Sure — give me a moment to pull your Roth contribution history.',
        },
        {
          id: 'qa_pending_m2',
          role: 'user',
          text: 'Also check the Roth conversion limit for 2026',
          pending: 'queued',
        },
      ]
      await new Promise((resolve_, reject) => {
        const tx = db.transaction('conversations', 'readwrite')
        tx.objectStore('conversations').put(messages, convId)
        tx.oncomplete = () => resolve_()
        tx.onerror = () => reject(tx.error)
      })
    }, ACTIVE_ID)
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 20000 })
    await page.waitForTimeout(1200)
    const panel = page.locator('.lx-side')
    await panel.waitFor({ state: 'visible', timeout: 15000 })
    const chip = page.locator('.msg-pending-chip')
    await chip.waitFor({ state: 'visible', timeout: 10000 })
    check(`${label}: pending chip rendered`, (await chip.count()) === 1)
    check(
      `${label}: exact copy — "Queued — Lucius will read this at its next step"`,
      (await chip.getByText('Queued — Lucius will read this at its next step').count()) === 1,
    )
    check(
      `${label}: the pending bubble is dimmed (msg-user-pending)`,
      (await page.locator('.msg-user-pending').count()) === 1,
    )
    await overflowAndErrors(page, consoleErrors, label)
    await panel.screenshot({ path: join(OUT_DIR, `pending-chip-${widthLabel}.png`) })
  } catch (e) {
    check(`${label}: scene loaded`, false, e.message)
  } finally {
    await ctx.close()
  }
}

async function main() {
  let booted = null
  let baseUrl = arg('--base-url')
  let dataDir = arg('--data-dir')
  if (!baseUrl) {
    booted = await bootDisposableInstance({ port: Number(arg('--port', '8098')) })
    baseUrl = booted.baseUrl
    dataDir = booted.dataDir
  }
  if (!dataDir) {
    throw new Error('--data-dir is required when --base-url is given without --boot')
  }
  assertSafeBoot({ port: 1, dataDir, cacheDir: dataDir, logDir: dataDir })

  console.log(`\n== concurrent-conversations live-state screenshots against ${baseUrl} ==`)
  console.log(`   writing to ${OUT_DIR}\n`)

  const browser = await chromium.launch()
  try {
    for (const { label, w } of WIDTHS) {
      await shootNeedsYou(browser, baseUrl, dataDir, label, w)
      await shootDone(browser, baseUrl, dataDir, label, w)
      await shootFailed(browser, baseUrl, dataDir, label, w)
      await shootMixed(browser, baseUrl, dataDir, label, w)
      await shootPendingChip(browser, baseUrl, dataDir, label, w)
    }
  } finally {
    await browser.close()
    if (booted) await booted.stop()
  }
}

main()
  .then(() => {
    const failed = results.filter((r) => !r.ok)
    console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
    console.log(
      `\nNOT captured: 'working' (needs a live model turn to populate GET /api/sessions/live's` +
        ` in-process registry — see the file header). Not represented as a screenshot or a` +
        ` PASS/FAIL row above; report this gap plainly rather than fake it.`,
    )
    process.exit(failed.length ? 1 : 0)
  })
  .catch((e) => {
    console.error('\nHARNESS ERROR:', e.message)
    process.exit(2)
  })
