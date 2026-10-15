import { Skeleton } from './Skeleton'
import { useSkeletonHandoff } from './skeletonHandoff'

export type TabSkeletonVariant =
  | 'accounts'
  | 'assets'
  | 'automations'
  | 'activity'
  | 'budget'
  | 'builder'
  | 'cashflow'
  | 'cards'
  | 'categories'
  | 'commitments'
  | 'connectors'
  | 'dashboard'
  | 'equity'
  | 'help'
  | 'holdings'
  | 'income'
  | 'inbox'
  | 'intel'
  | 'options'
  | 'performance'
  | 'proportional'
  | 'skills'
  | 'workspace'
  | 'list'
  | 'rail'

export interface TabSkeletonProps {
  label: string
  variant: TabSkeletonVariant
  /** Use when the tab already keeps its real masthead visible while its body loads. */
  header?: boolean
  className?: string
}

const rows = (count: number, key: string) =>
  Array.from({ length: count }, (_, index) => (
    <div className="ui-tab-skeleton__row" key={`${key}-${index}`}>
      <Skeleton circle width={32} height={32} />
      <div className="ui-tab-skeleton__row-copy">
        <Skeleton width={index % 2 === 0 ? '44%' : '58%'} />
        <Skeleton width={index % 2 === 0 ? '24%' : '32%'} height={10} />
      </div>
      <Skeleton width={72} height={20} radius={999} />
    </div>
  ))

const PAGE_HEADER_VARIANTS = new Set<TabSkeletonVariant>([
  'assets',
  'proportional',
  'automations',
  'budget',
  'builder',
  'commitments',
  'connectors',
  'dashboard',
  'inbox',
  'intel',
  'skills',
])

/** Title + subtitle bars sized to the real PageHeader's line boxes (27px title, 18px sub), so
 *  the real text lands exactly where the bars were. */
function TitleBlock({ title, sub, rd }: { title: number; sub?: number; rd?: boolean }) {
  // `rd`: the rd-masthead type scale (Connectors) — a 24px title line and a 16px subtitle.
  return (
    <div className="ui-tab-skeleton__page-head-title">
      <Skeleton className="ui-tab-skeleton__ph-title" width={title} height={rd ? 18 : 21} />
      {sub != null && (
        <Skeleton
          className={rd ? 'ui-tab-skeleton__ph-sub--rd' : 'ui-tab-skeleton__ph-sub'}
          width={sub}
          height={rd ? 11 : 12}
        />
      )}
      {/* A phone wraps the subtitle onto a second line. */}
      {sub != null && !rd && (
        <Skeleton className="ui-tab-skeleton__ph-sub-wrap" width="34%" height={12} />
      )}
    </div>
  )
}

