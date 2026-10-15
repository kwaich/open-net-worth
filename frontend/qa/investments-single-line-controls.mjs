// Regression for the two Investments rows that must never wrap.
//
// A desktop viewport can still have a narrow Investments pane when Lucius is open.
// The holdings toolbar must shrink its filters around the fixed density toggle, while
// Today's Drivers becomes a horizontal scroller instead of adding another line.

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
      <div class="intel-feed">
      <div class="mk-toolbar" id="toolbar">
        <label class="mk-search-wrap"><input class="mk-search" placeholder="Search holdings…"></label>
        <div class="mk-filter-wrap mk-pane-filter" id="merged-filter">
          <button class="mk-filter-btn" type="button" aria-label="Filters"></button>
          <div class="mk-filter-popover" id="filter-popover">
            <div class="mk-filter-popover-head"><span class="mk-filter-popover-title">Filter holdings</span></div>
            <div class="mk-filter-popover-body">
              ${['All', 'Equities', 'Crypto', 'Commodities', 'All accounts', 'JP Morgan Workplace Solutions', 'IBKR', 'Gemini', 'Coinbase', 'Binance']
                .map((label) => `<button class="kebab-sheet-item mk-filter-item">${label}</button>`)
                .join('')}
            </div>
          </div>
        </div>
        <select class="mk-toolsel mk-account-filter" id="account-filter" aria-label="Filter by account">
          <option>All accounts</option>
          <option>JP Morgan Workplace Solutions</option>
        </select>
        <div class="mk-group-wrap is-active" id="group-control">
          <svg width="17" height="17" viewBox="0 0 24 24" aria-hidden="true"></svg>
          <select class="mk-toolsel" aria-label="Grouping"><option>Group: Account</option></select>
        </div>
        <div class="mk-density">
          <button type="button"><svg width="15" height="15"></svg></button>
          <button type="button"><svg width="15" height="15"></svg></button>
        </div>
      </div>

      <div class="intel-feed-bar mk-class-filter-bar" id="class-filter">
        <div class="intel-feed-filter"><button class="intel-feed-filter-btn">All 34</button></div>
      </div>

      <div class="intel-brief-movers" id="drivers">
        <span class="mvr-label">Today's drivers</span>
        <div class="mvr-list">
          ${[
            'META ▲ 0.97%',
            'RDW ▲ 3.89%',
            'RKLB ▲ 1.55%',
            'ORCL ▼ 3.87%',
            'KSTL.DE ▼ 0.24%',
            'MSFT ▼ 0.70%',
          ]
            .map((label) => `<button class="mvr-chip">${label}</button>`)
            .join('')}
        </div>
      </div>
      </div>
    </main>
  `)

  const result = await page.evaluate(() => {
    const toolbar = document.getElementById('toolbar')
    const toolbarRect = toolbar.getBoundingClientRect()
    const controls = [...toolbar.children]
      .filter((control) => getComputedStyle(control).display !== 'none')
      .map((control) => {
        const rect = control.getBoundingClientRect()
        return {
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          top: Math.round(rect.top),
        }
      })
    const drivers = document.getElementById('drivers')
    const mergedFilter = document.getElementById('merged-filter')
    const accountFilter = document.getElementById('account-filter')
    const classFilter = document.getElementById('class-filter')
    const filterPopover = document.getElementById('filter-popover')
    const groupControl = document.getElementById('group-control')
    const chips = [...drivers.querySelectorAll('.mvr-chip')].map((chip) => {
      const rect = chip.getBoundingClientRect()
      return { left: Math.round(rect.left), top: Math.round(rect.top) }
    })
    return {
      toolbar: {
        clientWidth: toolbar.clientWidth,
        scrollWidth: toolbar.scrollWidth,
        right: Math.round(toolbarRect.right),
        controls,
        mergedFilterDisplay: getComputedStyle(mergedFilter).display,
        accountFilterDisplay: getComputedStyle(accountFilter).display,
        classFilterDisplay: getComputedStyle(classFilter).display,
        popover: {
          width: Math.round(filterPopover.getBoundingClientRect().width),
          height: Math.round(filterPopover.getBoundingClientRect().height),
          maxHeight: getComputedStyle(filterPopover).maxHeight,
        },
        group: {
          width: Math.round(groupControl.getBoundingClientRect().width),
          iconDisplay: getComputedStyle(groupControl.querySelector('svg')).display,
          selectOpacity: getComputedStyle(groupControl.querySelector('select')).opacity,
        },
      },
      drivers: {
        clientWidth: drivers.clientWidth,
        scrollWidth: drivers.scrollWidth,
        overflowX: getComputedStyle(drivers).overflowX,
        chips,
      },
    }
  })
  const wideResult = await page.evaluate(() => {
    document.querySelector('.intel-tab').style.width = '900px'
    return {
      mergedFilterDisplay: getComputedStyle(document.getElementById('merged-filter')).display,
      accountFilterDisplay: getComputedStyle(document.getElementById('account-filter')).display,
      classFilterDisplay: getComputedStyle(document.getElementById('class-filter')).display,
      groupWidth: Math.round(document.getElementById('group-control').getBoundingClientRect().width),
    }
  })

  const toolbarTops = result.toolbar.controls.map(({ top }) => top)
  const driverTops = result.drivers.chips.map(({ top }) => top)
  // Native selects can sit one device pixel lower than buttons while remaining on
  // the same flex line. A real wrap is separated by the control height, not 1px.
  const toolbarRows = Math.max(...toolbarTops) - Math.min(...toolbarTops) <= 2 ? 1 : 2
  const driversRows = Math.max(...driverTops) - Math.min(...driverTops) <= 2 ? 1 : 2
  const toolbarFits = result.toolbar.controls.every(({ right }) => right <= result.toolbar.right)
  const failures = []
  if (toolbarRows !== 1) failures.push(`toolbar uses ${toolbarRows} rows`)
  if (!toolbarFits) failures.push('toolbar controls do not fit inside the pane')
  if (result.toolbar.mergedFilterDisplay === 'none') failures.push('merged filter is hidden')
  if (result.toolbar.accountFilterDisplay !== 'none')
    failures.push('account select is still visible')
  if (result.toolbar.classFilterDisplay !== 'none') failures.push('type chips are still visible')
  if (result.toolbar.popover.width > 300 || result.toolbar.popover.height > 420)
    failures.push('desktop filter popover is not compact')
  if (result.toolbar.group.width !== 34 || result.toolbar.group.iconDisplay === 'none' || result.toolbar.group.selectOpacity !== '0')
    failures.push('narrow grouping control is not icon-only')
  if (wideResult.mergedFilterDisplay !== 'none')
    failures.push('wide pane still shows merged filter')
  if (wideResult.accountFilterDisplay === 'none') failures.push('wide pane lost account select')
  if (wideResult.classFilterDisplay === 'none') failures.push('wide pane lost type chips')
  if (wideResult.groupWidth <= 34) failures.push('wide pane did not restore the labelled grouping control')
  if (driversRows !== 1) failures.push(`driver chips use ${driversRows} rows`)
  if (result.drivers.overflowX !== 'auto')
    failures.push('drivers strip is not horizontally scrollable')
  if (result.drivers.scrollWidth <= result.drivers.clientWidth) {
    failures.push('overflowing drivers did not produce a scrollable strip')
  }
  if (failures.length) {
    throw new Error(
      `600px Investments pane must keep both rows single-line: ${failures.join('; ')}\n${JSON.stringify(result, null, 2)}`,
    )
  }

  console.log(JSON.stringify({ narrow: result, wide: wideResult }, null, 2))
} finally {
  await browser.close()
}
