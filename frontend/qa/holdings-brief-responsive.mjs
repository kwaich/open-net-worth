import assert from 'node:assert/strict'
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { chromium, webkit } from 'playwright'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'

const out = fileURLToPath(new URL('./artifacts/holdings-brief-responsive/', import.meta.url))
mkdirSync(out, { recursive: true })
const server = await createServer({
  configFile: false,
  root: fileURLToPath(new URL('../', import.meta.url)),
  plugins: [react()],
  server: { host: '127.0.0.1', port: 8093, strictPort: true },
})

try {
  await server.listen()
  for (const [name, engine] of Object.entries({ chromium, webkit })) {
    const browser = await engine.launch({ headless: true })
    try {
      const page = await browser.newPage()
      for (const width of [320, 360, 390, 430, 600, 640, 1024]) {
        await page.setViewportSize({ width, height: 844 })
        await page.goto('http://127.0.0.1:8093/qa/holdings-brief/')
        await page.getByRole('button', { name: /Mark all read/ }).waitFor()
        const layout = await page.evaluate(() => {
          const rect = (el) => {
            const r = el.getBoundingClientRect()
            return {
              x: r.x,
              y: r.y,
              width: r.width,
              height: r.height,
              right: r.right,
              overflowing: el.scrollWidth > el.clientWidth + 1,
            }
          }
          const bar = document.querySelector('.intel-desk-bar')
          return {
            viewport: innerWidth,
            pageWidth: document.documentElement.scrollWidth,
            bar: rect(bar),
            statuses: [...bar.querySelectorAll('.intel-desk-status-btn')].map(rect),
            sort: rect(bar.querySelector('.intel-sort')),
            actions: [...bar.querySelectorAll('.intel-feed-tool')].map(rect),
            archiveStyle: {
              borderColor: getComputedStyle(bar.querySelector('.intel-feed-tool--archive'))
                .borderColor,
              background: getComputedStyle(bar.querySelector('.intel-feed-tool--archive'))
                .backgroundColor,
            },
          }
        })
        assert.ok(layout.pageWidth <= width, `${name} ${width}: page overflow`)
        for (const r of [...layout.statuses, layout.sort, ...layout.actions]) {
          assert.ok(!r.overflowing, `${name} ${width}: control contents overflow`)
          assert.ok(
            r.x >= layout.bar.x && r.right <= layout.bar.right + 1,
            `${name} ${width}: control outside card`,
          )
        }
        if (width < 640) {
          assert.equal(layout.statuses[0].y, layout.statuses[1].y, 'first status row')
          assert.equal(layout.statuses[2].y, layout.statuses[3].y, 'second status row')
          assert.equal(layout.sort.y, layout.actions[0].y, 'sort and mark-read share a row')
          assert.ok(
            Math.abs(layout.sort.width - layout.actions[0].width) <= 1,
            'sort and mark-read have equal width',
          )
          assert.ok(
            layout.actions[1].y >= layout.actions[0].y + layout.actions[0].height,
            'view archived sits below the main controls',
          )
          assert.equal(
            layout.archiveStyle.borderColor,
            'rgba(0, 0, 0, 0)',
            'archive has no visible border',
          )
          assert.equal(
            layout.archiveStyle.background,
            'rgba(0, 0, 0, 0)',
            'archive has no button fill',
          )
        }
        await page.screenshot({ path: `${out}/${name}-${width}.png` })
        console.log(`${name} ${width}: responsive layout passed`)
      }
      await page.setViewportSize({ width: 320, height: 844 })
      await page.goto('http://127.0.0.1:8093/qa/holdings-brief/?scan')
      await page.getByText('News not scanned yet').waitFor()
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      await page.getByRole('combobox', { name: 'Sort the list' }).selectOption('today')
      await page.getByRole('button', { name: /17 needs attention/ }).click()
      assert.equal(await page.getByLabel('Focused story').textContent(), 'synthetic-0')
      await page.getByRole('button', { name: /View archived 81/ }).click()
      await page.getByText('Archived example stories').waitFor()
      await page.getByRole('button', { name: /Back 81/ }).click()
      await page.getByRole('button', { name: /Mark all read/ }).click()
      assert.equal(await page.getByRole('button', { name: /Mark all read/ }).count(), 0)
      console.log(`${name}: scan note, sort, status jump, archive/back and mark-read passed`)
    } finally {
      await browser.close()
    }
  }
} finally {
  await server.close()
}
