// concurrent-conversations-live-turn.mjs — closes the last three unproven acceptance
// criteria for concurrent conversations: E1 (live indicator, cross-tab), E2 (it clears on
// both success and error), E4 (the mobile Lucius tab badges after a background completion).
// See docs/qa/acceptance/concurrent-conversations.md.
//
//   cd frontend && node qa/concurrent-conversations-live-turn.mjs
//
// Why this script exists and how it differs from concurrent-conversations-live-state.mjs
// -----------------------------------------------------------------------------------------
// That script proved everything reachable by SEEDING backend/frontend storage directly
// (DATA_DIR/sessions/index.json, IndexedDB) and loading the real app on top of it — honest,
// but by its own header it could not touch `working`: GET /api/sessions/live's `working`
// state lives ONLY in backend.ai.live_sessions's in-process registry while a turn genuinely
// executes. There is no file or IndexedDB backing to seed. So E1/E2/E4 need an ACTUAL turn.
//
// The Anthropic account is over its usage limit until 2026-09-01 — any approach that calls a
// paid provider fails. So this drives a REAL turn against a tiny local OpenAI-compatible mock
// (./mock-llm-server.mjs, SSE, deliberately slow, includes one real tool-call round) reached
// via the app's own `openai_compatible` transport with a caller-supplied `base_url`
// (backend/db/model_config_store.py). Zero network egress, zero cost, fully deterministic.
//
// Safety: self-boots via qa/boot.mjs (disposable DATA_DIR/CACHE_DIR/LOGS_DIR, ONW_QA_MODE=1,
// never :8080) exactly like every other self-booting script here. The mock server binds
// 127.0.0.1 only. model_config.json is written directly into the disposable DATA_DIR the same
// way concurrent-conversations-live-state.mjs writes sessions/index.json — never the live
// ~/.open-net-worth tree (assertSafeBoot below asserts this before anything runs).
//
// Output: qa/artifacts/concurrent-conversations-live-state/live-*.png — the SAME evidence
// directory the other script uses (the task calls for "alongside the 15 already there"),
// filenames prefixed `live-` so the two scripts' outputs are never confused for one another.
//
// E4 is only PARTIALLY provable as specified, and this script says so honestly rather than
// papering over it (see watchMobileE4): the spinner half (a background turn is running while
// you sit on another tab) genuinely updates live, polled every 4s exactly like the sidebar
// indicator. The BADGE half (that background turn having FINISHED) does not update live in
// the current app — frontend/src/hooks/useConversations.ts fetches GET /api/sessions exactly
// once, at mount, with no periodic refetch and no bus event that wakes it — so a background
// completion this browser never locally touched is invisible until the next reload. This
// script proves that distinction empirically rather than asserting it from reading the code:
// it drives a real isolated turn, watches live for up to 25s with nothing else contaminating
// the check, and then reloads to show the underlying data was correct all along.

import { chromium } from 'playwright'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { bootDisposableInstance, assertSafeBoot } from './boot.mjs'
import { startMockLlmServer, SCENARIO_MARKER, FAIL_MARKER } from './mock-llm-server.mjs'

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

const SUCCESS_TEXT = `${SCENARIO_MARKER}OK check my portfolio value please`
const FAIL_TEXT = `${FAIL_MARKER} check my portfolio value please`
const E4_TEXT = `${SCENARIO_MARKER}OK for the mobile badge check please`