function PageHeaderSkeleton({ variant }: { variant: TabSkeletonVariant }) {
  // 50/30/20 is a Plan method, so it wears Plan's own masthead.
  const headVariant = variant === 'proportional' ? 'budget' : variant
  const className = `ui-tab-skeleton__${headVariant}-page-head${variant === 'assets' ? ' as-head' : ''}`

  if (headVariant === 'budget') {
    // Plan: title row with FIRE Settings, then the sub-tab strip with the method menu.
    return (
      <div className={`${className} ui-tab-skeleton__ph-stack`} aria-hidden="true">
        <div className="ui-tab-skeleton__ph-row">
          <TitleBlock title={52} sub={470} />
          <Skeleton className="ui-tab-skeleton__ph-action" width={107} height={34} radius={8} />
        </div>
        <div className="ui-tab-skeleton__ph-tabs-row">
          <div className="ui-tab-skeleton__ph-tabs">
            {[50, 70, 56].map((width, index) => (
              <Skeleton key={index} width={width} height={14} />
            ))}
          </div>
          <Skeleton width={121} height={34} radius={8} />
          <Skeleton width={32} height={26} radius={8} />
        </div>
      </div>
    )
  }

  if (variant === 'connectors') {
    return (
      <div className={`${className} ui-tab-skeleton__ph-stack`} aria-hidden="true">
        <TitleBlock title={116} sub={300} rd />
        <div className="ui-tab-skeleton__ph-toolbar">
          <Skeleton width={220} height={30} radius={999} />
          <Skeleton width={133} height={30} radius={999} />
        </div>
        <div className="ui-tab-skeleton__ph-tabs-row">
          <div className="ui-tab-skeleton__ph-tabs">
            {[34, 64, 88, 34, 36].map((width, index) => (
              <Skeleton key={index} width={width} height={14} />
            ))}
          </div>
          <Skeleton width={196} height={27} radius={8} />
        </div>
      </div>
    )
  }

  if (variant === 'automations') {
    // Title + subtitle over the masthead divider; the list toolbar belongs to the body.
    return (
      <div className={`${className} ui-tab-skeleton__ph-divided`} aria-hidden="true">
        <TitleBlock title={150} sub={560} />
      </div>
    )
  }

  const compact = variant === 'assets'
  return (
    <div className={className} aria-hidden="true">
      <TitleBlock
        title={compact ? 76 : variant === 'skills' ? 62 : 136}
        sub={compact ? undefined : variant === 'skills' ? 380 : 270}
      />
      {variant === 'skills' ? (
        <Skeleton className="ui-tab-skeleton__ph-action--sm" width={84} height={27} radius={8} />
      ) : (
        <Skeleton
          width={compact ? 108 : variant === 'dashboard' ? 106 : 116}
          height={36}
          radius={8}
        />
      )}
    </div>
  )
}

/**
 * Layout-shaped first-load placeholder for a tab. It intentionally uses the shared Skeleton
 * primitive so shimmer and reduced-motion behaviour stay identical across the app.
 */
