// Regression for holdings cards inside a narrow Investments/Lucius split pane.
//
// The pane can be narrow while the viewport remains desktop-wide. The complete compact
// card layout must follow the pane; changing only its grid tracks lets the six desktop
// fields auto-flow into arbitrary rows.

import { readFileSync } from 'node:fs'
import { chromium } from 'playwright'

const css = [
  new URL('../src/index.tokens.generated.css', import.meta.url),
  new URL('../src/index.css', import.meta.url),
  new URL('../src/components/tabs/investments/design.css', import.meta.url),
]
  .map((url) => readFileSync(url, 'utf8'))
  .join('\n')
  .replace(/@import[^;]+;/g, '')

const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })

try {
  await page.setContent(`
    <style>${css}</style>
    <main class="intel-tab" style="width:600px">
      <div class="mk-cardhead" id="head">
        <span>Holding</span><span>Value</span><span>Weight</span>
        <span>P&amp;L</span><span>Price</span><span>Today</span>
      </div>
      <article class="mk-hold">
        <div class="mk-hold-inner">
          <div class="mk-id" id="identity">
            <span class="mk-hold-marker"></span>
            <div class="mk-id-copy"><div class="mk-sym">KSTL</div><div class="mk-nm">Kestrel Dash SE</div></div>
          </div>
          <div class="mk-card-metrics">
            <div class="mk-col" id="value"><div class="mk-v">S$40,100.00</div></div>
            <div class="mk-col" id="weight"><div class="mk-v">20.4%</div></div>
            <div class="mk-col" id="pnl"><div class="mk-v">+S$8,000.00</div><div class="mk-plpct">28.74%</div></div>
          </div>
          <div class="mk-col mk-price-col" id="price"><div class="mk-v">€37.09</div></div>
          <div class="mk-market-move" id="move">
            <div class="mk-market" id="market"><span class="money">€37.09</span></div>
            <div class="mk-day down">−0.24%</div>
          </div>
        </div>
      </article>
    </main>
  `)

  const result = await page.evaluate(() => {
    const read = (id) => {
      const element = document.getElementById(id)
      const rect = element.getBoundingClientRect()
      return {
        display: getComputedStyle(element).display,
        left: Math.round(rect.left),
        top: Math.round(rect.top),
      }
    }
    return {
      head: read('head'),
      identity: read('identity'),
      value: read('value'),
      weight: read('weight'),
      pnl: read('pnl'),
      price: read('price'),
      move: read('move'),
      market: read('market'),
    }
  })

  const failures = []
  if (result.head.display !== 'none') failures.push('desktop column header remains visible')
  if (result.price.display !== 'none') failures.push('desktop price column remains visible')
  if (result.market.display === 'none') failures.push('compact footer price is hidden')
  if (result.value.left !== result.weight.left || result.value.left !== result.pnl.left) {
    failures.push('value, weight, and P&L are not aligned in the right column')
  }
  if (result.identity.left !== result.move.left || Math.abs(result.move.top - result.pnl.top) > 4) {
    failures.push('price/today footer is not aligned opposite P&L')
  }
  if (result.identity.left >= result.value.left)
    failures.push('identity is not left of the figures')

  if (failures.length) {
    throw new Error(
      `600px Investments pane must use the complete compact holdings card: ${failures.join('; ')}\n${JSON.stringify(result, null, 2)}`,
    )
  }

  console.log(JSON.stringify(result, null, 2))
} finally {
  await browser.close()
}
