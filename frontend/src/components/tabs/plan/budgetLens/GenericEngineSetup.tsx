// budgetLens/GenericEngineSetup.tsx — the empty state for any budget engine with no bespoke
// frontend (RFC Decision D). Copy comes entirely from the engine's own `descriptor` — never
// hardcoded here, since this is the fallback for every future engine, not just envelope.
//
// Deliberately NOT <EmptyState> (owner, 2026-08-23: "why are we limiting the size of it? and I
// don't like the card, it makes things look too fragmented"). EmptyState is a centered, narrow,
// bordered panel — right for "no data yet, here's one button", wrong here: this screen is the
// method's introduction, the pane is ~1120px wide, and a small grey box adrift in it both
// fragmented the page and squeezed the diagram down to nothing. Full-bleed, no card, on the same
// left edge as every other pane so it reads as part of the page rather than a tile on top of it.
//
// Same action hierarchy as proportional/ProportionalSetup.tsx: "Set up with Lucius" is the
// PRIMARY action alone on its own row (the only genuinely AI-powered path — "Draft a proposal"
// runs a deterministic backend heuristic, since services/ must not call the model directly),
// with the two manual paths together beneath it as the fallback.
import { Button, Loading } from '../../../ui'
import { Spark } from '../../../../lucius/identity'
import { MethodExplainer } from './MethodExplainer'
import type { BudgetEngineDescriptor } from './types'

export interface GenericEngineSetupProps {
  descriptor: BudgetEngineDescriptor
  drafting: boolean
  /** False when the engine declares no single-list setup verb — a derived engine with
   *  nothing to configure, or a future engine this generic reader can't drive. Neither manual
   *  CTA has anywhere to send its result, so both are hidden rather than shown disabled.
   *  Lucius is still offered: it can operate the engine through its own declared verbs even
   *  where this generic reader cannot. */
  canSetUp: boolean
  onDraft: () => void
  onStartBlank: () => void
  onAskLucius?: (text: string) => void
}

export function GenericEngineSetup({
  descriptor,
  drafting,
  canSetUp,
  onDraft,
  onStartBlank,
  onAskLucius,
}: GenericEngineSetupProps) {
  if (drafting) return <Loading label={`Drafting your ${descriptor.name} setup…`} />
  return (
    <div className="bgl-setup">
      <div className="bgl-setup__head">
        <h2 className="bgl-setup__title">Set up {descriptor.name}</h2>
        <p className="bgl-setup__sub">{descriptor.description}</p>
      </div>

      {/* The best teaching moment in the feature: the user picked this method and nothing else on
          the page competes for attention. Shows HOW it works (diagram) and what it costs them,
          reusing the comparison's own content so the two can never drift apart. */}
      <MethodExplainer engineId={descriptor.id} />

      <div className="bgl-setup__stack">
        {onAskLucius && (
          <Button
            variant="primary"
            size="dense"
            className="fi-lucius"
            iconLeft={<Spark size={15} ariaHidden />}
            onClick={() =>
              onAskLucius(
                `Help me set up the ${descriptor.name} budget lens — look at my spending ` +
                  `history, propose a setup, and explain why you chose it.`,
              )
            }
          >
            Set up with Lucius
          </Button>
        )}
        {canSetUp && (
          <div className="bgl-setup__actions">
            <Button variant="secondary" size="dense" onClick={onDraft}>
              Suggest from my spending
            </Button>
            <Button variant="secondary" size="dense" onClick={onStartBlank}>
              Set up manually
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
