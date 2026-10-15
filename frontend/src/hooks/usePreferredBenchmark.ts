// The user's "vs the market" index — `profile.preferred_benchmark`, the ONE benchmark the
// Performance tab, the Overview widget, the YTD stat, the Intel Holdings brief and the watch
// pulse all compare against. Never a per-browser copy: a localStorage mirror left each surface
// free to compare against a different index from the one Intel was using.

import { useProfile } from './serverState'

export const DEFAULT_BENCHMARK = 'SPY'

export function usePreferredBenchmark(): string {
  const { data } = useProfile()
  const key = data?.preferred_benchmark
  return typeof key === 'string' && key ? key : DEFAULT_BENCHMARK
}
