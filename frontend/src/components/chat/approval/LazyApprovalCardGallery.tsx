import { Suspense, lazy } from 'react'

// The card gallery's fixtures are dev data; loading it lazily keeps them out of the app's
// main chunk — only /?gallery=cards ever fetches them.
const ApprovalCardGallery = lazy(() =>
  import('./ApprovalCardGallery').then((m) => ({ default: m.ApprovalCardGallery })),
)

export function LazyApprovalCardGallery() {
  return (
    <Suspense fallback={null}>
      <ApprovalCardGallery />
    </Suspense>
  )
}
