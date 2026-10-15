// Screenshot evidence for the "Notable Price Movement" timeline on the ticker page.
//
// Self-boots a disposable demo instance (qa/boot.mjs — fresh mktemp root, never :8080),
// opens /quote/UBER against REAL provider data (Yahoo bars + market state), seeds two cited
// paragraphs straight into the disposable DB (no LLM is configured in QA, so the narrator
// would otherwise land "no coverage"), then shoots:
//   closed-desktop / closed-sources-open / closed-mobile-{390,360,320} — real closed state
//   live-desktop / after-hours-desktop                    — SIMULATED payloads served via
//                                                           page.route() (market shut on a
//                                                           Sunday; labelled as such)
// Artifacts: qa/artifacts/notable-price-movement/*.png

import { spawnSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { bootDisposableInstance, reserveFreeLoopbackPort, resolveQaPython } from './boot.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const OUT = join(HERE, 'artifacts', 'notable-price-movement')
mkdirSync(OUT, { recursive: true })
const REPO_ROOT = join(HERE, '..', '..')

const PARAGRAPHS = {
  0: {
    summary:
      'Uber shares closed modestly lower, weighed by a California arbitration ruling ordering the company to pay $40 million in a wrongful death case where a judge found Uber liable as a common carrier with a non-delegable duty to protect passengers; Uber disputed the liability finding while citing ongoing safety improvements. The stock remains under pressure this year despite a post-earnings insider buying wave from top executives, as investors weigh legal and regulatory risk and robotaxi competition against a low ~15.5x P/E and a Street consensus target near $101-103.',
    sources: [
      { url: 'https://www.reuters.com/legal/uber-arbitration-ruling', title: 'Uber ordered to pay $40 million in wrongful death arbitration', domain: 'reuters.com' },
      { url: 'https://www.bloomberg.com/news/uber-insider-buying', title: 'Uber executives step up share purchases after earnings', domain: 'bloomberg.com' },
      { url: 'https://seekingalpha.com/article/uber-valuation', title: 'Uber: the valuation case at 15x earnings', domain: 'seekingalpha.com' },
      { url: 'https://www.cnbc.com/uber-robotaxi-competition', title: 'Robotaxi rivals crowd Uber’s home markets', domain: 'cnbc.com' },
      { url: 'https://www.wsj.com/uber-price-target', title: 'Analysts hold Uber targets near $101', domain: 'wsj.com' },
    ],
  },
  1: {
    summary:
      'Uber shares closed little changed, holding steady as investors weighed continued robotaxi expansion momentum against a broader macro backdrop of rising rate expectations. Recent catalysts include Uber and WeRide securing Spain’s first national Level 4 autonomous vehicle permit for Madrid, an expanded Costco grocery delivery partnership and a completed €4.5B multi-tranche euro debt offering.',
    sources: [
      { url: 'https://www.reuters.com/technology/uber-weride-madrid', title: 'Uber, WeRide win Spain’s first national L4 permit', domain: 'reuters.com' },
      { url: 'https://techcrunch.com/uber-costco-grocery', title: 'Uber expands Costco grocery delivery', domain: 'techcrunch.com' },
    ],
  },
}

function seedNotes(dataDir, instrumentId, rows) {
  // Two realistic paragraphs on the newest sessions, then short placeholders on the 14
  // weekdays before them — as if the ticker had been watched for weeks — so the page has more
  // than one screen of WRITTEN-UP history (an old day without a paragraph is not shown, so
  // only seeded days can exercise the scroll paging). A seeded weekday with no bar (a holiday)
  // is simply never rendered.
  const sessions = rows.filter((r) => r.kind === 'session')
  const older = []
  const cursor = new Date(`${sessions[1]?.session_date ?? sessions[0].session_date}T12:00:00Z`)
  while (older.length < 14) {
    cursor.setUTCDate(cursor.getUTCDate() - 1)
    if (cursor.getUTCDay() === 0 || cursor.getUTCDay() === 6) continue
    older.push({
      instrument_id: instrumentId,
      session_date: cursor.toISOString().slice(0, 10),
      currency: 'USD',
      final: true,
      summary: 'Shares moved with the broader market on a session without a company-specific catalyst in the coverage; sector peers traded in the same direction.',
      sources: [{ url: 'https://www.reuters.com/markets/', title: 'Markets wrap', domain: 'reuters.com' }],
      narrated_at: new Date().toISOString(),
      narrated_final: true,
      narration_checked_at: new Date().toISOString(),
      narration_status: 'ready',
    })
  }
  const notes = sessions
    .slice(0, 2)
    .map((r, i) => ({
      instrument_id: instrumentId,
      session_date: r.session_date,
      currency: 'USD',
      close: r.price,
      prev_close: r.prev_close,
      change_pct: r.change_pct,
      final: true,
      summary: PARAGRAPHS[i].summary,
      sources: PARAGRAPHS[i].sources,
      narrated_at: new Date().toISOString(),
      narrated_final: true,
      narration_checked_at: new Date().toISOString(),
      ...(i === 0 ? { after_hours: { price: 70.6, change_pct: 0.1418, as_of: null } } : {}),
    }))
    .concat(older)
  const py = [
    'import json, sys',
    'from backend.db.price_movement_store import PriceMovementStore',
    'store = PriceMovementStore()',
    'for n in json.loads(sys.argv[1]):',
    '    store.upsert_note(n)',
    'print("seeded", len(json.loads(sys.argv[1])))',
  ].join('\n')
  const res = spawnSync(resolveQaPython(), ['-c', py, JSON.stringify(notes)], {
    cwd: REPO_ROOT,
    env: { ...process.env, DATA_DIR: dataDir, ONW_QA_MODE: 'true', AUTH_DISABLED: 'true' },
    encoding: 'utf8',
  })
  if (res.status !== 0) throw new Error(`seed failed: ${res.stderr}`)
  console.log('[seed]', res.stdout.trim())
}

function simulatedPayload(real, mode) {
  const sessions = real.rows.filter((r) => r.kind === 'session')
  const latest = sessions[0]
  const nowIso = new Date().toISOString()
  if (mode === 'closed-cest') {
    // A European venue: the notice row's clock reads e.g. "10:14 AM CEST" — the widest label
    // the date column has to hold (owner-reported clipping, 2026-09-20, KSTL.DE on a phone).
    const market = { ...real.market, timezone: 'Europe/Berlin', tz_short: 'CEST' }
    const notice = { ...real.rows[0], kind: 'market_closed', session_date: real.today, as_of: nowIso }
    return { ...real, market, rows: [notice, ...sessions] }
  }
  const market = { ...real.market, state: mode === 'live' ? 'REGULAR' : 'POST', is_open: true, is_regular: mode === 'live' }
  const top =
    mode === 'live'
      ? {
          kind: 'live', session_date: real.today, as_of: nowIso, phase: 'regular', price: 71.12,
          change_pct: 0.8794, prev_close: latest.price, after_hours: null,
          summary: 'Uber shares are trading higher, extending a rebound from Friday’s close as the market reopened; early coverage points to renewed robotaxi partnership headlines and a broader risk-on tone in transport and platform names, with no company-specific filings so far this session.',
          sources: PARAGRAPHS[1].sources, narration: 'ready', notable: false,
        }
      : {
          kind: 'session', session_date: real.today, as_of: nowIso, phase: 'post', price: 72.41,
          change_pct: 2.7092, prev_close: latest.price,
          after_hours: { price: 72.2, change_pct: -0.29, as_of: nowIso },
          summary: null, sources: [], narration: 'pending', notable: true,
        }
  return { ...real, market, rows: [top, ...sessions] }
}

async function shoot(page, name, { width = 1280, height = 900, full = false } = {}) {
  await page.setViewportSize({ width, height })
  const section = page.locator('.asd-pm')
  await section.waitFor({ state: 'visible', timeout: 60_000 })
  await section.scrollIntoViewIfNeeded()
  // The sticky market strip sits over the top edge after scrollIntoView — nudge up 72px.
  await page.evaluate(() => window.scrollBy(0, -72))
  await page.waitForTimeout(400)
  if (full) await page.screenshot({ path: join(OUT, `${name}.png`), fullPage: true })
  else await section.screenshot({ path: join(OUT, `${name}.png`) })
  console.log('[shot]', name)
}

const port = await reserveFreeLoopbackPort()
const inst = await bootDisposableInstance({ port })
const browser = await chromium.launch({ headless: true })
try {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: 'dark' })
  const page = await ctx.newPage()
  page.on('console', (m) => { if (m.type() === 'error') console.log('[console.error]', m.text().slice(0, 200)) })

  // Resolve the ticker + read the REAL timeline once, so the seed and the simulated
  // payloads are anchored on the provider's actual session dates and closes.
  const detail = await (await fetch(`${inst.baseUrl}/api/intel/assets/by-ticker/UBER`)).json()
  const real = await (await fetch(`${inst.baseUrl}/api/intel/assets/${detail.instrument_id}/movements?limit=10`)).json()
  console.log('[real]', real.market?.state, real.today, real.rows.slice(0, 3).map((r) => `${r.kind}:${r.session_date}:${r.price}`).join(' '))
  seedNotes(inst.dataDir, detail.instrument_id, real.rows)

  await page.goto(`${inst.baseUrl}/quote/UBER`, { waitUntil: 'domcontentloaded' })
  await shoot(page, 'closed-desktop')
  // The card is capped and scrolls inside itself (Perplexity): its box never exceeds the cap,
  // and with a full page of sessions there is more content than box.
  const scrollGeo = await page.evaluate(() => {
    const el = document.querySelector('.asd-pm-card')
    return { box: Math.round(el.getBoundingClientRect().height), content: el.scrollHeight, overflowY: getComputedStyle(el).overflowY }
  })
  console.log(`[scroll] ${JSON.stringify(scrollGeo)}`)
  if (scrollGeo.box > 340 || scrollGeo.overflowY !== 'auto') throw new Error(`timeline card not capped/scrollable: ${JSON.stringify(scrollGeo)}`)
  // Infinite scroll (Perplexity: no button): scrolling the BOX to its bottom requests the next
  // page, which appends in place — the card keeps its height and its first rows throughout.
  const before = await page.evaluate(() => document.querySelectorAll('.asd-pm-row[data-kind]').length)
  const seenLimit20 = page.waitForRequest((r) => r.url().includes('/movements') && r.url().includes('limit=20'), { timeout: 15000 })
  await page.evaluate(() => { const el = document.querySelector('.asd-pm-card'); el.scrollTop = el.scrollHeight })
  await seenLimit20
  await page.waitForFunction((n) => document.querySelectorAll('.asd-pm-row[data-kind]').length > n, before, { timeout: 30000 })
  const after = await page.evaluate(() => ({ rows: document.querySelectorAll('.asd-pm-row[data-kind]').length, box: Math.round(document.querySelector('.asd-pm-card').getBoundingClientRect().height), firstRowStill: !!document.querySelector('.asd-pm-row[data-kind="market_closed"], .asd-pm-row[data-kind="live"]') }))
  console.log(`[more] rows ${before} → ${after.rows}, box ${after.box}px, first row kept=${after.firstRowStill}`)
  if (after.rows <= before || after.box !== scrollGeo.box) throw new Error(`load-more did not append in place: ${JSON.stringify(after)}`)
  await shoot(page, 'closed-desktop-after-scroll')
  await shoot(page, 'closed-page', { full: true })
  await page.locator('.asd-pm-sources-btn').first().click()
  await page.waitForTimeout(200)
  await shoot(page, 'closed-sources-open')
  // Phone widths — sources still expanded from the click above. Assert no horizontal
  // overflow at each width (the page must never scroll sideways on a phone).
  for (const width of [390, 360, 320]) {
    await shoot(page, `closed-mobile-${width}`, { width, height: 844 })
    // Quote stats under the chart: Perplexity-style tile grid on a phone (3 across, 2 across
    // only when the block itself is ≲300px wide). Shot separately so it's reviewable alone.
    await page.setViewportSize({ width, height: 844 })
    const stats = page.locator('.asd-stats')
    if (await stats.count()) {
      await stats.scrollIntoViewIfNeeded()
      await page.evaluate(() => window.scrollBy(0, -72))
      await page.waitForTimeout(200)
      // The whole card, not the cropped grid — the inset around the grid is only visible
      // with the card edges in frame (owner report 2026-09-20: "why does our grid have padding").
      const card = stats.locator('xpath=ancestor::*[contains(concat(" ", @class, " "), " panel ")][1]')
      const cb = await card.boundingBox()
      await page.screenshot({
        path: join(OUT, `stats-mobile-${width}.png`),
        clip: { x: 0, y: Math.max(0, cb.y + cb.height - 300), width, height: 320 },
      })
      const geo = await page.evaluate(() => {
        const grid = document.querySelector('.asd-stats-grid').getBoundingClientRect()
        const card = document.querySelector('.asd-stats').closest('.panel').getBoundingClientRect()
        const cols = getComputedStyle(document.querySelector('.asd-stats-grid')).gridTemplateColumns.split(' ').length
        const cells = [...document.querySelectorAll('.asd-stat')]
        const boxes = [...new Set(cells.map((c) => { const cs = getComputedStyle(c); return `${cs.padding}|${cs.borderTopWidth}` }))]
        const heights = [...new Set(cells.map((c) => Math.round(c.getBoundingClientRect().height)))]
        return { cols, insetLeft: Math.round(grid.left - card.left), insetRight: Math.round(card.right - grid.right), insetBottom: Math.round(card.bottom - grid.bottom), boxes, heights }
      })
      console.log(`[stats] ${width}px ${JSON.stringify(geo)}`)
      // 1px = the card's own border; anything more is a blank margin around the grid.
      if (geo.insetLeft > 1 || geo.insetRight > 1 || geo.insetBottom > 1) throw new Error(`stats grid not full-bleed at ${width}px: ${JSON.stringify(geo)}`)
      // Uniform by construction: every tile — first row, last row, middle — has the SAME
      // padding and top border. (Heights may still differ when a label/value wraps; that's
      // content, not structure, and is logged, not asserted.)
      if (geo.boxes.length !== 1) throw new Error(`tiles are not structurally uniform at ${width}px: ${JSON.stringify(geo)}`)
    }
    const overflow = await page.evaluate(() => ({
      doc: document.documentElement.scrollWidth - window.innerWidth,
      card: (() => {
        const el = document.querySelector('.asd-pm-card')
        return el ? Math.round(el.getBoundingClientRect().right - window.innerWidth) : 0
      })(),
    }))
    console.log(`[overflow] ${width}px doc=${overflow.doc}px card-right=${overflow.card}px`)
    if (overflow.doc > 0 || overflow.card > 0) throw new Error(`horizontal overflow at ${width}px: ${JSON.stringify(overflow)}`)
  }

  // Long timezone label on a phone: the clock must stay inside the date column (never clipped
  // by the card's left edge) — simulated market only; the rows are still the real sessions.
  for (const width of [320, 390]) {
    const p3 = await ctx.newPage()
    await p3.route('**/api/intel/assets/*/movements*', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(simulatedPayload(real, 'closed-cest')) }),
    )
    await p3.goto(`${inst.baseUrl}/quote/UBER`, { waitUntil: 'domcontentloaded' })
    await shoot(p3, `closed-mobile-${width}-CEST-SIMULATED`, { width, height: 844 })
    const clip = await p3.evaluate(() => {
      const card = document.querySelector('.asd-pm-card').getBoundingClientRect()
      const when = document.querySelector('.asd-pm-row[data-kind="market_closed"] .asd-pm-when').getBoundingClientRect()
      const time = document.querySelector('.asd-pm-row[data-kind="market_closed"] .asd-pm-time')
      const t = time.getBoundingClientRect()
      return { text: time.textContent, timeLeftInsideCard: Math.round(t.left - card.left), timeLeftInsideColumn: Math.round(t.left - when.left), lines: Math.round(t.height / parseFloat(getComputedStyle(time).lineHeight)) }
    })
    console.log(`[clip] ${width}px ${JSON.stringify(clip)}`)
    if (clip.timeLeftInsideCard < 0 || clip.timeLeftInsideColumn < 0) throw new Error(`time label clipped at ${width}px: ${JSON.stringify(clip)}`)
    await p3.close()
  }

  // Simulated open-market states (the venue is shut while this runs): intercept ONLY the
  // movements endpoint and reshape the real payload; everything else stays live.
  for (const mode of ['live', 'after-hours']) {
    const page2 = await ctx.newPage()
    await page2.route('**/api/intel/assets/*/movements*', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(simulatedPayload(real, mode)) }),
    )
    await page2.goto(`${inst.baseUrl}/quote/UBER`, { waitUntil: 'domcontentloaded' })
    await shoot(page2, `${mode}-desktop-SIMULATED`)
    await page2.close()
  }
} finally {
  await browser.close()
  inst.stop()
}
console.log('[done]', OUT)
