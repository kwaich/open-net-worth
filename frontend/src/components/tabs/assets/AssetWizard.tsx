// Add asset — four short steps in one md dialog that fits each step (owner, 2026-09-26: no
// fixed frame): what it is, its name and value, whether it is all yours, and optionally what
// was paid. The title is the step's question; the subtitle and a thin progress bar say where
// you are. Mount it only while open, so
// every open starts fresh. Saving creates a staged loan first (once — it then stays selected
// by its real id), then the asset; a refusal keeps every step's input and shows the server's
// reason beside the field it names, or in the dialog when it names none.
import { useState, type KeyboardEvent } from 'react'
import type { AssetCategory, AssetLoan, OwnedAsset } from '../../../types/assets'
import { localIso } from '../../../utils/format'
import { Button, ConfirmDialog, Modal, Text } from '../../ui'
import * as api from './api'
import { purchaseCopy } from './assetCategoryRules'
import {
  WIZARD_STEPS,
  isDirty,
  nameValueErrors,
  withOwnership,
  withPurchase,
  type WizardStep,
} from './assetForms'
import { catOf } from './assetsData'
import { CategoryStep, DescribeToLucius } from './CategoryStep'
import { NameValueStep } from './NameValueStep'
import { OwnershipStep } from './OwnershipStep'
import { PurchaseStep } from './PurchaseStep'
import { useWizardFlow, useWizardSave, type WizardFlow } from './useAssetWizard'
import { linkedTo, useStagedLoan } from './useStagedLoan'

export interface AssetWizardProps {
  /** The book's currency — what every amount here is typed in. */
  baseCurrency: string
  cats: AssetCategory[]
  loans: AssetLoan[]
  /** A category chip active on the register: start at step 2 with it chosen. */
  initialCategory: string | null
  onClose(): void
  /** The asset exists — the caller closes this, confirms it, and opens its page. */
  onCreated(asset: OwnedAsset): void
  onAskLucius(seed: string): void
}

/** Each step leads with its question — step 4's is a policy's own. */
function questionOf(step: WizardStep, category: string | null): string {
  if (step === 'cat') return 'What is it?'
  if (step === 'nameValue') return 'Name and value'
  if (step === 'ownership') return 'Is it all yours?'
  return purchaseCopy(category ?? '').question
}

/** "Step 1 of 4" — then "{Category} · Step n of 4" once there is a category. */
function subtitleOf(step: WizardStep, categoryLabel: string | null): string {
  const position = `Step ${WIZARD_STEPS.indexOf(step) + 1} of 4`
  return step === 'cat' || !categoryLabel ? position : `${categoryLabel} · ${position}`
}

/** Four thin segments, the steps reached so far filled. Decorative: the subtitle says it. */
function WizardProgress({ step }: { step: WizardStep }) {
  const at = WIZARD_STEPS.indexOf(step)
  return (
    <div className="as-wizard-progress" aria-hidden="true">
      {WIZARD_STEPS.map((s, i) => (
        <span key={s} className={i <= at ? 'on' : undefined} />
      ))}
    </div>
  )
}

/** Only a text-like input advances on Enter — never a button, select or toggle. */
function isTextInput(target: EventTarget): boolean {
  return (
    target instanceof HTMLInputElement &&
    !['checkbox', 'radio', 'button', 'submit', 'reset'].includes(target.type)
  )
}

interface WizardStepBodyProps {
  flow: WizardFlow
  cats: AssetCategory[]
  loans: AssetLoan[]
  currency: string
  today: string
  onCreateCategory(label: string): Promise<AssetCategory>
}

function WizardStepBody({
  flow,
  cats,
  loans,
  currency,
  today,
  onCreateCategory,
}: WizardStepBodyProps) {
  const { step, draft, update, errors } = flow
  if (step === 'cat')
    return (
      <CategoryStep
        cats={cats}
        newCatLabel={draft.newCatLabel}
        onNewCatLabel={(newCatLabel) => update({ newCatLabel })}
        onPick={flow.pick}
        onCreateCategory={onCreateCategory}
      />
    )
  if (!draft.cat) return null
  if (step === 'nameValue')
    return (
      <NameValueStep
        category={draft.cat}
        hint={catOf(cats, draft.cat).hint}
        name={draft.name}
        value={draft.value}
        currency={currency}
        errors={errors}
        onName={(name) => {
          update({ name })
          flow.clearErrors(['name'])
        }}
        onValue={(value) => {
          update({ value })
          flow.clearErrors(['value'])
        }}
        onUnreadable={flow.markUnreadable}
      />
    )
  if (step === 'ownership')
    return (
      <OwnershipStep
        category={draft.cat}
        value={draft.ownership}
        onChange={(patch) => {
          update({ ownership: withOwnership(flow.latest.current.ownership, patch) })
          flow.clearErrors(Object.keys(patch))
        }}
        onUnreadable={flow.markUnreadable}
        onLoanForm={flow.setLoanForm}
        loans={loans}
        assetId={null}
        currency={currency}
        errors={errors}
      />
    )
  return (
    <PurchaseStep
      category={draft.cat}
      value={draft.purchase}
      onChange={(patch) => {
        update({ purchase: withPurchase(flow.latest.current.purchase, patch) })
        flow.clearErrors(Object.keys(patch))
      }}
      onUnreadable={flow.markUnreadable}
      currency={currency}
      maxDate={today}
      errors={errors}
    />
  )
}

