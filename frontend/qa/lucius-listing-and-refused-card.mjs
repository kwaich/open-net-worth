// lucius-listing-and-refused-card.mjs — mocked-API browser regression for two Lucius panel
// rules (ADR 2026-09-06-the-transcript-in-hand-outranks-the-listing):
//
//   A. A conversation listing that reports `message_count: 0` (and a stale title) for a
//      conversation whose transcript has messages must NOT paint the empty greeting over the
//      transcript, on first paint or after a reload, and must not rename it in the list.
//   B. A proposed-change card with `refused: true` renders the terminal "Not run" row instead
//      of Allow once / Deny, and stays terminal across a reload.
//
// No backend: every /api/* call is answered by a Playwright route, so this runs against a
// plain `vite preview` of the production bundle and never touches a data directory.
//
//   cd frontend && npm run build && npx vite preview --host 127.0.0.1 --port 8099 &
//   node qa/lucius-listing-and-refused-card.mjs        # APP_URL overrides the preview URL
//
// Exit code 1 on any failed check. Screenshots land in qa/artifacts/lucius-listing-refused/.
import { mkdirSync } from 'node:fs'
import { chromium, devices } from 'playwright'

const BASE = process.env.APP_URL || 'http://127.0.0.1:8099'
const OUT = 'qa/artifacts/lucius-listing-refused'
mkdirSync(OUT, { recursive: true })

