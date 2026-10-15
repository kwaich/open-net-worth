// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { _resetBusForTests, onDataChanged } from './bus'
import { connectDataEvents, readEvents } from './dataEventsStream'

vi.mock('../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../api/client'

function sse(frames: string[], opts: { keepOpen?: boolean } = {}): ReadableStream<Uint8Array> {
  const enc = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const f of frames) controller.enqueue(enc.encode(f))
      if (!opts.keepOpen) controller.close()
    },
  })
}

const frame = (domains: string[]) =>
  `data: ${JSON.stringify({ type: 'data_changed', domains })}\n\n`

describe('readEvents', () => {
  it('parses data frames split across chunks and ignores pings, retry and junk', async () => {
    const got: string[][] = []
    const whole = `retry: 3000\n\n: ping\n\n${frame(['ledger'])}data: {not json\n\n${frame(['budget'])}`
    const chunks = [whole.slice(0, 17), whole.slice(17, 40), whole.slice(40)]
    await readEvents(sse(chunks), (d) => got.push(d))
    expect(got).toEqual([['ledger'], ['budget']])
  })
})

describe('connectDataEvents', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    _resetBusForTests()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.mocked(authFetch).mockReset()
  })

  it('merges a burst into one bus emit, and emits * after a reconnect', async () => {
    const emits: string[][] = []
    onDataChanged((d) => emits.push([...d].sort()))
    vi.mocked(authFetch)
      .mockResolvedValueOnce(new Response(sse([frame(['ledger']), frame(['accounts'])])))
      .mockResolvedValueOnce(new Response(sse([], { keepOpen: true })))

    const stop = connectDataEvents()
    await vi.advanceTimersByTimeAsync(300)
    expect(emits).toEqual([['accounts', 'ledger']])

    // The first stream closed (server restart) — the reconnect can't know what it missed.
    await vi.advanceTimersByTimeAsync(2_500)
    expect(authFetch).toHaveBeenCalledTimes(2)
    expect(emits.at(-1)).toEqual(['*'])
    stop()
  })

  it('stops reconnecting once disconnected', async () => {
    vi.mocked(authFetch).mockResolvedValue(new Response(sse([])))
    const stop = connectDataEvents()
    await vi.advanceTimersByTimeAsync(10)
    stop()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(authFetch).toHaveBeenCalledTimes(1)
  })
})
