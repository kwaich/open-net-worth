// The Details section's reading of an asset's facts — pure, synthetic data only.
import { describe, expect, it } from 'vitest'
import { joinFacts, labelList, splitFacts } from './detailsFacts'

describe('splitFacts', () => {
  it("matches the category's guided labels case- and apostrophe-insensitively, in guided order", () => {
    const { guided, other } = splitFacts('watch', [
      ['serial NUMBER', 'X1'],
      ["Where it's kept", 'Home safe'],
      ['Strap', 'Oyster'],
    ])
    expect(guided.map((g) => [g.prompt[0], g.value])).toEqual([
      ['Where it’s kept', 'Home safe'],
      ['Brand & reference', ''],
      ['Serial number', 'X1'],
      ['Box & papers', ''],
    ])
    expect(other).toEqual([['Strap', 'Oyster']])
  })

  it('keeps a fact the category does not guide as another detail — nothing is dropped', () => {
    const facts: [string, string][] = [
      ['Plate number', 'SLA 4417 X'],
      ['Tenure', 'Freehold'],
    ]
    const { guided, other } = splitFacts('vehicle', facts)
    expect(guided.find((g) => g.prompt[0] === 'Plate number')?.value).toBe('SLA 4417 X')
    expect(other).toEqual([['Tenure', 'Freehold']])
  })

  it('keeps a second fact under the same guided label as another detail', () => {
    const { guided, other } = splitFacts('property', [
      ['Tenure', 'Freehold'],
      ['tenure', '99-year'],
    ])
    expect(guided.find((g) => g.prompt[0] === 'Tenure')?.value).toBe('Freehold')
    expect(other).toEqual([['tenure', '99-year']])
  })
})

describe('joinFacts', () => {
  it('saves guided values under their own labels first, then the others, blanks dropped', () => {
    const { guided } = splitFacts('property', [])
    const filled = guided.map((g) => ({
      ...g,
      value: g.prompt[0] === 'Tenure' ? ' Freehold ' : '',
    }))
    expect(
      joinFacts(filled, [
        ['Parking', 'Lot 12'],
        ['', ''],
      ]),
    ).toEqual([
      ['Tenure', 'Freehold'],
      ['Parking', 'Lot 12'],
    ])
  })

  it('round-trips: splitting what it saved gives the same guided values and other details', () => {
    const facts: [string, string][] = [
      ['Address or unit', '#14-02, 1 Test Rise'],
      ['Parking', 'Lot 12'],
    ]
    const { guided, other } = splitFacts('property', facts)
    expect(splitFacts('property', joinFacts(guided, other))).toEqual({ guided, other })
  })
})

describe('labelList', () => {
  it('lowers each label for a sentence but keeps an acronym', () => {
    expect(labelList(['Plate number', 'COE expires'])).toBe('plate number, COE expires')
  })

  it('names at most two, then says how many more', () => {
    expect(labelList(['Plate number', 'COE expires', 'Mileage'])).toBe(
      'plate number, COE expires and 1 more',
    )
    expect(labelList(['Address or unit', 'Tenure', 'Floor area', 'Tenanted'])).toBe(
      'address or unit, tenure and 2 more',
    )
  })
})
