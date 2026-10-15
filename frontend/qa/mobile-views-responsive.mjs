import { readFileSync } from 'node:fs'
import { chromium } from 'playwright'

const indexCss = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8').replace(
  /@import[^;]+;/g,
  '',
)
const commitmentsCss = readFileSync(
  new URL('../src/components/tabs/commitments/commitments.css', import.meta.url),
  'utf8',
)

const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 320, height: 800 } })
const widths = [320, 390, 589]

try {
  await page.setContent(`
    <style>${indexCss}\n${commitmentsCss}</style>
    <main>
      <section class="intel-brief-card fixture-brief" style="width:calc(100vw - 32px)">
        <div class="intel-brief-head">
          <span class="lx-mark" aria-hidden>✦</span>
          <span class="intel-brief-title">Lucius brief</span>
          <span class="intel-brief-pill">36 to decide</span>
          <button
            type="button"
            class="intel-brief-ask intel-brief-ask--lucius"
            aria-label="Ask Lucius about this brief"
          >
            <span class="intel-brief-ask-label">Ask Lucius</span>
            <span class="intel-brief-ask-spark" aria-hidden>✦</span>
          </button>
          <span class="intel-brief-updated">02:16 PM</span>
        </div>
      </section>

      <div class="c2-sec-tools" style="width:calc(100vw - 32px);margin-top:24px">
        <div class="c2-seg">
          <button>Active</button><button>At risk</button><button>Goals</button><button>Completed</button>
        </div>
        <select class="c2-select c2-select--sort" aria-label="Sort commitments">
          <option>Sort: Next payment</option>
        </select>
      </div>

      <div class="c3-act-tools" style="width:calc(100vw - 32px);margin-top:24px">
        <select class="c2-select detail-status" aria-label="Filter by status">
          <option>All statuses (5)</option>
        </select>
        <select class="c2-select detail-category" aria-label="Filter by category">
          <option>All categories</option>
        </select>
      </div>
    </main>
  `)

  const results = []
  const failures = []
  for (const width of widths) {
    await page.setViewportSize({ width, height: 800 })
    const result = await page.evaluate(() => {
      const head = document.querySelector('.intel-brief-head')
      const askLabel = document.querySelector('.intel-brief-ask-label')
      const sort = document.querySelector('.c2-select--sort')
      const detailStatus = document.querySelector('.detail-status')
      const detailCategory = document.querySelector('.detail-category')
      const metrics = (element) => {
        const style = getComputedStyle(element)
        return {
          width: element.getBoundingClientRect().width,
          textIndent: style.textIndent,
          backgroundImage: style.backgroundImage,
        }
      }
      return {
        page: {
          clientWidth: document.documentElement.clientWidth,
          scrollWidth: document.documentElement.scrollWidth,
        },
        header: { clientWidth: head.clientWidth, scrollWidth: head.scrollWidth },
        askLabelDisplay: getComputedStyle(askLabel).display,
        sort: metrics(sort),
        detailStatus: metrics(detailStatus),
        detailCategory: metrics(detailCategory),
      }
    })
    results.push({ width, ...result })

    if (result.page.scrollWidth > result.page.clientWidth + 1) {
      failures.push(
        `page overflows at ${width}px: ${result.page.scrollWidth} > ${result.page.clientWidth}`,
      )
    }
    if (result.header.scrollWidth > result.header.clientWidth + 1) {
      failures.push(
        `brief header overflows at ${width - 32}px: ${result.header.scrollWidth} > ${result.header.clientWidth}`,
      )
    }
    if (result.askLabelDisplay !== 'none') {
      failures.push(`Ask Lucius label remains visible at ${width}px: ${result.askLabelDisplay}`)
    }
    if (Math.round(result.sort.width) !== 36 || result.sort.backgroundImage === 'none') {
      failures.push(
        `list sorter is not the 36px icon control at ${width}px: ${JSON.stringify(result.sort)}`,
      )
    }
    for (const [name, metrics] of [
      ['status', result.detailStatus],
      ['category', result.detailCategory],
    ]) {
      if (metrics.width <= 36 || metrics.textIndent.startsWith('-')) {
        failures.push(
          `${name} detail filter was compacted at ${width}px: ${JSON.stringify(metrics)}`,
        )
      }
    }
  }

  if (failures.length > 0) {
    throw new Error(failures.join('\n'))
  }
  console.log(JSON.stringify(results, null, 2))
} finally {
  await browser.close()
}
