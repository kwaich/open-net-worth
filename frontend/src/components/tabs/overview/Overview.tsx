// Overview v2 — the redesigned Overview tab: a customizable widget dashboard
// (Datadog-style grid, add/drag/resize/rename/duplicate/remove, sections,
// multiple named views) replacing the old static single-column layout.
// Faithful port of frontend/design_handoffs/overview_handoff/; see
// docs/adr/2026-07-22-overview-tab-redesign-faithful-port.md for the scope
// decisions (unified widget model, instanceId/defId naming, Lucius-widget
// integration via the REAL side panel — never a reimplementation of it).

import type { RegisterDeepLink } from './types'
import { useEffect, useState } from 'react'
import { Pencil } from 'lucide-react'
import type { ComputedRow } from '../../../types/portfolio'
import { Button, Container, ErrorState, Modal, PageHeader, TabSkeleton, Text } from '../../ui'
import { useRegisterHandles, useRegisterScreenPrompts } from '../../../lucius/useScreenContract'
import { Spark } from '../../../lucius/identity'
import { AddWidgetPicker } from './AddWidgetPicker'
import { DashboardGrid } from './DashboardGrid'
import { OvToast } from './OvToast'
import { ViewTabs } from './ViewTabs'
import { DataInHero } from '../../dataIn/DataInHero'
import { installOvTooltip } from './tooltip'
import { useDashboardLayout } from './useDashboardLayout'
import { stickyTopbarOffset } from '../../../utils/stickyTopbarOffset'
import './registry' // ensure every widget-group module registers before first render
import './overview.css'

interface Props {
  /** Used ONLY to gate the empty state and to resolve a clicked label back to a row for the
   *  asset drawer. No money is read off these here — every figure the widgets render comes
   *  from `/api/overview/*` via OverviewDataContext
   *  (ADR 2026-07-31-overview-aggregates-are-server-computed). */
  rows: ComputedRow[]
  /** The portfolio read has not answered yet. The shell no longer withholds this tab while it
   *  is in flight, so without this an owner with holdings would be told their dashboard is
   *  empty (and offered demo data) for as long as the fetch takes. */
  loading?: boolean
  onChatSuggest?: (text: string) => void
  onNavigate?: (tab: string, search?: Record<string, string>) => void
  onOpenAsset?: (row: ComputedRow) => void
  /** The portfolio read failed — an error, never the empty-book welcome. */
  loadFailed?: boolean
  onRetry?: () => void
  onOpenRegister?: (filter: RegisterDeepLink) => void
}