const results = []
const check = (name, ok, detail = '') => {
  results.push({ name, ok, detail })
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`)
}
// Diagnostics that are neither a scripted PASS nor a product-code FAIL — an honest, named
// finding about what the current app does, recorded separately so it is never confused with
// "the harness broke" or silently dropped.
const findings = []
const note = (text) => {
  findings.push(text)
  console.log(`  NOTE  ${text}`)
}

function writeModelConfig(dataDir, mockOrigin) {
  const baseUrl = `${mockOrigin}/v1`
  const cfg = {
    provider: 'openai_compatible',
    model: 'qa-mock-model',
    base_url: baseUrl,
    providers: {
      custom: { api_key: 'sk-qa-mock-dummy', base_url: baseUrl, model: 'qa-mock-model' },
    },
  }
  writeFileSync(join(dataDir, 'model_config.json'), JSON.stringify(cfg, null, 2))
}

async function newContext(browser, width, { activeId, chatOpen } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height: HEIGHT } })
  await ctx.addInitScript(
    ({ activeId, chatOpen }) => {
      if (chatOpen !== undefined) localStorage.setItem('chat-open', String(chatOpen))
      if (activeId) localStorage.setItem('lucius_active_conversation', activeId)
    },
    { activeId, chatOpen },
  )
  const consoleErrors = []
  const page = await ctx.newPage()
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text())
  })
  page.on('pageerror', (e) => consoleErrors.push(String(e.message || e)))
  return { ctx, page, consoleErrors }
}

async function overflowAndErrors(page, consoleErrors, label) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  check(`${label}: no horizontal overflow`, overflow <= 1, `${overflow}px`)
  check(`${label}: no console errors`, consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '))
}

// Opens the Lucius panel (chat-open seeded true) and its conversation menu — same path
// concurrent-conversations-live-state.mjs uses, at whatever width the context was created
// with (>=640px renders the floating side panel; <640px the full-tab surface; same component).
async function openMenu(page, baseUrl) {
  await page.goto(baseUrl + '/', { waitUntil: 'domcontentloaded', timeout: 20000 })
  await page.waitForTimeout(1200)
  const burger = page.locator('.lx-side-burger')
  await burger.waitFor({ state: 'visible', timeout: 15000 })
  await burger.click()
  const menu = page.locator('.conv-menu')
  await menu.waitFor({ state: 'visible', timeout: 10000 })
  await page.waitForTimeout(250)
  return menu
}

// Sender: load the panel, mint a definitely-fresh conversation (never rely on what got
// auto-selected at boot), then type + send. Returns the conversation id captured the instant
// it is minted (createConversation() stamps localStorage synchronously, well before the
// message itself is sent) so the caller can hand it to watcher contexts with no race.
async function sendFreshMessage(page, baseUrl, text) {
  await page.goto(baseUrl + '/', { waitUntil: 'domcontentloaded', timeout: 20000 })
  await page.waitForTimeout(1200)
  const newBtn = page.locator('[aria-label="New conversation"]')
  await newBtn.waitFor({ state: 'visible', timeout: 15000 })
  await newBtn.click()
  await page.waitForTimeout(200)
  const convId = await page.evaluate(() => localStorage.getItem('lucius_active_conversation'))
  const composer = page.locator('.chat-composer')
  await composer.waitFor({ state: 'visible', timeout: 10000 })
  await composer.click()
  await page.keyboard.type(text, { delay: 12 })
  await page.keyboard.press('Enter')
  // Let POST /api/chat land (202) before this tab is torn down — spawn() is synchronous
  // server-side before the 202 write, so the live episode is already open by then; this
  // is slack for the client round trip, not something the turn itself depends on.
  await page.waitForTimeout(800)
  return convId
}

function rowByTitle(container, titleText) {
  return container.locator('.conv-menu-row', { hasText: titleText })
}

// E1 (working, cross-tab + cross-width) and the live half of E2 (clears with no reload) in
// one pass per width — the same mounted page proves both: it never sent this turn's message
// (a fresh context seeded onto the DECOY conversation, never this one), so a `working` glyph
// on this row can only have come from the global GET /api/sessions/live poll
// (frontend/src/state/luciusChat.tsx), and its disappearance a few seconds later — WITHOUT a
// reload — can only be that same poll noticing the turn ended.
async function watchSidebarWorkingThenCleared(browser, baseUrl, decoyId, titleText, widthLabel, width, tag) {
  const { ctx, page, consoleErrors } = await newContext(browser, width, { activeId: decoyId, chatOpen: true })
  const label = `${tag}@${widthLabel}`
  try {
    const menu = await openMenu(page, baseUrl)
    const row = rowByTitle(menu, titleText)
    await row.waitFor({ state: 'visible', timeout: 20000 })

    try {
      const workingCount = await row.locator('.conv-row-status--working').count()
      check(`${label}: sidebar shows WORKING for a turn this tab never sent`, workingCount === 1)
      check(
        `${label}: "Working…" label visible`,
        (await row.getByText('Working…', { exact: true }).count()) === 1,
      )
      await overflowAndErrors(page, consoleErrors, label)
      await menu.screenshot({ path: join(OUT_DIR, `live-e1-working-${tag}-${widthLabel}.png`) })
    } catch (e) {
      check(`${label}: working scene`, false, e.message)
    }

    try {
      // No page.reload() anywhere in this branch — this is what makes it a genuine E2 proof
      // rather than a repeat of the already-closed E3/E7 "durable state after a fresh load"
      // evidence.
      await row.locator('.conv-row-status--working').waitFor({ state: 'detached', timeout: 30000 })
      check(`${label}: WORKING clears live (no reload) once the turn ends`, true)
      await menu.screenshot({ path: join(OUT_DIR, `live-e2-cleared-${tag}-${widthLabel}.png`) })
    } catch (e) {
      check(`${label}: clears live (no reload)`, false, e.message)
    }
  } catch (e) {
    check(`${label}: scene failed to load at all`, false, e.message)
  } finally {
    await ctx.close()
  }
}

// E1's "and the browser" clause — ConversationBrowser ("View all conversations"), captured
// once (1440) alongside the per-width sidebar shots above; the sidebar already proves the
// cross-width claim.
async function watchBrowserWorking(browser, baseUrl, decoyId, titleText) {
  const { ctx, page, consoleErrors } = await newContext(browser, 1440, { activeId: decoyId, chatOpen: true })
  const label = 'e1-browser@1440'
  try {
    const menu = await openMenu(page, baseUrl)
    await menu.getByText('View all conversations', { exact: true }).click()
    const browserRoot = page.locator('.conv-browser')
    await browserRoot.waitFor({ state: 'visible', timeout: 10000 })
    const row = browserRoot.locator('.conv-browser-row', { hasText: titleText })
    await row.waitFor({ state: 'visible', timeout: 10000 })
    check(
      `${label}: full conversation browser ALSO shows WORKING`,
      (await row.locator('.conv-row-status--working').count()) === 1,
    )
    await overflowAndErrors(page, consoleErrors, label)
    await browserRoot.screenshot({ path: join(OUT_DIR, 'live-e1-working-browser-1440.png') })
  } catch (e) {
    check(`${label}: scene failed`, false, e.message)
  } finally {
    await ctx.close()
  }
}

// E4 — the mobile Lucius tab while the user is sitting on a DIFFERENT tab the whole time:
// spinner while a background turn runs, then (attempted with NO reload — see the finding
// this either confirms or refutes) a badge once it completes.
//
// MUST be the only conversation in play when this runs (see runE4Scenario / main()'s call
// order) — `hasBadge` in frontend/src/components/mobile/MobileTabBar.tsx is a union over
// EVERY conversation this browser knows about (`conversations.some(c => statusFor(c.id) is
// done/failed/needs_you)`), not scoped to the one conversation this scenario is about. A
// first pass that ran this alongside the fail/success scenarios' ALREADY-COMPLETED
// conversations produced a false PASS — the badge lit up immediately from THOSE rows, not
// from this scenario's own turn (caught by dumping the matched button's outerHTML: the
// spinner was still running at the exact instant the badge check "passed"). Confirmed with a
// dedicated, fully isolated repro (only the harmless decoy conversation present) that the
// true behaviour is: the badge never appears without a reload.
async function watchMobileE4(browser, baseUrl, decoyId, titleText) {
  const { ctx, page, consoleErrors } = await newContext(browser, 430, { activeId: decoyId, chatOpen: false })
  const label = 'e4@430'
  try {
    await page.goto(baseUrl + '/', { waitUntil: 'domcontentloaded', timeout: 20000 })
    await page.waitForTimeout(1500)
    const luciusBtn = page.locator('button[aria-label="Lucius"]')
    await luciusBtn.waitFor({ state: 'visible', timeout: 15000 })
    check(`${label}: NOT on the Lucius tab (sitting on the default tab)`, await isNotLuciusActive(page))

    const spinnerAppeared = await luciusBtn
      .locator('.lx-dock-dot')
      .waitFor({ state: 'visible', timeout: 20000 })
      .then(() => true)
      .catch(() => false)
    check(
      `${label}: Lucius tab spins while a background turn runs (user on another tab)`,
      spinnerAppeared,
    )
    if (spinnerAppeared) {
      await overflowAndErrors(page, consoleErrors, label)
      await page.locator('.mb-tabbar').screenshot({ path: join(OUT_DIR, 'live-e4-spinner-430.png') })
    }

    const badgeAppearedLive = await luciusBtn
      .locator('.mb-tab-badge')
      .waitFor({ state: 'visible', timeout: 25000 })
      .then(() => true)
      .catch(() => false)
    check(
      `${label}: badge appears LIVE (no reload) once the background turn completes`,
      badgeAppearedLive,
    )
    if (badgeAppearedLive) {
      await page.locator('.mb-tabbar').screenshot({ path: join(OUT_DIR, 'live-e4-badge-430.png') })
      return
    }

    // Root-cause + a second, clearly-labelled data point: does the badge appear once this
    // context learns the outcome at all (a reload — the next natural mount), proving the
    // DATA is correct even though nothing delivered it to the already-open tab live?
    note(
      'E4 badge did not appear live. useConversations() (frontend/src/hooks/useConversations.ts) ' +
        'fetches GET /api/sessions exactly once, in a mount-only effect with an empty dependency ' +
        'array — there is no periodic refetch and no bus event that refreshes conversation ' +
        "metadata when a turn a browser never locally touched finishes. The spinner (streamingIds, " +
        'fed by the /live poll) clears live because that IS polled every 4s ' +
        '(luciusChat.tsx LIVE_POLL_MS) — but the row falls through resolveRowStatus to `idle`, ' +
        "not `done`, because `conversations` (and so `meta.lastTurnStatus`) is stale until the " +
        'next mount. Checking whether a reload surfaces it (a DIFFERENT claim than E4 as written).',
    )
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 20000 })
    await page.waitForTimeout(1500)
    const luciusBtnAfterReload = page.locator('button[aria-label="Lucius"]')
    const badgeAfterReload = await luciusBtnAfterReload
      .locator('.mb-tab-badge')
      .waitFor({ state: 'visible', timeout: 10000 })
      .then(() => true)
      .catch(() => false)
    note(
      `After a reload (a DIFFERENT action than "stayed on another tab"), the badge ${
        badgeAfterReload ? 'DOES' : 'still does NOT'
      } appear — ${
        badgeAfterReload
          ? 'confirms the underlying last_turn_status data is correct; only the LIVE delivery to an already-open tab is missing.'
          : 'the data itself is not settling correctly even on a fresh mount — a deeper issue than a missing live refresh.'
      }`,
    )
    if (badgeAfterReload) {
      await page
        .locator('.mb-tabbar')
        .screenshot({ path: join(OUT_DIR, 'live-e4-badge-after-reload-430.png') })
    }
  } catch (e) {
    check(`${label}: scene failed to load at all`, false, e.message)
  } finally {
    await ctx.close()
  }
}

