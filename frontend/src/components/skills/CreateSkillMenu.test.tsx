// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, fireEvent } from '@testing-library/react'
import { CreateSkillMenu } from './CreateSkillMenu'

afterEach(cleanup)

describe('CreateSkillMenu', () => {
  function setup() {
    const onCreateWithLucius = vi.fn()
    const onWriteInstructions = vi.fn()
    const onUpload = vi.fn()
    const onBrowseStarters = vi.fn()

    render(
      <CreateSkillMenu
        onCreateWithLucius={onCreateWithLucius}
        onWriteInstructions={onWriteInstructions}
        onUpload={onUpload}
        onBrowseStarters={onBrowseStarters}
      />,
    )

    const trigger = document.querySelector('[aria-haspopup="menu"]') as HTMLElement
    return { trigger, onCreateWithLucius, onWriteInstructions, onUpload, onBrowseStarters }
  }

  it('renders the trigger button with the correct text', () => {
    const { trigger } = setup()
    expect(trigger).not.toBeNull()
    expect(trigger!.textContent).toContain('New skill')
  })

  it('menu is hidden initially, shown after clicking trigger', () => {
    const { trigger } = setup()
    expect(screen.queryByRole('menu')).toBeNull()
    fireEvent.click(trigger)
    expect(screen.queryByRole('menu')).toBeTruthy()
  })

  it('all four item labels appear in the menu', () => {
    const { trigger } = setup()
    fireEvent.click(trigger)
    const menu = screen.queryByRole('menu')!
    const text = menu.textContent ?? ''
    expect(text).toContain('Create with Lucius')
    expect(text).toContain('Write skill instructions')
    expect(text).toContain('Upload a skill')
    expect(text).toContain('Browse starter skills')
  })

  it('never says "Claude" — always "Lucius"', () => {
    const { trigger } = setup()
    fireEvent.click(trigger)
    const menu = screen.queryByRole('menu')!
    expect(menu.textContent).not.toMatch(/\bClaude\b/)
    expect(menu.textContent).toContain('Lucius')
  })

  it('calls onCreateWithLucius for the first item', () => {
    const { trigger, onCreateWithLucius } = setup()
    fireEvent.click(trigger)

    const buttons = Array.from(document.querySelectorAll('[role="menuitem"]')) as HTMLElement[]
    expect(buttons.length).toBe(4)
    fireEvent.click(buttons[0]!)
    expect(onCreateWithLucius).toHaveBeenCalledOnce()
  })

  it('calls onWriteInstructions for "Write skill instructions"', () => {
    const { trigger, onWriteInstructions } = setup()
    fireEvent.click(trigger)
    const buttons = Array.from(document.querySelectorAll('[role="menuitem"]')) as HTMLElement[]
    const btn = buttons.find((b) => b.textContent?.includes('Write skill instructions'))
    fireEvent.click(btn!)
    expect(onWriteInstructions).toHaveBeenCalledOnce()
  })

  it('calls onUpload for "Upload a skill"', () => {
    const { trigger, onUpload } = setup()
    fireEvent.click(trigger)
    const buttons = Array.from(document.querySelectorAll('[role="menuitem"]')) as HTMLElement[]
    const btn = buttons.find((b) => b.textContent?.includes('Upload a skill'))
    fireEvent.click(btn!)
    expect(onUpload).toHaveBeenCalledOnce()
  })

  it('calls onBrowseStarters for "Browse starter skills"', () => {
    const { trigger, onBrowseStarters } = setup()
    fireEvent.click(trigger)
    const buttons = Array.from(document.querySelectorAll('[role="menuitem"]')) as HTMLElement[]
    const btn = buttons.find((b) => b.textContent?.includes('Browse starter skills'))
    fireEvent.click(btn!)
    expect(onBrowseStarters).toHaveBeenCalledOnce()
  })

  it('closes after clicking an item', () => {
    const { trigger } = setup()
    fireEvent.click(trigger)
    expect(screen.queryByRole('menu')).toBeTruthy()
    const buttons = Array.from(document.querySelectorAll('[role="menuitem"]')) as HTMLElement[]
    fireEvent.click(buttons[1]!)
    expect(screen.queryByRole('menu')).toBeNull()
  })
})