export function Overview({
  rows,
  loading,
  onChatSuggest,
  onNavigate,
  onOpenAsset,
  loadFailed = false,
  onRetry,
  onOpenRegister,
}: Props) {
  const layout = useDashboardLayout()
  const [editing, setEditing] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [addingSection, setAddingSection] = useState(false)
  const [sectionName, setSectionName] = useState('')
  const [confirmDeleteViewId, setConfirmDeleteViewId] = useState<string | null>(null)
  const [toastMsg, setToastMsg] = useState<string | null>(null)

  useEffect(() => installOvTooltip(), [])

  // Tablet/desktop scroll the document, so the shell bar and this header share a sticky
  // containing block even though they are siblings. Phone scrolls .app-layout instead.
  useEffect(() => {
    const topbar = document.querySelector<HTMLElement>('.topbar')
    if (!topbar) return
    const root = document.documentElement
    const measure = () => {
      root.style.setProperty('--ov-topbar-h', stickyTopbarOffset(topbar, window.innerWidth) + 'px')
    }
    const ro = new ResizeObserver(measure)
    measure()
    ro.observe(topbar)
    window.addEventListener('resize', measure)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', measure)
      root.style.removeProperty('--ov-topbar-h')
    }
  }, [rows.length, loading, editing])

  useRegisterHandles([
    {
      id: 'series-nw',
      type: 'series',
      label: 'Net-worth history',
      descriptor: 'Net-worth trend (last 12 months)',
      source: 'net_worth_series:1Y',
    },
    {
      id: 'tbl-alloc',
      type: 'table',
      label: 'Allocation breakdown',
      descriptor: 'Asset allocation',
      source: 'holdings:all',
    },
  ])
  useRegisterScreenPrompts(() => [
    'Is my allocation balanced?',
    "What's driving my net-worth growth?",
    "Today's biggest movers?",
    'Any concentration risk?',
  ])

  const onAskLucius = onChatSuggest
  const onShareWithLucius = onChatSuggest
    ? (label: string) => onChatSuggest(`Let's talk about my "${label}" widget.`)
    : undefined
  const onOpenAssetByLabel = onOpenAsset
    ? (label: string) => {
        const match = rows.find(
          (r) => r.label === label || r.symbol === label || r.ticker === label,
        )
        if (match) onOpenAsset(match)
      }
    : undefined

  const enterCustomize = () => {
    layout.snapshot()
    setEditing(true)
  }
  const cancelCustomize = () => {
    layout.restoreSnapshot()
    setEditing(false)
  }
  const saveCustomize = () => setEditing(false)
  const resetChanges = () => layout.restoreSnapshot()

  if (!rows.length) {
    // "Nothing yet" is not "nothing at all": while the portfolio read is still in flight the
    // empty state below would tell an owner with holdings to load demo data.
    if (loading) {
      return (
        <Container size="wide">
          <div className="ov-page">
            <TabSkeleton variant="dashboard" label="Loading your dashboard…" />
          </div>
        </Container>
      )
    }
    if (loadFailed) {
      return (
        <Container size="wide">
          <ErrorState
            title="Your dashboard couldn’t load"
            message="Nothing is missing from your book. It couldn’t be read just now, so try again or ask Lucius what went wrong."
            onRetry={onRetry}
          />
        </Container>
      )
    }

    return (
      <Container size="wide">
        <DataInHero
          title="Start with the account you use most"
          body="Add an account to start your financial picture."
          onAddAccount={() => onNavigate?.('accounts')}
          luciusMessage="Help me set up my accounts"
          onAskLucius={onChatSuggest}
        />
      </Container>
    )
  }

  const hasWidgets = !!layout.activeView?.widgets.length

  return (
    <>
      <Container size="wide">
        <div
          data-ctx-region="content"
          data-ctx-title="Dashboard"
          className={'ov-page' + (editing ? ' ov-page--editing' : '')}
        >
          <div className="ov-pagehead">
            <PageHeader
              title="Dashboard"
              subtitle="Your complete financial picture"
              actions={
                editing ? (
                  <span className="ov-page-actions">
                    <span className="ov-page-actions__tools">
                      <Button
                        size="sm"
                        variant="ghost"
                        className="ov-reset-changes"
                        onClick={resetChanges}
                        disabled={!layout.isDirty}
                      >
                        Reset changes
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => setAddingSection(true)}>
                        Add section
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => setPickerOpen(true)}>
                        Add widget
                      </Button>
                    </span>
                    <span className="ov-page-actions__commit">
                      <Button size="sm" variant="ghost" onClick={cancelCustomize}>
                        Cancel
                      </Button>
                      <Button size="sm" variant="primary" onClick={saveCustomize}>
                        Save
                      </Button>
                    </span>
                  </span>
                ) : (
                  <Button
                    size="sm"
                    variant="secondary"
                    className="ov-customize-button"
                    aria-label="Customize overview"
                    onClick={enterCustomize}
                    data-tour="setup-dashboard"
                  >
                    <Pencil size={15} strokeWidth={1.8} aria-hidden="true" />
                    <span className="ov-customize-label">Customize</span>
                  </Button>
                )
              }
            />

            <ViewTabs
              views={layout.views}
              activeViewId={layout.activeViewId}
              onSelect={layout.setActiveViewId}
              onAdd={layout.addView}
              onRename={layout.renameView}
              onRequestDelete={setConfirmDeleteViewId}
              onReorder={layout.reorderViews}
              editing={editing}
            />
          </div>

          {editing && (
            <div className="ov-mobile-edit-dock mb-anchored" aria-label="Customize actions">
              <Button size="sm" variant="ghost" onClick={resetChanges} disabled={!layout.isDirty}>
                Reset all
              </Button>
              <Button size="sm" variant="secondary" onClick={cancelCustomize}>
                Cancel
              </Button>
              <Button size="sm" variant="primary" onClick={saveCustomize}>
                Save
              </Button>
            </div>
          )}

          {layout.error && (
            <Text as="p" size="xs" tone="bad">
              {layout.error}
            </Text>
          )}

          {!layout.loading && !hasWidgets && (
            <div className="overview-empty">
              <Text as="h2" size="lg" weight={600}>
                Build this view
              </Text>
              <Text as="p" size="base" tone="muted">
                Add the widgets you want to see, or let Lucius assemble a view for you.
              </Text>
              <div className="overview-empty__actions">
                <Button variant="primary" size="md" onClick={() => setPickerOpen(true)}>
                  Add widget
                </Button>
                {onAskLucius && (
                  <Button
                    variant="primary"
                    size="md"
                    className="fi-lucius"
                    iconLeft={<Spark size={15} ariaHidden />}
                    onClick={() =>
                      onAskLucius(
                        'Help me build my Overview dashboard — what widgets should I add?',
                      )
                    }
                  >
                    Build with Lucius
                  </Button>
                )}
              </div>
            </div>
          )}

          <DashboardGrid
            layout={layout}
            editing={editing}
            onNavigate={onNavigate}
            onOpenAsset={onOpenAssetByLabel}
            onOpenRegister={onOpenRegister}
            onAskLucius={onAskLucius}
            onShareWithLucius={onShareWithLucius}
          />
        </div>
      </Container>

      <AddWidgetPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        definitions={layout.pickerDefinitions}
        onPick={layout.addWidget}
        onPickWithConfig={layout.addWidgetWithConfig}
        onAskLucius={onAskLucius}
        onAdded={setToastMsg}
      />

      <OvToast message={toastMsg} onDone={() => setToastMsg(null)} />

      {addingSection && (
        <Modal
          open
          onClose={() => setAddingSection(false)}
          title="Add section"
          size="sm"
          className="ov-section-prompt"
          footer={
            <div className="ov-section-prompt-actions">
              <Button size="sm" variant="ghost" onClick={() => setAddingSection(false)}>
                Cancel
              </Button>
              <Button
                size="sm"
                variant="primary"
                onClick={() => {
                  layout.addSection(sectionName || 'Section')
                  setSectionName('')
                  setAddingSection(false)
                }}
              >
                Add
              </Button>
            </div>
          }
        >
          <input
            className="ui-input"
            data-autofocus
            placeholder="Section name"
            value={sectionName}
            onChange={(e) => setSectionName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                layout.addSection(sectionName || 'Section')
                setSectionName('')
                setAddingSection(false)
              }
            }}
          />
        </Modal>
      )}

      {confirmDeleteViewId &&
        (() => {
          const v = layout.views.find((x) => x.viewId === confirmDeleteViewId)
          const name = v?.name || 'this view'
          return (
            <Modal
              open
              onClose={() => setConfirmDeleteViewId(null)}
              title={`Delete "${name}"?`}
              size="sm"
              className="ov-confirm"
              footer={
                <div className="ov-confirm-actions">
                  <Button size="sm" variant="ghost" onClick={() => setConfirmDeleteViewId(null)}>
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => {
                      layout.deleteView(confirmDeleteViewId)
                      setConfirmDeleteViewId(null)
                    }}
                  >
                    Delete view
                  </Button>
                </div>
              }
            >
              <Text as="p" size="sm" tone="muted">
                This view and its layout will be removed. Your other views aren&rsquo;t affected.
              </Text>
            </Modal>
          )
        })()}
    </>
  )
}