export function TabSkeleton({ label, variant, header = true, className }: TabSkeletonProps) {
  const hasPageHeader = header && PAGE_HEADER_VARIANTS.has(variant)
  const handoff = useSkeletonHandoff()
  return (
    <section
      className={`ui-tab-skeleton ui-tab-skeleton--${variant}${className ? ` ${className}` : ''}`}
      style={handoff}
      role="status"
      aria-label={label}
      aria-busy="true"
    >
      <span className="ui-visually-hidden">{label}</span>
      {hasPageHeader && <PageHeaderSkeleton variant={variant} />}
      {header &&
        !hasPageHeader &&
        variant !== 'accounts' &&
        variant !== 'holdings' &&
        (variant === 'assets' ? (
          <div className="ui-tab-skeleton__header" aria-hidden="true">
            <Skeleton width={76} height={28} />
            <Skeleton width={108} height={38} radius={8} />
          </div>
        ) : (
          <div className="ui-tab-skeleton__header" aria-hidden="true">
            <div>
              <Skeleton width={154} height={28} />
              <Skeleton width={270} height={13} />
            </div>
            <Skeleton width={106} height={34} radius={8} />
          </div>
        ))}

      {variant === 'accounts' && (
        <>
          <div className="ui-tab-skeleton__accounts-layout" aria-hidden="true">
            <aside className="ui-tab-skeleton__accounts-sidebar">
              <div className="ui-tab-skeleton__accounts-side-head">
                <Skeleton width="46%" height={16} />
                <Skeleton width="72%" height={28} />
                <Skeleton width="100%" height={6} radius={999} />
              </div>
              {[0, 1].map((group) => (
                <div className="ui-tab-skeleton__accounts-group" key={group}>
                  <Skeleton width="34%" height={11} />
                  {[0, 1, 2].map((row) => (
                    <div className="ui-tab-skeleton__accounts-nav-row" key={row}>
                      <Skeleton width={28} height={28} radius={8} />
                      <div>
                        <Skeleton width={row === 1 ? '74%' : '54%'} height={13} />
                        <Skeleton width="42%" height={10} />
                      </div>
                    </div>
                  ))}
                </div>
              ))}
              <Skeleton width="100%" height={36} radius={8} />
            </aside>
            <div className="ui-tab-skeleton__accounts-main">
              <div className="ui-tab-skeleton__accounts-head">
                <div>
                  <Skeleton width={144} height={28} />
                  <Skeleton width={218} height={13} />
                </div>
                <Skeleton width={112} height={36} radius={8} />
              </div>
              <div className="ui-tab-skeleton__accounts-summary">
                {[0, 1, 2, 3].map((index) => (
                  <div className="ui-tab-skeleton__account-summary" key={index}>
                    <Skeleton width="56%" height={11} />
                    <Skeleton width="76%" height={25} />
                  </div>
                ))}
              </div>
              <div className="ui-tab-skeleton__accounts-filters">
                <Skeleton width={224} height={36} radius={8} />
                <Skeleton width={116} height={36} radius={8} />
              </div>
              <div className="ui-tab-skeleton__accounts-register">
                {[0, 1, 2, 3, 4, 5, 6, 7].map((index) => (
                  <div className="ui-tab-skeleton__account-register-row" key={index}>
                    <Skeleton width="26%" height={13} />
                    <Skeleton width={index % 2 ? '40%' : '54%'} height={13} />
                    <Skeleton width="16%" height={13} />
                    <Skeleton width="14%" height={13} />
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="ui-tab-skeleton__accounts-mobile" aria-hidden="true">
            <div className="ui-tab-skeleton__accounts-mobile-head">
              <Skeleton width={38} height={38} radius={10} />
              <Skeleton width={96} height={28} />
              <Skeleton width={138} height={38} radius={8} />
              <Skeleton width={44} height={38} radius={10} />
            </div>
            <div className="ui-tab-skeleton__accounts-mobile-cash">
              <Skeleton width="18%" height={12} />
              <div className="ui-tab-skeleton__accounts-mobile-equation">
                {[0, 1, 2].map((index) => (
                  <div key={index}>
                    <Skeleton width="92%" height={18} />
                    <Skeleton width="62%" height={10} />
                  </div>
                ))}
              </div>
              <div className="ui-tab-skeleton__accounts-mobile-cash-meta">
                <Skeleton width={78} height={12} />
                <Skeleton width={62} height={12} />
              </div>
            </div>
            <div className="ui-tab-skeleton__accounts-mobile-controls">
              <Skeleton height={38} radius={10} />
              <Skeleton width={40} height={38} radius={10} />
              <Skeleton width={70} height={38} radius={10} />
            </div>
            <div className="ui-tab-skeleton__accounts-mobile-register">
              <div className="ui-tab-skeleton__accounts-mobile-count">
                <Skeleton width={20} height={20} radius={6} />
                <Skeleton width={118} height={12} />
              </div>
              {[0, 1].map((group) => (
                <div className="ui-tab-skeleton__accounts-mobile-group" key={group}>
                  <Skeleton width={142} height={18} />
                  {[0, 1, 2].map((row) => (
                    <div className="ui-tab-skeleton__accounts-mobile-row" key={row}>
                      <Skeleton width={20} height={20} radius={6} />
                      <div>
                        <Skeleton width={row === 1 ? '58%' : '72%'} height={17} />
                        <Skeleton width="84%" height={12} />
                        <Skeleton width={row === 2 ? '94%' : '70%'} height={11} />
                      </div>
                      <div>
                        <Skeleton width={66} height={17} />
                        <Skeleton width={42} height={10} />
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {variant === 'assets' && (
        <div className="ui-tab-skeleton__assets" aria-hidden="true">
          <div className="ui-tab-skeleton__asset-kpis">
            {[0, 1, 2, 3].map((index) => (
              <div className="ui-tab-skeleton__asset-kpi" key={index}>
                <Skeleton width={index % 2 === 0 ? '48%' : '55%'} height={11} />
                <Skeleton width={index === 2 ? '64%' : '72%'} height={24} />
              </div>
            ))}
          </div>
          <div className="ui-tab-skeleton__filter-row">
            {[72, 94, 116, 104, 88].map((width, index) => (
              <Skeleton
                className="ui-tab-skeleton__filter-chip"
                key={index}
                width={width}
                height={38}
                radius={999}
              />
            ))}
          </div>
          <div className="ui-tab-skeleton__asset-cards">
            {[0, 1, 2, 3, 4].map((index) => (
              <div className="ui-tab-skeleton__asset-card" key={index}>
                <Skeleton width={62} height={62} radius={14} />
                <div className="ui-tab-skeleton__asset-copy">
                  <Skeleton width={index === 3 ? '72%' : '54%'} height={17} />
                  <Skeleton width="42%" height={12} />
                  <Skeleton width="34%" height={23} />
                </div>
                {index === 1 || index === 3 ? <Skeleton width={44} height={13} /> : null}
              </div>
            ))}
          </div>
        </div>
      )}

      {variant === 'budget' && (
        <div className="ui-tab-skeleton__budget" aria-hidden="true">
          <div className="ui-tab-skeleton__budget-summary">
            <Skeleton width={110} height={11} />
            <Skeleton width={96} height={24} />
            <Skeleton width={180} height={12} />
          </div>
          <div className="ui-tab-skeleton__budget-toolbar">
            {[63, 119, 103, 112, 134].map((width, index) => (
              <Skeleton key={index} width={width} height={29} radius={999} />
            ))}
            <Skeleton width={122} height={34} radius={8} />
          </div>
          <div className="ui-tab-skeleton__budget-layout">
            <div className="ui-tab-skeleton__budget-table">
              <div className="ui-tab-skeleton__budget-columns">
                {[64, 60, 52, 64].map((width, index) => (
                  <Skeleton key={index} width={width} height={10} />
                ))}
              </div>
              {[0, 1, 2, 3, 4, 5, 6].map((index) => (
                <div className="ui-tab-skeleton__budget-row" key={index}>
                  <Skeleton
                    width={index === 0 ? '34%' : `${[46, 30, 38, 42, 28, 36][index - 1]}%`}
                    height={13}
                  />
                  <Skeleton width={48} height={13} />
                  <Skeleton width={48} height={13} />
                  <Skeleton width={54} height={13} />
                </div>
              ))}
            </div>
            {/* The Summary / Auto-Assign rail beside the table on a wide screen (showRail). */}
            <aside className="ui-tab-skeleton__budget-rail">
              {[5, 4].map((count, card) => (
                <div className="ui-tab-skeleton__budget-rail-card" key={card}>
                  <Skeleton width={card ? 92 : 150} height={14} />
                  {Array.from({ length: count }, (_, index) => (
                    <div key={index}>
                      <Skeleton width={`${[52, 34, 30, 44, 58][index]}%`} height={12} />
                      {card === 0 && <Skeleton width={72} height={12} />}
                    </div>
                  ))}
                </div>
              ))}
            </aside>
          </div>
        </div>
      )}

      {variant === 'proportional' && (
        <div className="ui-tab-skeleton__prop" aria-hidden="true">
          <div className="ui-tab-skeleton__prop-bar">
            <Skeleton width={132} height={30} radius={8} />
            {/* Configure / Manage categories — icon-only on a phone, as in the real bar. */}
            <Skeleton className="ui-tab-skeleton__prop-barbtn" height={30} radius={8} />
            <Skeleton className="ui-tab-skeleton__prop-barbtn--wide" height={30} radius={8} />
          </div>
          <div className="ui-tab-skeleton__prop-hero">
            <Skeleton width={140} height={14} />
            <Skeleton width={90} height={28} />
            <Skeleton height={28} radius={999} />
            <div className="ui-tab-skeleton__prop-legend">
              {[0, 1, 2].map((index) => (
                <Skeleton key={index} width={110} height={14} />
              ))}
            </div>
          </div>
          {/* Desktop table and phone card list are both rendered; CSS shows the one the real
              view uses at this width (PropTable switches on the same 640px query). */}
          <div className="ui-tab-skeleton__prop-table">
            <div className="ui-tab-skeleton__prop-row ui-tab-skeleton__prop-row--head">
              {[72, 36, 52, 36].map((width, index) => (
                <Skeleton key={index} width={width} height={11} />
              ))}
            </div>
            {[0, 1, 2, 3, 4, 5, 6].map((index) => (
              <div className="ui-tab-skeleton__prop-row" key={index}>
                <Skeleton width={index % 3 === 0 ? '30%' : index % 2 ? '44%' : '36%'} height={14} />
                <Skeleton width={64} height={14} />
                <Skeleton width={72} height={14} />
                <Skeleton width={64} height={14} />
              </div>
            ))}
          </div>
          <div className="ui-tab-skeleton__prop-cards">
            {[0, 1, 2, 3, 4].map((index) => (
              <div className="ui-tab-skeleton__prop-card" key={index}>
                <div>
                  <Skeleton width={index % 2 ? '62%' : '48%'} height={15} />
                  <Skeleton width="36%" height={11} />
                </div>
                <Skeleton width={64} height={15} />
              </div>
            ))}
          </div>
        </div>
      )}

      {variant === 'commitments' && (
        <div className="ui-tab-skeleton__commitments" aria-hidden="true">
          <div className="ui-tab-skeleton__commitment-masthead">
            <div className="ui-tab-skeleton__commitment-hero">
              <Skeleton width="36%" height={13} />
              <Skeleton width="58%" height={34} />
              <Skeleton width="74%" height={12} />
            </div>
            <div className="ui-tab-skeleton__commitment-stats">
              {[0, 1, 2, 3].map((index) => (
                <div key={index}>
                  <Skeleton width="55%" height={11} />
                  <Skeleton width="76%" height={21} />
                </div>
              ))}
            </div>
          </div>
          <div className="ui-tab-skeleton__commitment-list">
            {[0, 1, 2, 3].map((index) => (
              <div className="ui-tab-skeleton__commitment-row" key={index}>
                <Skeleton circle width={38} height={38} />
                <div>
                  <Skeleton width={index === 2 ? '68%' : '42%'} height={16} />
                  <Skeleton width="62%" height={11} />
                </div>
                <Skeleton width={76} height={26} radius={999} />
              </div>
            ))}
          </div>
        </div>
      )}

      {variant === 'automations' && (
        <div className="ui-tab-skeleton__automations" aria-hidden="true">
          <div className="ui-tab-skeleton__automation-controls">
            <Skeleton className="ui-tab-skeleton__automation-search" />
            <Skeleton className="ui-tab-skeleton__automation-sort" />
            <Skeleton className="ui-tab-skeleton__automation-new" />
          </div>
          <div className="ui-tab-skeleton__automation-list">
            {[0, 1, 2, 3, 4, 5].map((index) => (
              <div className="ui-tab-skeleton__automation-card" key={index}>
                <div>
                  <Skeleton width={index % 3 === 1 ? '52%' : '38%'} height={14} />
                  <Skeleton width={index % 2 ? '66%' : '78%'} height={12} />
                  <Skeleton
                    className="ui-tab-skeleton__automation-chip"
                    width={index % 2 ? 150 : 96}
                    height={22}
                    radius={6}
                  />
                </div>
                <Skeleton width={4} height={16} radius={2} />
              </div>
            ))}
          </div>
        </div>
      )}

      {variant === 'skills' && (
        <div className="ui-tab-skeleton__skills" aria-hidden="true">
          <aside className="ui-tab-skeleton__skills-tree">
            <Skeleton height={36} radius={8} />
            {[0, 1, 2, 3, 4, 5, 6].map((index) => (
              <div className="ui-tab-skeleton__skills-tree-row" key={index}>
                <Skeleton width={6} height={10} radius={2} />
                <Skeleton width={[92, 84, 118, 96, 128, 58, 88][index]} height={12} />
              </div>
            ))}
          </aside>
          <div className="ui-tab-skeleton__skills-detail" />
        </div>
      )}

      {variant === 'connectors' && (
        <div className="ui-tab-skeleton__connectors" aria-hidden="true">
          {[3, 2].map((count, section) => (
            <div className="ui-tab-skeleton__connector-section" key={section}>
              <Skeleton width={section ? 44 : 96} height={13} />
              <div className="ui-tab-skeleton__connector-list">
                {Array.from({ length: count }, (_, index) => (
                  <div className="ui-tab-skeleton__connector-row" key={index}>
                    <Skeleton width={32} height={32} radius={8} />
                    <Skeleton width={[118, 96, 132][index]} height={14} />
                    <Skeleton width={78} height={13} />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {variant === 'dashboard' && (
        <div className="ui-tab-skeleton__dashboard" aria-hidden="true">
          <div className="ui-tab-skeleton__dashboard-hero">
            <Skeleton width="28%" height={14} />
            <Skeleton width="42%" height={36} />
          </div>
          <div className="ui-tab-skeleton__dashboard-widgets">
            {[0, 1, 2, 3, 4, 5].map((index) => (
              <div key={index}>
                <Skeleton width="48%" height={14} />
                <Skeleton width="72%" height={index % 2 ? 86 : 46} />
              </div>
            ))}
          </div>
        </div>
      )}

      {variant === 'inbox' && (
        <div className="ui-tab-skeleton__inbox" aria-hidden="true">
          <div className="ui-tab-skeleton__inbox-controls">
            <Skeleton width={178} height={34} radius={999} />
            <Skeleton width={112} height={34} radius={8} />
          </div>
          <div className="ui-tab-skeleton__inbox-sections">
            {[0, 1, 2].map((section) => (
              <div key={section}>
                <Skeleton width="22%" height={14} />
                {rows(2, `inbox-${section}`)}
              </div>
            ))}
          </div>
        </div>
      )}

      {variant === 'holdings' && (
        <div className="ui-tab-skeleton__holdings" aria-hidden="true">
          <div className="ui-tab-skeleton__holdings-masthead">
            <div className="ui-tab-skeleton__holdings-total">
              <Skeleton width="34%" height={11} />
              <Skeleton width="74%" height={30} />
              <Skeleton width="68%" height={13} />
            </div>
            <div className="ui-tab-skeleton__holdings-ytd">
              <Skeleton width="72%" height={11} />
              <Skeleton width="64%" height={21} />
              <Skeleton width="80%" height={12} />
            </div>
            <div className="ui-tab-skeleton__holdings-pl">
              <Skeleton width="52%" height={13} />
              <Skeleton width="38%" height={13} />
            </div>
          </div>
          <div className="ui-tab-skeleton__holdings-brief">
            <div className="ui-tab-skeleton__holdings-brief-head">
              <Skeleton width={28} height={28} radius={8} />
              <Skeleton width={110} height={16} />
              <Skeleton width={82} height={22} radius={999} />
              <Skeleton width={72} height={34} radius={8} />
            </div>
            <div className="ui-tab-skeleton__holdings-drivers">
              <Skeleton width={92} height={11} />
              {[76, 88, 70].map((width, index) => (
                <Skeleton key={index} width={width} height={28} radius={999} />
              ))}
            </div>
          </div>
          <div className="ui-tab-skeleton__holdings-tabs">
            {[72, 74, 102, 62, 70, 68, 118].map((width, index) => (
              <Skeleton key={index} width={width} height={20} />
            ))}
          </div>
          <div className="ui-tab-skeleton__holdings-controls">
            <Skeleton height={42} radius={10} />
            <Skeleton width={46} height={42} radius={10} />
            <Skeleton width={46} height={42} radius={10} />
            <Skeleton width={66} height={42} radius={10} />
          </div>
          <div className="ui-tab-skeleton__holdings-list-head">
            <Skeleton width={112} height={17} />
            <Skeleton width={184} height={13} />
          </div>
          <div className="ui-tab-skeleton__holdings-cards">
            {[0, 1, 2, 3, 4, 5].map((index) => (
              <div className="ui-tab-skeleton__holding-card" key={index}>
                <div>
                  <Skeleton width={index === 2 ? '38%' : '30%'} height={18} />
                  <Skeleton width="42%" height={12} />
                </div>
                <div>
                  <Skeleton width="72%" height={17} />
                  <Skeleton width="84%" height={13} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {variant === 'performance' && (
        <div className="ui-tab-skeleton__performance" aria-hidden="true">
          <div className="ui-tab-skeleton__performance-stats">
            {[0, 1, 2].map((index) => (
              <div key={index}>
                <Skeleton width="52%" height={11} />
                <Skeleton width="72%" height={24} />
              </div>
            ))}
          </div>
          <div className="ui-tab-skeleton__performance-chart">
            <Skeleton width="38%" height={13} />
            <Skeleton width="100%" height={230} />
          </div>
        </div>
      )}

      {variant === 'income' && (
        <div className="ui-tab-skeleton__income" aria-hidden="true">
          <div className="ui-tab-skeleton__income-summary">
            {[0, 1, 2].map((index) => (
              <div key={index}>
                <Skeleton width="48%" height={11} />
                <Skeleton width="76%" height={23} />
              </div>
            ))}
          </div>
          <div className="ui-tab-skeleton__income-columns">
            {[0, 1].map((column) => (
              <div key={column}>
                <Skeleton width="44%" height={17} />
                {rows(3, `income-${column}`)}
              </div>
            ))}
          </div>
        </div>
      )}

      {variant === 'activity' && (
        <div className="ui-tab-skeleton__activity" aria-hidden="true">
          <div className="ui-tab-skeleton__activity-summary">
            {[0, 1, 2].map((index) => (
              <div key={index}>
                <Skeleton width="48%" height={11} />
                <Skeleton width="70%" height={23} />
              </div>
            ))}
          </div>
          <div className="ui-tab-skeleton__activity-controls">
            <Skeleton width={220} height={34} radius={999} />
            <Skeleton width={188} height={34} radius={8} />
          </div>
          <div className="ui-tab-skeleton__activity-table">{rows(6, 'activity')}</div>
        </div>
      )}

      {variant === 'options' && (
        <div className="ui-tab-skeleton__options" aria-hidden="true">
          <div className="ui-tab-skeleton__options-summary">
            {[0, 1, 2].map((index) => (
              <div key={index}>
                <Skeleton width="54%" height={11} />
                <Skeleton width="76%" height={23} />
              </div>
            ))}
          </div>
          <div className="ui-tab-skeleton__options-table">
            {[0, 1, 2, 3].map((index) => (
              <div key={index}>
                <Skeleton width="32%" height={15} />
                <Skeleton width="66%" height={12} />
                <Skeleton width="42%" height={12} />
              </div>
            ))}
          </div>
        </div>
      )}

      {variant === 'equity' && (
        <div className="ui-tab-skeleton__equity" aria-hidden="true">
          <div className="ui-tab-skeleton__equity-summary">
            {[0, 1, 2].map((index) => (
              <div key={index}>
                <Skeleton width="54%" height={11} />
                <Skeleton width="78%" height={23} />
              </div>
            ))}
          </div>
          <div className="ui-tab-skeleton__equity-timeline">
            {[0, 1, 2, 3, 4].map((index) => (
              <div key={index}>
                <Skeleton circle width={18} height={18} />
                <div>
                  <Skeleton width="48%" height={14} />
                  <Skeleton width="72%" height={11} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {variant === 'cashflow' && (
        <div className="ui-tab-skeleton__cashflow" aria-hidden="true">
          <div className="ui-tab-skeleton__cashflow-summary">
            {[0, 1, 2].map((index) => (
              <div key={index}>
                <Skeleton width="48%" height={11} />
                <Skeleton width="76%" height={23} />
              </div>
            ))}
          </div>
          <div className="ui-tab-skeleton__cashflow-chart">
            <Skeleton width="100%" height={210} />
          </div>
          <div className="ui-tab-skeleton__cashflow-rows">{rows(4, 'cashflow')}</div>
        </div>
      )}

      {variant === 'categories' && (
        <div className="ui-tab-skeleton__categories" aria-hidden="true">
          <div className="ui-tab-skeleton__categories-controls">
            <Skeleton width={176} height={34} radius={8} />
            <Skeleton width={120} height={34} radius={8} />
          </div>
          <div className="ui-tab-skeleton__categories-table">
            {[0, 1, 2, 3, 4, 5].map((index) => (
              <div key={index}>
                <Skeleton width="44%" height={14} />
                <Skeleton width="20%" height={12} />
                <Skeleton width="18%" height={12} />
              </div>
            ))}
          </div>
        </div>
      )}

      {variant === 'intel' && (
        <div className="ui-tab-skeleton__intel" aria-hidden="true">
          <div className="ui-tab-skeleton__intel-brief">
            <Skeleton width="34%" height={14} />
            <Skeleton width="76%" height={26} />
            <Skeleton width="92%" height={12} />
          </div>
          <div className="ui-tab-skeleton__intel-filters">
            <Skeleton width={186} height={32} radius={999} />
            <Skeleton width={112} height={32} radius={8} />
          </div>
          <div className="ui-tab-skeleton__intel-stories">
            {[0, 1, 2, 3].map((index) => (
              <div key={index}>
                <Skeleton width="62%" height={17} />
                <Skeleton width="92%" height={12} />
                <Skeleton width="46%" height={11} />
              </div>
            ))}
          </div>
        </div>
      )}

      {variant === 'builder' && (
        <div className="ui-tab-skeleton__builder" aria-hidden="true">
          <div className="ui-tab-skeleton__builder-thesis">
            <Skeleton circle width={44} height={44} />
            <div>
              <Skeleton width="36%" height={24} />
              <Skeleton width="72%" height={13} />
            </div>
          </div>
          <div className="ui-tab-skeleton__builder-compose">
            <Skeleton width="38%" height={18} />
            <Skeleton width="86%" height={12} />
            <Skeleton height={108} radius={8} />
            <Skeleton width={122} height={36} radius={8} />
          </div>
        </div>
      )}

      {variant === 'help' && (
        <div className="ui-tab-skeleton__help-layout" aria-hidden="true">
          <aside>
            {[0, 1, 2, 3, 4, 5].map((index) => (
              <Skeleton key={index} width={index % 2 ? '66%' : '82%'} height={14} />
            ))}
          </aside>
          <div>
            <Skeleton width="42%" height={28} />
            {[0, 1, 2, 3, 4, 5].map((index) => (
              <Skeleton key={index} width={`${94 - index * 6}%`} height={13} />
            ))}
          </div>
        </div>
      )}

      {variant === 'cards' && (
        <div className="ui-tab-skeleton__cards" aria-hidden="true">
          <div className="ui-tab-skeleton__metrics">
            {[0, 1, 2].map((index) => (
              <div className="ui-tab-skeleton__metric" key={index}>
                <Skeleton width="42%" height={11} />
                <Skeleton width="68%" height={25} />
              </div>
            ))}
          </div>
          <Skeleton height={36} radius={8} />
          <div className="ui-tab-skeleton__card-grid">
            {[0, 1, 2, 3].map((index) => (
              <div className="ui-tab-skeleton__card" key={index}>
                <Skeleton width="52%" height={15} />
                <Skeleton width="76%" height={30} />
                <Skeleton width="42%" height={11} />
              </div>
            ))}
          </div>
        </div>
      )}

      {variant === 'workspace' && (
        <div className="ui-tab-skeleton__workspace" aria-hidden="true">
          <div className="ui-tab-skeleton__tree">{rows(6, 'tree')}</div>
          <div className="ui-tab-skeleton__detail">
            <Skeleton width="42%" height={23} />
            {[0, 1, 2, 3, 4, 5].map((index) => (
              <Skeleton key={index} width={`${92 - index * 7}%`} height={12} />
            ))}
          </div>
        </div>
      )}

      {variant === 'list' && (
        <div className="ui-tab-skeleton__list" aria-hidden="true">
          {rows(6, 'list')}
        </div>
      )}

      {variant === 'rail' && (
        <div className="ui-tab-skeleton__rail-layout" aria-hidden="true">
          <aside className="ui-tab-skeleton__rail">{rows(6, 'rail')}</aside>
          <div className="ui-tab-skeleton__rail-content">
            <Skeleton width="38%" height={25} />
            <Skeleton height={38} radius={8} />
            <div className="ui-tab-skeleton__list">{rows(6, 'content')}</div>
          </div>
        </div>
      )}
    </section>
  )
}