const failures = []
function check(name, ok, detail) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : `  — ${detail}`}`)
  if (!ok) failures.push(name)
}

function mock(page, listing, messagesById) {
  return page.route('**/api/**', async (route) => {
    const p = new URL(route.request().url()).pathname
    const json = (body) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    const fixed = {
      '/api/auth/status': { required: false, authenticated: true },
      '/api/setup-status': { is_fresh: false },
      '/api/profile': { name: 'QA', nickname: 'QA' },
      '/api/sessions': listing,
      '/api/sessions/live': { session_ids: [] },
      '/api/entity-types': [],
      '/api/config': {},
      '/api/intel/signals': [],
      '/api/inbox/count': { count: 0 },
      '/api/matches/reviews': [],
      '/api/portfolio': {},
      '/api/market-ticker': {},
      '/api/automations/activity': {},
      '/api/models': [],
      '/api/model-config': {},
    }
    if (Object.prototype.hasOwnProperty.call(fixed, p)) return json(fixed[p])
    if (p.endsWith('/permission-mode')) return json({ mode: 'manual', editable: true })
    const m = p.match(/^\/api\/sessions\/([^/]+)\/messages$/)
    if (m) {
      const messages = messagesById[m[1]] || []
      return json({ messages, total: messages.length })
    }
    return json({})
  })
}

/** What the VISIBLE Lucius panel (`.lx-side.on`) is showing — never whole-body text, which
 *  on desktop also includes the closed dock's DOM. */
async function panelState(page) {
  return page.evaluate(() => {
    const el = document.querySelector('.lx-side.on')
    if (!el) return null
    return {
      hero: el.querySelectorAll('.lx-side-empty').length,
      bubbles: el.querySelectorAll('.msg-text').length,
      buttons: [...el.querySelectorAll('button')].map((b) => b.textContent.trim()),
      statusRows: [...el.querySelectorAll('[role="status"]')].map((s) => s.textContent.trim()),
      text: el.textContent,
    }
  })
}

const now = new Date().toISOString()
const listing = (count, title) => [
  { id: 'c1', title, created_at: now, updated_at: now, message_count: count, model: '' },
]
const sixMessages = Array.from({ length: 6 }, (_, i) => ({
  id: `c1-${i + 1}`,
  role: i % 2 ? 'assistant' : 'user',
  text: `Probe message ${i + 1}`,
}))
const change = {
  type: 'txn_create',
  grant_tool: 'add_transactions',
  id: 'toolu_refused_1',
  session_id: 'c1',
  description: 'Coffee',
  from: { account_id: 'acc_chk', account_name: 'Checking', amount: 5.5, currency: 'SGD' },
}
const cardMessages = (refused) => [
  { id: 'c1-1', role: 'user', text: 'Please approve this transaction.' },
  {
    id: 'c1-2',
    role: 'assistant',
    text: '',
    blocks: [{ type: 'change', change: { ...change, refused } }],
    proposedChanges: [{ ...change, refused }],
    error: refused ? 'This approval was already answered. Nothing was run — ask Lucius again.' : undefined,
  },
]
const ACTION = /^(allow once|deny|reject|always allow|confirm|dismiss|approve)$/i
const seedActive = { lucius_active_conversation: 'c1' }
const seedCached = {
  ...seedActive,
  lucius_conversation_list: JSON.stringify([
    { id: 'c1', title: 'Help me with assets', createdAt: now, updatedAt: now, messageCount: 6, model: '' },
  ]),
}

async function open(name, ctxOpts, rows, messages, seed) {
  const browser = await chromium.launch()
  const page = await (await browser.newContext(ctxOpts)).newPage()
  page.on('pageerror', (e) => check(`${name}: no page error`, false, String(e).slice(0, 200)))
  await page.addInitScript((s) => {
    for (const [k, v] of Object.entries(s)) localStorage.setItem(k, v)
  }, seed)
  await mock(page, rows, { c1: messages })
  await page.goto(`${BASE}/lucius`)
  await page.waitForTimeout(2500)
  const first = await panelState(page)
  await page.screenshot({ path: `${OUT}/${name}-1.png` })
  await page.reload()
  await page.waitForTimeout(2500)
  const second = await panelState(page)
  await page.screenshot({ path: `${OUT}/${name}-2.png` })
  await browser.close()
  return { first, second }
}

const phone = { ...devices['iPhone 13'] }
const desktop = { viewport: { width: 1280, height: 900 } }

// A — phone: first paint and reload both show the transcript, never the greeting.
{
  const r = await open('A-phone', phone, listing(0, 'New conversation'), sixMessages, seedCached)
  for (const [label, s] of [['first paint', r.first], ['after reload', r.second]]) {
    check(`A phone ${label}: 6 messages rendered`, s?.bubbles === 6, JSON.stringify(s))
    check(`A phone ${label}: no greeting`, s?.hero === 0, JSON.stringify(s))
  }
  // The history menu keeps the local title (the listing said "New conversation").
  const browser = await chromium.launch()
  const page = await (await browser.newContext(phone)).newPage()
  await page.addInitScript((s) => {
    for (const [k, v] of Object.entries(s)) localStorage.setItem(k, v)
  }, seedCached)
  await mock(page, listing(0, 'New conversation'), { c1: sixMessages })
  await page.goto(`${BASE}/lucius`)
  await page.waitForTimeout(2500)
  await page.getByRole('button', { name: 'Conversations' }).click()
  await page.waitForTimeout(500)
  const menuText = await page.evaluate(() => document.querySelector('.conv-menu')?.textContent || '')
  await page.screenshot({ path: `${OUT}/A-phone-menu.png` })
  check('A phone menu: keeps the local title', menuText.includes('Help me with assets'), menuText.slice(0, 200))
  await browser.close()
}
// A — desktop: after a reload (the desktop home tab starts a fresh draft on a brand-new
// browser session, by design) the transcript shows, never the greeting.
{
  const r = await open('A-desktop', desktop, listing(0, 'New conversation'), sixMessages, seedCached)
  check('A desktop after reload: 6 messages rendered', r.second?.bubbles === 6, JSON.stringify(r.second))
  check('A desktop after reload: no greeting', r.second?.hero === 0, JSON.stringify(r.second))
}
// A control — a genuinely empty conversation still greets.
{
  const r = await open('A-control', phone, listing(0, 'New conversation'), [], seedActive)
  check('A control: greeting shown for an empty conversation', r.first?.hero === 1, JSON.stringify(r.first))
}
// B — refused card is terminal on phone and desktop, and after reload.
for (const [name, ctx] of [['B-phone', phone], ['B-desktop', desktop]]) {
  const r = await open(name, ctx, listing(2, 'Approve'), cardMessages(true), seedActive)
  for (const [label, s] of [['first paint', r.first], ['after reload', r.second]]) {
    const actions = (s?.buttons || []).filter((b) => ACTION.test(b))
    check(`${name} ${label}: no approval buttons`, actions.length === 0, actions.join(','))
    check(
      `${name} ${label}: terminal status row`,
      (s?.statusRows || []).includes("Not run — can't be reused."),
      JSON.stringify(s?.statusRows),
    )
    check(`${name} ${label}: proposal body still visible`, /Coffee/.test(s?.text || ''), '')
  }
}
// B control — a live card still offers Allow once and Deny.
{
  const r = await open('B-control', phone, listing(2, 'Approve'), cardMessages(undefined), seedActive)
  const b = r.first?.buttons || []
  check('B control: Allow once present', b.includes('Allow once'), b.join(','))
  check('B control: Deny present', b.includes('Deny'), b.join(','))
}

console.log(failures.length ? `\n${failures.length} check(s) FAILED` : '\nall checks passed')
process.exit(failures.length ? 1 : 0)
