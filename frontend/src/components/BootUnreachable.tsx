import { useBrand } from '../brand-context'
import { ReportProblemButton } from './ReportProblemButton'

// Shown when a boot-time request (auth or setup status) gets no answer. Without it a hung
// server left the splash or the blank boot ground up forever, with no way to try again.

export function BootUnreachable({ onRetry, busy }: { onRetry: () => void; busy?: boolean }) {
  const brand = useBrand()
  return (
    <div className="auth-splash" role="alert">
      <div className="auth-splash-brand">{brand.app_name.toUpperCase()}</div>
      <div className="boot-unreachable-text">Can&rsquo;t reach {brand.app_name}.</div>
      <div className="boot-unreachable-actions">
        <button type="button" className="btn-secondary" disabled={busy} onClick={onRetry}>
          {busy ? 'Retrying…' : 'Retry'}
        </button>
        <ReportProblemButton error={`Can't reach ${brand.app_name}.`} />
      </div>
    </div>
  )
}
