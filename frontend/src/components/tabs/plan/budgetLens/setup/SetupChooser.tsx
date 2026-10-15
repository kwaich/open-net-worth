// budgetLens/setup/SetupChooser.tsx — the first step inside a setup modal: teach the method,
// then offer the three ways to start it.
//
// Replaces GenericEngineSetup's inline version. The action hierarchy is unchanged — "Set up
// with Lucius" leads, the two manual paths follow after an "or" — but it now lives inside the
// modal frame, which is what the inline version never had: an unconfigured engine has nothing
// to render behind a form, so inline it was a heading and some buttons adrift in whitespace.
//
// The explainer is passed in rather than imported so this file stays free of any one engine's
// content (MethodExplainer owns the diagrams and the for/strength/cost every setup screen reads).
import type { ReactNode } from 'react'
import { Button, Loading } from '../../../../ui'
import { Spark } from '../../../../../lucius/identity'

export function SetupChooser({
  name,
  busy,
  error,
  explainer,
  onDraft,
  onStartBlank,
  onAskLucius,
}: {
  name: string
  busy: boolean
  error: string | null
  explainer?: ReactNode
  onDraft: () => void
  onStartBlank: () => void
  /** Absent in contexts with no chat dock. */
  onAskLucius?: () => void
}) {
  if (busy) return <Loading label={`Reading your spending to size ${name}…`} />
  return (
    <div className="bgl-chooser">
      {explainer}
      <div className="bgl-setup__stack">
        {onAskLucius && (
          <Button
            variant="primary"
            size="dense"
            className="fi-lucius"
            iconLeft={<Spark size={15} ariaHidden />}
            onClick={onAskLucius}
          >
            Set up with Lucius
          </Button>
        )}
        <div className="bgl-setup__actions">
          <Button variant="secondary" size="dense" onClick={onDraft}>
            Suggest from my spending
          </Button>
          <Button variant="secondary" size="dense" onClick={onStartBlank}>
            Set up manually
          </Button>
        </div>
      </div>
      {error && <p className="bgl-review__error">{error}</p>}
    </div>
  )
}