async function isNotLuciusActive(page) {
  const luciusBtn = page.locator('button[aria-label="Lucius"]')
  const current = await luciusBtn.getAttribute('aria-current')
  return current !== 'page'
}

async function runFailScenario(browser, baseUrl, decoyId) {
  console.log('\n== FAIL scenario (drives E2 on the ERROR branch) ==')
  const sender = await newContext(browser, 1440, { chatOpen: true })
  let convId
  try {
    convId = await sendFreshMessage(sender.page, baseUrl, FAIL_TEXT)
  } finally {
    // Phase 3 (server-owned turn lifetime, already proven by tests/test_turn_survives_client_
    // disconnect.py) means the turn does not depend on this tab staying open — closing it
    // immediately both simplifies this script and leans on that guarantee for real.
    await sender.ctx.close()
  }
  check('fail-scenario: captured a conversation id after send', !!convId, convId || '(none)')
  if (!convId) return

  await Promise.all(
    WIDTHS.map(({ label, w }) =>
      watchSidebarWorkingThenCleared(browser, baseUrl, decoyId, FAIL_TEXT, label, w, 'fail'),
    ),
  )
}

async function runSuccessScenario(browser, baseUrl, decoyId) {
  console.log('\n== SUCCESS scenario (drives E1 + E2 success branch) ==')
  const sender = await newContext(browser, 1440, { chatOpen: true })
  let convId
  try {
    convId = await sendFreshMessage(sender.page, baseUrl, SUCCESS_TEXT)
  } finally {
    await sender.ctx.close()
  }
  check('success-scenario: captured a conversation id after send', !!convId, convId || '(none)')
  if (!convId) return

  await Promise.all([
    ...WIDTHS.map(({ label, w }) =>
      watchSidebarWorkingThenCleared(browser, baseUrl, decoyId, SUCCESS_TEXT, label, w, 'ok'),
    ),
    watchBrowserWorking(browser, baseUrl, decoyId, SUCCESS_TEXT),
  ])
}

