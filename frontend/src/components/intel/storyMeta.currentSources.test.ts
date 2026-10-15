import { expect, it } from 'vitest'
import { aggregateSources, type StoryArc } from './storyMeta'

it('shows citations for the current update rather than older claims', () => {
  const story = {
    timeline: [
      {
        id: 'now',
        date: '2026-09-24',
        title: 'New update',
        summary: '',
        sources: [
          { title: 'Current report', url: 'https://example.com/current', domain: 'example.com' },
        ],
      },
      {
        id: 'old',
        date: '2026-08-01',
        title: 'Old update',
        summary: '',
        sources: [{ title: 'Old report', url: 'https://old.test/old', domain: 'old.test' }],
      },
    ],
  } as StoryArc
  expect(aggregateSources(story).map((source) => source.name)).toEqual(['example.com'])
})
