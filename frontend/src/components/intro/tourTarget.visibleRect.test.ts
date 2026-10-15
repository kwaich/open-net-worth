// @vitest-environment jsdom
import { expect, it } from 'vitest'
import { visibleRect } from './tourTarget'

const box = (left: number, top: number, right: number, bottom: number) =>
  ({ left, top, right, bottom, width: right - left, height: bottom - top }) as DOMRect

it('cuts a target to the scrolling container it sits in (a row wider than its table)', () => {
  const table = document.createElement('div')
  table.style.overflowX = 'auto'
  table.getBoundingClientRect = () => box(500, 300, 900, 600)
  const row = document.createElement('div')
  row.getBoundingClientRect = () => box(500, 350, 1600, 390)
  table.appendChild(row)
  document.body.appendChild(table)
  const r = visibleRect(row)
  expect([r.left, r.right, r.top, r.bottom]).toEqual([500, 900, 350, 390])
  table.remove()
})

it('leaves a target alone when nothing around it clips', () => {
  const wrap = document.createElement('div')
  wrap.getBoundingClientRect = () => box(0, 0, 0, 0)
  const el = document.createElement('button')
  el.getBoundingClientRect = () => box(20, 300, 120, 328)
  wrap.appendChild(el)
  document.body.appendChild(wrap)
  const r = visibleRect(el)
  expect([r.left, r.right, r.top, r.bottom]).toEqual([20, 120, 300, 328])
  wrap.remove()
})