// E4 gets its OWN dedicated turn and conversation, and — per main()'s call order — runs
// BEFORE fail/success so no other completed conversation exists yet to contaminate the
// `hasBadge` union check. See watchMobileE4's docstring for the false positive this avoids.
async function runE4Scenario(browser, baseUrl, decoyId) {
  console.log('\n== E4 scenario (mobile badge, isolated — no other context running) ==')
  const sender = await newContext(browser, 1440, { chatOpen: true })
  let convId
  try {
    convId = await sendFreshMessage(sender.page, baseUrl, E4_TEXT)
  } finally {
    await sender.ctx.close()
  }
  check('e4-scenario: captured a conversation id after send', !!convId, convId || '(none)')
  if (!convId) return

  await watchMobileE4(browser, baseUrl, decoyId, E4_TEXT)
}

async function main() {
  const booted = await bootDisposableInstance({ port: Number(arg('--port', '8099')) })
  const { baseUrl, dataDir, stop } = booted
  assertSafeBoot({ port: 1, dataDir, cacheDir: dataDir, logDir: dataDir })

  const mock = await startMockLlmServer()
  console.log(`\n[mock-llm] listening at ${mock.url} (127.0.0.1-only, no network egress)`)
  writeModelConfig(dataDir, mock.url)

  console.log(`\n== concurrent-conversations live-TURN screenshots against ${baseUrl} ==`)
  console.log(`   writing to ${OUT_DIR}\n`)

  const browser = await chromium.launch()
  try {
    // A real, harmless conversation every watcher seeds as ITS OWN active id — so a target
    // conversation is never accidentally "the one I'm looking at" from a watcher's own point
    // of view, which would suppress its row status (resolveRowStatus gates done/failed on
    // `!isActive`; a fresh context with nothing seeded would otherwise auto-adopt whichever
    // conversation is most-recently-updated, which races against the very conversation this
    // script is trying to observe from the outside).
    const decoyId = `qa-decoy-${Math.random().toString(36).slice(2, 10)}`
    const decoyRes = await fetch(`${baseUrl}/api/sessions/${decoyId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'QA decoy (ignore)' }),
    })
    check('setup: decoy conversation minted', decoyRes.ok, String(decoyRes.status))

    // Order matters: E4 MUST run before fail/success have any completed conversation sitting
    // around — see watchMobileE4's own docstring for why (a real false positive this ordering
    // was specifically added to close).
    await runE4Scenario(browser, baseUrl, decoyId)
    await runFailScenario(browser, baseUrl, decoyId)
    await runSuccessScenario(browser, baseUrl, decoyId)
  } finally {
    await browser.close()
    await mock.stop()
    await stop()
  }
}

main()
  .then(() => {
    const failed = results.filter((r) => !r.ok)
    console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
    if (findings.length) {
      console.log(`\n${findings.length} finding(s) recorded above (NOTE lines) — read them before`)
      console.log('trusting any FAIL near them: some are an honest "this could not be driven live",')
      console.log('not a harness defect.')
    }
    process.exit(failed.length ? 1 : 0)
  })
  .catch((e) => {
    console.error('\nHARNESS ERROR:', e.message)
    console.error(e.stack)
    process.exit(2)
  })
