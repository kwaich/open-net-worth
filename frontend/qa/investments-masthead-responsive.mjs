// Regression for the split-pane Investments masthead.
//
// A wide browser with Lucius open can leave the Investments container narrow while
// the viewport itself remains wide. The desktop masthead must stay one compact 1x4 row;
// a viewport-only test misses the wrapping failure reported on 2026-08-12.

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
    <main class="intel-tab" style="width:700px">
      <section class="mk-sum mk-sum--4">
        <div class="mk-sum-cell">
          <div class="mk-sum-label">Total invested</div>
          <div class="mk-sum-c1"><div><div class="mk-sum-val mk-sum-hero">S$333,215.81</div><div class="mk-sum-sub">94.2% of net worth</div></div></div>
        </div>
        <div class="mk-sum-cell">
          <div class="mk-sum-label">Today</div><div class="mk-sum-val">−S$1,240.61</div><div class="mk-sum-sub">−0.37%</div>
        </div>
        <div class="mk-sum-cell">
          <div class="mk-sum-label">YTD vs SPY</div><div class="mk-sum-val mk-sum-val-pair"><span>+14.1%</span><span class="mk-sum-bench">+13.9%</span></div><div class="mk-sum-sub">+0.2 pts ahead</div>
        </div>
        <div class="mk-sum-cell">
          <div class="mk-sum-label">Unrealized P&amp;L</div><div class="mk-sum-val">+S$29,635.23</div><div class="mk-sum-sub">+9.8%</div>
        </div>
      </section>
    </main>
  `)

  const measure = async ({ viewportWidth, paneWidth }) => {
    await page.setViewportSize({ width: viewportWidth, height: 800 })
    return page.evaluate((width) => {
      const pane = document.querySelector('.intel-tab')
      pane.style.width = `${width}px`
      const masthead = document.querySelector('.mk-sum--4')
      const cells = [...masthead.children].map((cell) => {
        const rect = cell.getBoundingClientRect()
        return {
          left: Math.round(rect.left),
          top: Math.round(rect.top),
          width: Math.round(rect.width),
          overflowing: cell.scrollWidth > cell.clientWidth + 1,
        }
      })
      return {
        columns: new Set(cells.map(({ left }) => left)).size,
        rows: new Set(cells.map(({ top }) => top)).size,
        cells,
        subtitleOffsets: [...masthead.querySelectorAll('.mk-sum-cell')].map((cell) => {
          const sub = cell.querySelector('.mk-sum-sub')
          return Math.round(sub.getBoundingClientRect().top - cell.getBoundingClientRect().top)
        }),
      }
    }, paneWidth)
  }

  const cases = [
    {
      name: 'narrow split pane',
      viewportWidth: 1280,
      paneWidth: 700,
      columns: 4,
      rows: 1,
      equalColumns: true,
      alignedSubtitles: true,
    },
    {
      name: 'wide pane',
      viewportWidth: 1280,
      paneWidth: 1200,
      columns: 4,
      rows: 1,
      equalColumns: false,
      alignedSubtitles: false,
    },
    {
      name: 'exact phone boundary',
      viewportWidth: 640,
      paneWidth: 600,
      columns: 4,
      rows: 1,
      equalColumns: true,
      alignedSubtitles: true,
    },
  ]
  const results = []
  for (const fixture of cases) {
    const result = await measure(fixture)
    results.push({ ...fixture, ...result })
    if (result.columns !== fixture.columns || result.rows !== fixture.rows) {
      throw new Error(
        `${fixture.name} must use a ${fixture.columns}x${fixture.rows} masthead, got ${result.columns}x${result.rows}: ${JSON.stringify(result.cells)}`,
      )
    }
    if (result.cells.some(({ overflowing }) => overflowing)) {
      throw new Error(`${fixture.name} must not clip or overlap: ${JSON.stringify(result.cells)}`)
    }
    const widths = result.cells.map(({ width }) => width)
    const areEqual = Math.max(...widths) - Math.min(...widths) <= 1
    if (areEqual !== fixture.equalColumns) {
      throw new Error(
        `${fixture.name} equal-column expectation was ${fixture.equalColumns}, got widths ${JSON.stringify(widths)}`,
      )
    }
    if (
      fixture.alignedSubtitles &&
      Math.max(...result.subtitleOffsets) - Math.min(...result.subtitleOffsets) > 1
    ) {
      throw new Error(
        `${fixture.name} subtitles must share one baseline within their rows, got offsets ${JSON.stringify(result.subtitleOffsets)}`,
      )
    }
  }

  console.log(JSON.stringify(results, null, 2))
} finally {
  await browser.close()
}
