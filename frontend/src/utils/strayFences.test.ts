import { describe, expect, it } from 'vitest'
import { repairStrayFences } from './strayFences'

const FIRST = 'You have S$788 left across the two, Tom: S$500 in Groceries and S$288 in Dining out.'
const SECOND = 'Those are the only amounts envelope-funded for food and fun.'

describe('repairStrayFences', () => {
  it('frees a paragraph a fence was glued onto (the info-string swallow)', () => {
    expect(repairStrayFences(`\`\`\`${FIRST}\n\n${SECOND}`)).toBe(`${FIRST}\n\n${SECOND}`)
    expect(repairStrayFences(`\n\`\`\`${FIRST}\n\n${SECOND}`)).toBe(`${FIRST}\n\n${SECOND}`)
  })

  it('drops a bare fence that opens the message and never closes', () => {
    expect(repairStrayFences(`\`\`\`\n\n${FIRST}\n\n${SECOND}`)).toBe(`${FIRST}\n\n${SECOND}`)
  })

  it('drops a bare fence the message ends on', () => {
    expect(repairStrayFences(`${FIRST}\n\`\`\``)).toBe(FIRST)
    expect(repairStrayFences(`${FIRST}\n\n\`\`\`\n`).trim()).toBe(FIRST)
  })

  it('leaves code that is still streaming alone', () => {
    const typed = 'Run this:\n\n```python\nprint(1)'
    expect(repairStrayFences(typed)).toBe(typed)
    const bare = 'Run this:\n\n```\nls -la'
    expect(repairStrayFences(bare)).toBe(bare)
  })

  it('never touches a balanced message', () => {
    const code = `${FIRST}\n\n\`\`\`\nls -la\n\`\`\`\n\n${SECOND}`
    expect(repairStrayFences(code)).toBe(code)
    expect(repairStrayFences('```\ncode\n```')).toBe('```\ncode\n```')
  })

  it('does not read inline code spans as fences', () => {
    const inline = 'Use ```x``` or `y` here.'
    expect(repairStrayFences(inline)).toBe(inline)
    expect(repairStrayFences('no fences at all')).toBe('no fences at all')
  })

  it('repairs only the fence that is still open', () => {
    expect(repairStrayFences(`\`\`\`\ncode\n\`\`\`\n\n\`\`\`${SECOND}`)).toBe(
      `\`\`\`\ncode\n\`\`\`\n\n${SECOND}`,
    )
  })
})
