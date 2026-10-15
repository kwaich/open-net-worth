// Create / edit — one alert, one form (verbatim port of the prototype's AlertForm).
import { useState } from 'react'
import type { ReactNode } from 'react'
import {
  Button,
  FormField,
  Modal,
  Money,
  NumberField,
  SegmentedControl,
  Select,
  Switch,
  TextArea,
  TextField,
} from '../../ui'
import { ArrowDown, ArrowUp, ChevronDown } from '../../../ui/icons'
import { AFTER, fmt, toFire } from './alertModel'
import type { AfterFire, AlertDraft, AlertKind } from './alertModel'
import {
  channelKey,
  keyToChannels,
  notifyOptions,
  routeHelp,
  useAlertRouting,
} from './alertRouting'

export interface AlertFormProps {
  draft: AlertDraft
  sym: string
  name: string
  ccy: string
  price: number | null
  saving: boolean
  error: string | null
  onCancel: () => void
  onSave: (d: AlertDraft) => void
  onDelete?: () => void
}

type Dir = 'up' | 'down'

/** The design-system chevron over a native select (the prototype shows the browser's own
 * arrow; the owner asked for the house one). Same pattern as `.pl-selwrap` / `.firstrun-select-wrap`. */
function SelectWrap({ children }: { children: ReactNode }) {
  return (
    <span className="pa-sel">
      {children}
      <ChevronDown size={14} strokeWidth={2} className="pa-sel-cv" aria-hidden="true" />
    </span>
  )
}

