// Measure what the REAL stylesheet does to the phone tab bar when the keyboard flag is set.
//
//   node qa/measure-keyboard-tabbar.mjs
//
// No backend, no build, no LLM — loads the app's stylesheets in the same order the app does,
// renders the tab-bar markup verbatim at 390x844, and measures the actual computed geometry
// in both states. Follows qa/measure-toolbar.mjs's pattern.
//
// Exists because the unit tests prove only half the contract: `useMobileKeyboardLayout` sets
// `.mobile-keyboard-open` on <html>, and the CSS is supposed to take the bar away. Twice now
// the owner has reported the bar still sitting on top of the keyboard, so measure the CSS half
// against the real file instead of reasoning about it.

import { chromium } from 'playwright'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const css = [
  join(SRC, 'index.tokens.generated.css'),
  join(SRC, 'index.css'),
]
  .map((f) => readFileSync(f, 'utf8'))
  .join('\n')

const MARKUP = `
<div class="app-shell mb-shell">
  <div class="app-layout"><p>content</p></div>
  <nav class="mb-tabbar" aria-label="Primary">
    <button class="mb-tab active"><span class="mb-tab-icon">i</span><span class="mb-tab-label">Overview</span></button>
    <button class="mb-tab"><span class="mb-tab-icon">i</span><span class="mb-tab-label">Lucius</span></button>
  </nav>
  <div class="acct-toast mb-anchored">saved</div>
</div>`

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
await page.setContent(`<style>${css}</style>${MARKUP}`)

const read = () =>
  page.evaluate(() => {
    const root = document.documentElement
    const bar = document.querySelector('.mb-tabbar')
    const toast = document.querySelector('.acct-toast')
    const shell = document.querySelector('.app-shell')
    const rect = bar.getBoundingClientRect()
    return {
      display: getComputedStyle(bar).display,
      barHeight: Math.round(rect.height),
      barTop: Math.round(rect.top),
      tabbarVar: getComputedStyle(shell).getPropertyValue('--mb-tabbar-h').trim(),
      toastBottom: getComputedStyle(toast).bottom,
      viewportH: root.clientHeight,
    }
  })

const closed = await read()
// Focus-only: we know a keyboard is up, not how tall it is.
await page.evaluate(() => document.documentElement.classList.add('mobile-keyboard-open'))
const assumed = await read()
// Measured: the visualViewport gave us the height, so the layout may re-flow around it.
await page.evaluate(() => document.documentElement.classList.add('mobile-keyboard-measured'))
const measured = await read()

await browser.close()

const rows = [
  ['keyboard closed ', closed],
  ['assumed (focus) ', assumed],
  ['measured        ', measured],
]
console.log('viewport 390x844 · real index.css\n')
for (const [label, m] of rows) {
  console.log(
    `${label}  display=${m.display.padEnd(5)} height=${String(m.barHeight).padStart(3)}px ` +
      `top=${String(m.barTop).padStart(4)} --mb-tabbar-h=${m.tabbarVar.padEnd(6)} ` +
      `toast bottom=${m.toastBottom}`,
  )
}

const checks = [
  ['bar is laid out when no keyboard', closed.display !== 'none' && closed.barHeight > 40],
  ['bar sits at the bottom edge', closed.barTop + closed.barHeight === closed.viewportH],
  ['bar is REMOVED on focus alone', assumed.display === 'none'],
  // The owner's "big black space": reclaiming the bar's 57px without knowing the keyboard
  // height grows the content pane down underneath it. Focus alone must not do that.
  ['focus alone does NOT re-flow the layout', assumed.tabbarVar === closed.tabbarVar],
  ['a real measurement DOES reclaim the space', measured.tabbarVar === '0px'],
  ['anchored surfaces follow the measured reflow', closed.toastBottom !== measured.toastBottom],
]

console.log('')
let failed = 0
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`)
  if (!ok) failed++
}
console.log(`\n${checks.length - failed}/${checks.length} checks passed`)
process.exit(failed ? 1 : 0)
