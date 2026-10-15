// Minimal headless-screenshot harness for design review / visual QA.
//
//   node scripts/shoot.mjs --url http://localhost:8080/ --out /tmp/shot.png \
//        [--tab transactions] [--selector .ui-modal-card] [--click-edit] [--full] \
//        [--click "button:has-text('Save')"] [--hover .pl-sum-stat]
//
// `--click` is repeatable and applied in order, for a state that takes several steps to reach.
//
// Loads the SPA (auth is dormant on loopback, so no login needed), optionally
// pins the active tab via localStorage, optionally opens the first "Edit" modal,
// optionally clicks and/or hovers one selector first (so a screenshot can show an
// opened picker or a hover-only affordance such as a title tooltip), and
// screenshots either a selector or the page. Hang-safe: a hard overall
// timeout always closes the browser and exits.
//
// Requires the dev `playwright` package + `npx playwright install chromium`.
// This is a DEV tool (no app dependency on it); it is never imported by the app.

import { chromium } from 'playwright'

const arg = (name, def = null) => {
  const i = process.argv.indexOf(`--${name}`)
  if (i === -1) return def
  const next = process.argv[i + 1]
  return next && !next.startsWith('--') ? next : true
}

// Every occurrence of a repeatable flag, in order — `--click a --click b` clicks a THEN b, so a
// screenshot can show a state that takes more than one step to reach (menu → item → confirm).
const argAll = (name) => {
  const out = []
  for (let i = 0; i < process.argv.length; i++) {
    if (process.argv[i] !== `--${name}`) continue
    const next = process.argv[i + 1]
    if (next && !next.startsWith('--')) out.push(next)
  }
  return out
}

const url = arg('url', 'http://localhost:8080/')
const out = arg('out', '/tmp/onw-shot.png')
const tab = arg('tab')
const selector = arg('selector')
const clickEdit = arg('click-edit') === true
const clicks = argAll('click')
const hover = arg('hover')
const full = arg('full') === true
const width = Number(arg('w', '1440'))
const height = Number(arg('h', '1000'))

const HARD_TIMEOUT_MS = 60000
const killer = setTimeout(() => {
  console.log('TIMEOUT — exiting')
  process.exit(1)
}, HARD_TIMEOUT_MS)

const browser = await chromium.launch()
try {
  const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 2 })
  await ctx.addInitScript(
    ([t]) => {
      if (t) localStorage.setItem('wealthTab', t)
      localStorage.setItem('chat-open', '0')
    },
    [tab],
  )
  const page = await ctx.newPage()
  await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 })
  await page.waitForTimeout(1200)

  if (clickEdit) {
    const editBtns = page.getByRole('button', { name: /^Edit$/ })
    const n = await editBtns.count()
    for (let i = 0; i < n; i++) {
      try {
        await editBtns.nth(i).click({ timeout: 2000 })
        await page.waitForSelector('.ui-modal-card', { timeout: 3000 })
        break
      } catch {
        /* try next Edit button */
      }
    }
    await page.waitForTimeout(500)
  }

  for (const click of clicks) {
    await page.locator(click).first().click({ timeout: 5000 })
    await page.waitForTimeout(600)
  }
  if (typeof hover === 'string') {
    await page.locator(hover).first().hover({ timeout: 5000 })
    // A native `title` tooltip is painted by the OS, not the page — hovering still
    // proves the affordance is reachable; assert the attribute for the real evidence.
    await page.waitForTimeout(1200)
  }

  if (selector) {
    await page.locator(selector).first().screenshot({ path: out })
  } else {
    await page.screenshot({ path: out, fullPage: full })
  }
  console.log('SHOT', out)
} catch (e) {
  console.log('ERROR', e.message)
  process.exitCode = 1
} finally {
  await browser.close()
  clearTimeout(killer)
}
