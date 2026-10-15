import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchInsights, fetchOutlook, fetchScenarioOdds } from './outlookApi'

const authFetch = vi.fn<typeof fetch>()
vi.mock('../../../api/client', () => ({
  authFetch: (...args: Parameters<typeof fetch>) => authFetch(...args),
}))

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }))

const BAND = { year: 0, age: 44, p10: 1, p25: 2, p50: 3, p75: 4, p90: 5 }

beforeEach(() => authFetch.mockReset())

describe('outlookApi', () => {
  it('posts the levers to the backend engine and maps its fan onto the screen shape', async () => {
    authFetch.mockImplementation(() =>
      json({
        result: {
          bands: [BAND],
          retire_year: 18,
          horizon_years: 46,
          n_paths: 5000,
          success_probability: 0.7234,
          depletion_probability: 0.2766,
          nest_egg_at_retire: 1_200_000,
          nest_egg_band: BAND,
          median_ending: 900_000,
          p10_ending: 10_000,
          p90_ending: 3_000_000,
          outcomes: null,
        },
      }),
    )

    const { result, outcomes } = await fetchOutlook({ retireAge: 60 }, { quick: true })

    const [url, init] = authFetch.mock.calls[0]!
    expect(url).toBe('/api/plan/outlook')
    expect(JSON.parse(String(init?.body))).toEqual({
      levers: { retireAge: 60 },
      quick: true,
      outcomes: false,
    })
    expect(result).toEqual({
      bands: [BAND],
      retireYear: 18,
      horizonYears: 46,
      nPaths: 5000,
      successProbability: 0.7234,
      depletionProbability: 0.2766,
      nestEggAtRetire: 1_200_000,
      nestEggBand: BAND,
      medianEnding: 900_000,
      p10Ending: 10_000,
      p90Ending: 3_000_000,
    })
    expect(outcomes).toBeNull()
  })

  it('maps the insights', async () => {
    authFetch.mockImplementation(() =>
      json({
        earliest: { age: 57, odds: 0.81 },
        extra_save: 500,
        gap_save: null,
        number_age: 61.3,
      }),
    )
    expect(await fetchInsights({})).toEqual({
      earliest: { age: 57, odds: 0.81 },
      extraSave: 500,
      gapSave: null,
      numberAge: 61.3,
    })
  })

  it('reads the live shelf odds', async () => {
    authFetch.mockImplementation(() => json({ odds: { s1: 64 } }))
    expect(await fetchScenarioOdds()).toEqual({ s1: 64 })
  })

  it('throws on a refused request so the screen can offer a retry', async () => {
    authFetch.mockImplementation(() => json({ detail: 'nope' }, 422))
    await expect(fetchOutlook({})).rejects.toThrow('HTTP 422')
    await expect(fetchInsights({})).rejects.toThrow('HTTP 422')
    await expect(fetchScenarioOdds()).rejects.toThrow('HTTP 422')
  })
})