export function AlertForm({
  draft,
  sym,
  name: assetName,
  ccy,
  price,
  saving,
  error,
  onCancel,
  onSave,
  onDelete,
}: AlertFormProps) {
  const [mode, setMode] = useState<AlertKind>(draft.mode)
  const [value, setValue] = useState(draft.value != null ? String(draft.value) : '')
  const [move, setMove] = useState(String(draft.move.toFixed(2)))
  const [after, setAfter] = useState<AfterFire>(draft.after)
  const [pickedChannels, setPickedChannels] = useState<string[] | null>(draft.channels)
  const [note, setNote] = useState(draft.note)
  const [name, setName] = useState(draft.name)
  const [moveDir, setMoveDir] = useState<Dir>(
    draft.mode === 'move' && draft.op === '<=' ? 'down' : draft.mode === 'move' ? 'up' : 'up',
  )
  const [priceDir, setPriceDir] = useState<Dir>(
    draft.mode === 'price' && draft.op === '>=' ? 'up' : 'down',
  )
  const [enabled, setEnabled] = useState(draft.enabled)
  const routing = useAlertRouting()
  // A new alert shows Settings' price-alert default until the user picks; after that (or on
  // an existing alert) the alert's own list is the whole story.
  const channels = pickedChannels ?? routing?.defaults ?? []
  const v = Number(value) || 0
  /* Direction isn't a question: a target above today's price can only mean "rises above". */
  const op = (mode === 'move' ? moveDir : priceDir) === 'up' ? '>=' : '<='
  const gap = v > 0 && price ? toFire(op, v, price) : null
  const editing = !!draft.id
  const mv = Number(move) || 0
  const valid = mode === 'price' ? v > 0 : mv > 0 && mv <= 100

  const query =
    mode === 'price' ? (
      <>
        Explain the factors driving the latest movement of {sym} ({assetName}), including its recent{' '}
        {op === '>=' ? 'rise to' : 'fall to'}{' '}
        <Money>
          {ccy}
          {v ? fmt(v) : '—'}
        </Money>
        . Contextualize this price movement within the last few weeks of price action and investor
        narrative.
      </>
    ) : (
      <>
        Explain the factors driving a {mv.toFixed(2)}% {moveDir === 'up' ? 'rise' : 'fall'} in {sym}{' '}
        ({assetName}). Contextualize this price movement within the last few weeks of price action
        and investor narrative.
      </>
    )

  const dirButtons = (dir: Dir, set: (d: Dir) => void, labels: [string, string]) => (
    <>
      <button
        type="button"
        aria-pressed={dir === 'down'}
        aria-label={labels[0]}
        className={'pa-dir-b' + (dir === 'down' ? ' pa-dir-b--on pa-dir-b--dn' : '')}
        onClick={() => set('down')}
      >
        <ArrowDown size={14} strokeWidth={2.2} />
      </button>
      <button
        type="button"
        aria-pressed={dir === 'up'}
        aria-label={labels[1]}
        className={'pa-dir-b' + (dir === 'up' ? ' pa-dir-b--on pa-dir-b--up' : '')}
        onClick={() => set('up')}
      >
        <ArrowUp size={14} strokeWidth={2.2} />
      </button>
    </>
  )

  return (
    <Modal
      open
      onClose={onCancel}
      size="lg"
      className="pa-modal"
      title={editing ? `Edit alert · ${sym}` : 'New alert'}
      footer={
        <div className="pa-foot">
          {editing && onDelete && (
            <button type="button" className="pa-del" onClick={onDelete} disabled={saving}>
              Delete alert
            </button>
          )}
          {error && <span className="pa-err">{error}</span>}
          <div className="pa-foot-r">
            <Button variant="ghost" size="sm" onClick={onCancel}>
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={saving}
              disabled={!valid}
              onClick={() =>
                onSave({
                  ...draft,
                  name,
                  mode,
                  op,
                  value: mode === 'price' ? v : null,
                  move: mv,
                  after,
                  channels: pickedChannels ?? routing?.defaults ?? null,
                  note,
                  enabled,
                })
              }
            >
              {editing ? 'Save alert' : 'Create alert'}
            </Button>
          </div>
        </div>
      }
    >
      <div className="pa-fields">
        {editing && (
          <div className="pa-formsw">
            <span className="pa-formsw-b">
              <span className="pa-formsw-t">{enabled ? 'Active' : 'Paused'}</span>
            </span>
            <Switch
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              aria-label="Alert on"
            />
          </div>
        )}

        <FormField label="Ticker" htmlFor="pa-tk">
          <SelectWrap>
            <Select id="pa-tk" value={sym} disabled options={[{ value: sym, label: sym }]} />
          </SelectWrap>
        </FormField>

        <FormField label="Name (optional)" htmlFor="pa-name">
          <TextField
            id="pa-name"
            value={name}
            maxLength={40}
            placeholder="e.g. Stop-loss zone"
            onChange={(e) => setName(e.target.value)}
          />
        </FormField>

        <div className="pa-row2">
          <FormField label="Alert criteria">
            <SegmentedControl<AlertKind>
              ariaLabel="Alert criteria"
              tone="primary"
              value={mode}
              onChange={setMode}
              options={[
                { value: 'price', label: 'Target price' },
                { value: 'move', label: '% move' },
              ]}
            />
          </FormField>
          {mode === 'price' ? (
            <FormField
              htmlFor="pa-val"
              label={
                price != null ? (
                  <>
                    Target price (now{' '}
                    <Money>
                      {ccy}
                      {fmt(price)}
                    </Money>
                    )
                  </>
                ) : (
                  'Target price'
                )
              }
              help={
                gap !== null && gap <= 0
                  ? `${sym} is already ${priceDir === 'up' ? 'above' : 'below'} this level — it would alert right away`
                  : undefined
              }
            >
              <div className="pa-dir">
                {dirButtons(priceDir, setPriceDir, ['Falls below', 'Rises above'])}
                <NumberField
                  id="pa-val"
                  min={0}
                  step="any"
                  invalid={gap !== null && gap <= 0}
                  value={value}
                  placeholder="0.00"
                  onChange={(e) => setValue(e.target.value)}
                />
              </div>
            </FormField>
          ) : (
            <FormField label="Movement amount (%)" htmlFor="pa-mv">
              <div className="pa-dir">
                {dirButtons(moveDir, setMoveDir, ['Falls by', 'Rises by'])}
                <NumberField
                  id="pa-mv"
                  min={0}
                  max={100}
                  step="any"
                  value={move}
                  placeholder="0.00"
                  onChange={(e) => setMove(e.target.value)}
                />
              </div>
            </FormField>
          )}
        </div>

        <div className="pa-row2">
          <FormField label="Cooldown" htmlFor="pa-af">
            <SelectWrap>
              <Select
                id="pa-af"
                value={after}
                onChange={(e) => setAfter(e.target.value as AfterFire)}
                options={AFTER.map(([v, l]) => ({ value: v, label: l }))}
              />
            </SelectWrap>
          </FormField>
          <FormField label="Notifications" htmlFor="pa-ch" help={routeHelp(channels, routing)}>
            <SelectWrap>
              <Select
                id="pa-ch"
                value={channelKey(channels, routing)}
                onChange={(e) => setPickedChannels(keyToChannels(e.target.value))}
                options={notifyOptions(routing, channels)}
              />
            </SelectWrap>
          </FormField>
        </div>

        <FormField label="Default query">
          <span className="pa-quote">{query}</span>
        </FormField>

        <FormField label="Additional instructions (optional)" htmlFor="pa-note">
          <TextArea
            id="pa-note"
            rows={5}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Check news and social media sentiment to assess consensus or controversy"
          />
        </FormField>
      </div>
    </Modal>
  )
}
