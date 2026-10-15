import type { ReactNode } from 'react'

export interface PageHeaderProps {
  title: ReactNode
  subtitle?: ReactNode
  /** Right-aligned actions; drop below the title under the mobile breakpoint. */
  actions?: ReactNode
  className?: string
}

/**
 * PageHeader — the title + optional subtitle + right-aligned actions row that
 * tabs hand-roll today (.tab-header / sticky .profile-header), with a built-in
 * mobile rule: below 640px the actions wrap under the title (RFC §2). Adopt
 * opportunistically during a screen's migration — purely additive.
 */
export function PageHeader({ title, subtitle, actions, className }: PageHeaderProps) {
  const classes = ['ui-page-header', className || ''].filter(Boolean).join(' ')
  return (
    <header className={classes}>
      <div className="ui-page-header__titles">
        <h1 className="ui-page-header__title">{title}</h1>
        {subtitle && <div className="ui-page-header__subtitle">{subtitle}</div>}
      </div>
      {actions && <div className="ui-page-header__actions">{actions}</div>}
    </header>
  )
}