interface WizardFooterProps {
  step: WizardStep
  saving: boolean
  /** Step 4 with step 2 broken by back-navigation — Add asset waits for a fix. */
  blocked: boolean
  onBack(): void
  onPrimary(): void
  onAskLucius(): void
}

function WizardFooter({
  step,
  saving,
  blocked,
  onBack,
  onPrimary,
  onAskLucius,
}: WizardFooterProps) {
  if (step === 'cat') return <DescribeToLucius onAsk={onAskLucius} />
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        className="as-wizard-back"
        onClick={onBack}
        disabled={saving}
      >
        Back
      </Button>
      <Button variant="primary" size="sm" onClick={onPrimary} disabled={saving || blocked}>
        {step !== 'purchase' ? 'Continue' : saving ? 'Adding…' : 'Add asset'}
      </Button>
    </>
  )
}

interface DiscardConfirmProps {
  open: boolean
  createdLoan: AssetLoan | null
  onConfirm(): void
  onCancel(): void
}

function DiscardConfirm({ open, createdLoan, onConfirm, onCancel }: DiscardConfirmProps) {
  const body = createdLoan
    ? `You'll lose what you've entered. The loan you added, ${createdLoan.name}, stays in Accounts.`
    : "You'll lose what you've entered."
  return (
    <ConfirmDialog
      data={
        open
          ? {
              title: 'Discard this asset?',
              body,
              confirmLabel: 'Discard',
              cancelLabel: 'Keep editing',
              danger: true,
            }
          : null
      }
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  )
}

interface WizardDialogProps {
  flow: WizardFlow
  saving: boolean
  cats: AssetCategory[]
  loans: AssetLoan[]
  currency: string
  today: string
  onRequestClose(): void
  onPrimary(): void
  onAskLucius(): void
  onCreateCategory(label: string): Promise<AssetCategory>
}

/** The one fixed frame every step shows in, with its footer. */
function WizardDialog(props: WizardDialogProps) {
  const { flow, saving, cats, onPrimary } = props
  const { step, draft } = flow
  const category = draft.cat ? catOf(cats, draft.cat) : null
  const blocked =
    step === 'purchase' && Object.keys(nameValueErrors(draft.name, draft.value)).length > 0
  // A held Enter repeats keydown — it must not skip past the optional last step.
  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (step === 'cat' || e.key !== 'Enter' || e.repeat || e.nativeEvent.isComposing) return
    if (!isTextInput(e.target)) return
    e.preventDefault()
    onPrimary()
  }
  return (
    <Modal
      className="as-modal"
      open
      onClose={props.onRequestClose}
      size="md"
      title={questionOf(step, draft.cat)}
      subtitle={subtitleOf(step, category?.label ?? null)}
      footer={
        <WizardFooter
          step={step}
          saving={saving}
          blocked={blocked}
          onBack={flow.back}
          onPrimary={onPrimary}
          onAskLucius={props.onAskLucius}
        />
      }
    >
      <div className="as-wizard" onKeyDown={onKeyDown}>
        <WizardProgress step={step} />
        <WizardStepBody
          flow={flow}
          cats={cats}
          loans={props.loans}
          currency={props.currency}
          today={props.today}
          onCreateCategory={props.onCreateCategory}
        />
        {flow.saveErr && (
          <div role="alert">
            <Text size="xs" tone="bad">
              {flow.saveErr}
            </Text>
          </div>
        )}
      </div>
    </Modal>
  )
}

export function AssetWizard(props: AssetWizardProps) {
  const { baseCurrency, cats, loans, initialCategory, onClose, onCreated, onAskLucius } = props
  const today = localIso()
  const flow = useWizardFlow(initialCategory, today)
  const staged = useStagedLoan((loan) =>
    flow.update({ ownership: linkedTo(flow.latest.current.ownership, loan) }),
  )
  const saver = useWizardSave(flow, today, staged, onCreated)
  // Closing with input asks first; `then` runs once the dialog is gone (the Lucius hand-off).
  const [discarding, setDiscarding] = useState<{ then?: () => void } | null>(null)
  // A category created this session — the parent's list only catches up after a refetch.
  const [extraCat, setExtraCat] = useState<AssetCategory | null>(null)
  const allCats = extraCat && !cats.some((c) => c.key === extraCat.key) ? [...cats, extraCat] : cats

  function close(then?: () => void) {
    onClose()
    then?.()
  }
  function requestClose(then?: () => void) {
    if (saver.savingRef.current) return
    if (isDirty(flow.latest.current)) setDiscarding({ then })
    else close(then)
  }
  function primary() {
    if (flow.step === 'purchase') void saver.save()
    else if (flow.step !== 'cat') flow.advance()
  }
  async function createCategory(label: string): Promise<AssetCategory> {
    const created = await api.createCategory(label)
    setExtraCat(created)
    return created
  }

  return (
    <>
      <WizardDialog
        flow={flow}
        saving={saver.saving}
        cats={allCats}
        loans={staged.withCreated(loans)}
        currency={baseCurrency}
        today={today}
        onRequestClose={() => requestClose()}
        onPrimary={primary}
        onAskLucius={() => requestClose(() => onAskLucius('Add an asset: '))}
        onCreateCategory={createCategory}
      />
      <DiscardConfirm
        open={discarding != null}
        createdLoan={staged.createdLoan}
        onConfirm={() => close(discarding?.then)}
        onCancel={() => setDiscarding(null)}
      />
    </>
  )
}
