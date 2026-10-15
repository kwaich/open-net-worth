import { ChangePreview } from '../ChangePreview'
import type { CardFixture } from './fixtures/types'

// Dev-only: every approval card from approval/fixtures/*.ts on one screen, at the chat
// panel's width, so the whole card vocabulary can be reviewed (and screenshotted) at once.
// Reachable at /?gallery=cards (gated in main.tsx); not in the nav.

const modules = import.meta.glob<Record<string, unknown>>('./fixtures/*.ts', { eager: true })

function allFixtures(): Array<{ group: string; fixtures: CardFixture[] }> {
  return Object.entries(modules)
    .filter(([path]) => !path.endsWith('/types.ts'))
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([path, mod]) => ({
      group: path.replace(/^.*\/(.+)\.ts$/, '$1'),
      fixtures: Object.values(mod).filter(Array.isArray).flat() as CardFixture[],
    }))
}

export function ApprovalCardGallery() {
  return (
    <div className="ui-gallery">
      <h1>Approval cards</h1>
      {allFixtures().map(({ group, fixtures }) => (
        <section className="ui-gallery__section" key={group}>
          <h2>{group}</h2>
          <div className="ac-gallery">
            {fixtures.map((f) => (
              <figure className="ac-gallery__item" key={f.name}>
                <figcaption>{f.name}</figcaption>
                <ChangePreview change={f.change} onRespond={() => {}} />
              </figure>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}
