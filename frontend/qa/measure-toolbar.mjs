// Measure the Investments phone toolbar geometry against the REAL stylesheets.
//
//   node qa/measure-toolbar.mjs
//
// No backend, no data, no build — loads index.css + design.css in the same order the
// app does, renders the toolbar markup verbatim, and prints the actual
// border-box rects, the gap between each pair of controls, and the computed font sizes.
//
// Exists because "the spacing is not even" / "the font is not smaller" were being argued
// from screenshots. Measure, don't squint.

import { chromium } from 'playwright'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const css = [
  // Tokens FIRST — index.css consumes var(--text-*)/var(--line) etc. Without this every
  // token-valued declaration is invalid-at-computed-value and silently drops to inherit.
  join(SRC, 'index.tokens.generated.css'),
  join(SRC, 'index.css'),
  join(SRC, 'components/tabs/investments/design.css'),
]
  .map((p) => readFileSync(p, 'utf8'))
  // Strip @import/url() refs that would 404 without a dev server; irrelevant to layout.
  .join('\n')
  .replace(/@import[^;]+;/g, '')

const TOOLBAR = `
<div class="intel-feed">
  <div class="mk-toolbar" id="bar">
    <label class="mk-search-wrap">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6">
        <circle cx="7" cy="7" r="4.5"/><path d="m10.4 10.4 3.1 3.1" stroke-linecap="round"/>
      </svg>
      <input class="mk-search" aria-label="Search holdings" placeholder="Search holdings…">
    </label>
    <div class="mk-filter-wrap">
      <button type="button" class="mk-filter-btn is-active" aria-label="Filters (active)">
        <svg width="17" height="17" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6">
          <path d="M2.5 4.5h13M5 9h8M7.5 13.5h3" stroke-linecap="round"/>
        </svg>
      </button>
    </div>
    <div class="mk-density" role="radiogroup" aria-label="Holdings density">
      <button type="button" role="radio" aria-label="Card view" class="on">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
          <rect x="2" y="2.5" width="12" height="4.5" rx="1.2"/><rect x="2" y="9" width="12" height="4.5" rx="1.2"/>
        </svg>
      </button>
      <button type="button" role="radio" aria-label="Table view">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
          <line x1="2.5" y1="4" x2="13.5" y2="4"/><line x1="2.5" y1="8" x2="13.5" y2="8"/><line x1="2.5" y1="12" x2="13.5" y2="12"/>
        </svg>
      </button>
    </div>
  </div>
</div>`

const browser = await chromium.launch()
try {
  for (const w of [390, 360, 430]) {
    const page = await browser.newPage({ viewport: { width: w, height: 800 } })
    await page.setContent(
      `<style>${css}</style><body class="" style="margin:0;padding:0 14px">${TOOLBAR}</body>`,
    )
    const out = await page.evaluate(() => {
      const bar = document.getElementById('bar')
      const kids = [...bar.children].filter((el) => getComputedStyle(el).display !== 'none')
      const rows = kids.map((el) => {
        const r = el.getBoundingClientRect()
        const cs = getComputedStyle(el)
        // The painted box is the element itself EXCEPT for .mk-search-wrap, which is a
        // bare <label> whose visible chrome is the <input> inside it. (.mk-density draws
        // its own border, so measuring its inner button would report a phantom gap.)
        const paint = el.classList.contains('mk-search-wrap')
          ? el.querySelector('input')
          : el
        const pr = paint.getBoundingClientRect()
        return {
          el: el.className.trim().split(/\s+/)[0],
          box: [Math.round(r.left), Math.round(r.right)],
          paint: [Math.round(pr.left), Math.round(pr.right)],
          h: Math.round(r.height),
          font: getComputedStyle(paint).fontSize,
          // Proof that the design.css rules reached this element at all.
          radius: getComputedStyle(paint).borderTopLeftRadius,
          pad: getComputedStyle(paint).padding,
          minW: Math.round(paint.scrollWidth),
        }
      })
      const layoutGaps = rows.slice(1).map((b, i) => Math.round(b.box[0] - rows[i].box[1]))
      const visualGaps = rows.slice(1).map((b, i) => Math.round(b.paint[0] - rows[i].paint[1]))
      return {
        rows,
        layoutGaps,
        visualGaps,
        gapProp: getComputedStyle(bar).columnGap,
        overflow: document.documentElement.scrollWidth - window.innerWidth,
      }
    })
    console.log(`\n=== viewport ${w}px ===`)
    for (const r of out.rows) {
      console.log(
        `  ${r.el.padEnd(16)} box=[${r.box.join(',')}] paint=[${r.paint.join(',')}] h=${r.h} font=${r.font} radius=${r.radius} pad=${r.pad}`,
      )
    }
    console.log(`  gap prop      : ${out.gapProp}`)
    console.log(`  layout gaps   : ${out.layoutGaps.join(' , ')}`)
    console.log(`  VISUAL gaps   : ${out.visualGaps.join(' , ')}   <-- what the eye sees`)
    console.log(`  page overflow : ${out.overflow}px`)
    await page.close()
  }
} finally {
  await browser.close()
}

// ── Cascade dump: every author rule that sets font-size on the class select ──
{
  const page = await (await chromium.launch()).newPage({ viewport: { width: 390, height: 800 } })
  await page.setContent(`<style>${css}</style><body style="margin:0;padding:0 14px">${TOOLBAR}</body>`)
  const dump = await page.evaluate(() => {
    const out = []
    const el = document.querySelectorAll('.mk-toolsel')[0]
    const walk = (rules, media) => {
      for (const r of rules) {
        if (r.media) { walk(r.cssRules, r.conditionText); continue }
        if (!r.selectorText) continue
        let matches = false
        try { matches = el.matches(r.selectorText) } catch { /* :has etc */ }
        if (!matches) continue
        const fs = r.style.getPropertyValue('font-size')
        const f = r.style.getPropertyValue('font')
        if (fs || f) out.push({ sel: r.selectorText, media: media || '-', font: f, fontSize: fs })
      }
    }
    for (const sheet of document.styleSheets) { try { walk(sheet.cssRules, null) } catch {} }
    return {
      rules: out,
      computed: getComputedStyle(el).fontSize,
      bodyFont: getComputedStyle(document.body).fontSize,
    }
  })
  console.log('\n=== font-size cascade for .mk-toolsel ===')
  console.log('body font-size:', dump.bodyFont, '| computed on select:', dump.computed)
  for (const r of dump.rules) console.log(`  @${r.media}  ${r.sel}  font="${r.font}" font-size="${r.fontSize}"`)
  process.exit(0)
}
