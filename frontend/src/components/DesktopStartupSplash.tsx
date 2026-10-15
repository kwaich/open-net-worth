export function DesktopStartupSplash() {
  const path = window.location.pathname
  const label =
    path === '/setup/restore'
      ? 'Preparing to restore your book…'
      : path.startsWith('/setup')
        ? 'Getting your setup ready…'
        : 'Opening your book…'

  return (
    <div className="desktop-startup-splash" role="status" aria-label={label}>
      <img src="/logo-mark.svg" alt="" data-builtin-logo width={64} height={64} />
      <div className="desktop-startup-progress">
        <span className="desktop-startup-spinner" aria-hidden="true" />
        <span>{label}</span>
      </div>
    </div>
  )
}
