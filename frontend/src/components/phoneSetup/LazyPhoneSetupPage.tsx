import { Suspense, lazy } from 'react'

// Only a phone opening /phone-setup ever fetches this chunk.
const PhoneSetupPage = lazy(() =>
  import('./PhoneSetupPage').then((m) => ({ default: m.PhoneSetupPage })),
)

export function LazyPhoneSetupPage() {
  return (
    <Suspense fallback={null}>
      <PhoneSetupPage />
    </Suspense>
  )
}
