import { describe, it, expect } from 'vitest'
import { layoutTickRows, rowTitle, conditionChip, draftToBody, draftFromRecord } from './alertModel'

describe('layoutTickRows — tick labels never overprint', () => {
  it('keeps every label on the first row when the track is unmeasured (jsdom / first paint)', () => {
    expect(
      layoutTickRows(
        [
          { pct: 10, text: '$375' },
          { pct: 11, text: '$380' },
        ],
        0,
      ),
    ).toEqual([0, 0])
  })

  it('drops a label to the second row when it would touch its neighbour', () => {
    // 300px track: $375 at 18%, $380 at 19.7%, $395 at 24.6%, $510 at 95%
    const rows = layoutTickRows(
      [
        { pct: 18, text: '$375' },
        { pct: 19.7, text: '$380' },
        { pct: 24.6, text: '$395' },
        { pct: 95, text: '$510' },
      ],
      300,
    )
    expect(rows).toEqual([0, 1, 2, 0])
  })

  it('reuses the first row once a label clears the pile-up', () => {
    const rows = layoutTickRows(
      [
        { pct: 18, text: '$375' },
        { pct: 19.7, text: '$380' },
        { pct: 40, text: '$430' },
      ],
      300,
    )
    expect(rows).toEqual([0, 1, 0])
  })

  it('leaves well-spread labels on one row regardless of input order', () => {
    const rows = layoutTickRows(
      [
        { pct: 90, text: '$285' },
        { pct: 5, text: '$145' },
        { pct: 50, text: '$198' },
      ],
      620,
    )
    expect(rows).toEqual([0, 0, 0])
  })
})

describe('alertModel — row text and bodies', () => {
  it('names a % move alert by its direction and size when unnamed', () => {
    expect(rowTitle({ op: '<=', value: 5, kind: 'move' }, '$')).toBe('Falls 5.00% in a day')
    expect(conditionChip({ op: '<=', value: 5, kind: 'move' })).toEqual({ sym: '↓', text: '5.00%' })
  })

  it('round-trips a strict op through an unedited draft and drops it once the direction changes', () => {
    const d = draftFromRecord({ id: 'a', op: '>', value: 150 })
    expect(draftToBody(d).op).toBe('>')
    expect(draftToBody({ ...d, op: '<=' }).op).toBe('<=')
  })
})
