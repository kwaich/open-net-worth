// @vitest-environment jsdom
/**
 * Regression tests for the "Ask Lucius about this" context menu's z-index (dragRightClick.tsx).
 *
 * The menu (.lx-ctxmenu) is portaled straight to document.body — a DOM SIBLING of any open
 * modal's own Radix Dialog.Portal, never a descendant. It used to inline z-index: var(--z-modal)
 * directly, the exact same token a real modal's own overlay/content uses. Because that tie was
 * not a deliberate layering decision, which one visually won was purely a function of DOM paint
 * order (later-painted siblings win ties in CSS stacking) — fragile to any change in mount order.
 *
 * The trigger IS concretely reachable from inside an open modal: PayeesModal.tsx (a real
 * ui/Modal, size="xl") renders its payee rows through EntityRow with type="payee", which stamps
 * data-entity-id="payee:<id>" — dragRightClick's global, modal-unaware `contextmenu` listener
 * fires on any such row regardless of whether a modal happens to be open over it. So the menu
 * must deliberately OUTRANK --z-modal, matching the reasoning already written for .lx-dock
 * ("must stay reachable on top of everything, incl. the Manage-payees modal").
 *
 * This file proves two things jsdom can and can't fake:
 *   1. A REAL contextmenu interaction renders the menu with the new dedicated token, not the
 *      old shared --z-modal token (jsdom reflects inline style directly — real, not simulated).
 *   2. The dedicated token's actual CALC VALUE numerically outranks --z-modal and --z-toast
 *      (jsdom does not load external stylesheets / compute cascade, so this is verified by
 *      parsing the real CSS source and the real generated token scale — same technique
 *      Modal.luciusDock.test.tsx already uses for the pointer-events guarantee).
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { DragRightClick } from './dragRightClick'

afterEach(() => cleanup())

// Stub chatComposer — DragRightClick calls useChatComposer() directly and throws without a
// real <ChatComposerProvider> ancestor; these tests only exercise the menu's own rendering,
// not the composer wiring (already covered by LuciusDock.test.tsx's DragRightClick suite).
vi.mock('../../../state/chatComposer', () => ({
  useChatComposer: () => ({ addMention: vi.fn() }),
}))

describe('dragRightClick ctx menu z-index — real render', () => {
  it('renders the open menu with the dedicated --z-lucius-ctxmenu token, not --z-modal', () => {
    render(<DragRightClick />)
    const el = document.createElement('div')
    el.setAttribute('data-entity-id', 'payee:pay-1')
    el.setAttribute('data-entity-label', 'Whole Foods')
    document.body.appendChild(el)

    fireEvent.contextMenu(el, { clientX: 40, clientY: 60 })

    const menu = document.querySelector<HTMLElement>('.lx-ctxmenu')
    expect(menu).not.toBeNull()
    // Inline style is what actually applies (higher specificity than the class rule) — this
    // is the real value the browser paints with, not a source-code assertion.
    expect(menu!.style.zIndex).toBe('var(--z-lucius-ctxmenu)')
    expect(menu!.style.zIndex).not.toBe('var(--z-modal)')

    document.body.removeChild(el)
  })
})

describe('dragRightClick ctx menu z-index — numeric stacking relationship', () => {
  const luciusCss = readFileSync(resolve(__dirname, '../../../lucius.css'), 'utf8')
  const tokensCss = readFileSync(resolve(__dirname, '../../../index.tokens.generated.css'), 'utf8')

  function ruleBody(css: string, selector: string): string {
    const start = css.indexOf(selector + ' {')
    expect(start, `no \`${selector} {\` rule in lucius.css`).toBeGreaterThan(-1)
    return css.slice(start, css.indexOf('}', start))
  }

  // Parse the flat base scale (--z-base … --z-toast) from the generated tokens file — these
  // are plain integers, no var()/calc() nesting.
  function baseTokenValue(name: string): number {
    const m = new RegExp(`--z-${name}:\\s*(-?\\d+)\\s*;`).exec(tokensCss)
    expect(m, `--z-${name} not found in index.tokens.generated.css`).not.toBeNull()
    return Number(m![1])
  }

  // Resolve a local `calc(var(--z-toast) + N)` escape-hatch expression to its numeric value —
  // the same pattern .lx-dock and .mp-pop/.mp-sheet already use above the generated scale.
  function resolveToastPlusOffset(expr: string): number {
    const m = /calc\(\s*var\(--z-toast\)\s*\+\s*(\d+)\s*\)/.exec(expr)
    expect(m, `expected a calc(var(--z-toast) + N) expression, got: ${expr}`).not.toBeNull()
    return baseTokenValue('toast') + Number(m![1])
  }

  it('.lx-ctxmenu declares z-index via the dedicated --z-lucius-ctxmenu token', () => {
    const body = ruleBody(luciusCss, '.lx-ctxmenu')
    expect(body).toMatch(/z-index:\s*var\(--z-lucius-ctxmenu\)/)
    // Regression guard against reverting to the shared modal token.
    expect(body).not.toMatch(/z-index:\s*var\(--z-modal\)/)
  })

  it('--z-lucius-ctxmenu numerically outranks --z-modal and --z-toast', () => {
    const body = ruleBody(luciusCss, '.lx-ctxmenu')
    const decl = /--z-lucius-ctxmenu:\s*([^;]+);/.exec(body)
    expect(decl, '--z-lucius-ctxmenu custom property not declared on .lx-ctxmenu').not.toBeNull()

    const ctxMenuZ = resolveToastPlusOffset(decl![1]!.trim())
    const modalZ = baseTokenValue('modal')
    const toastZ = baseTokenValue('toast')

    expect(ctxMenuZ).toBeGreaterThan(modalZ)
    expect(ctxMenuZ).toBeGreaterThan(toastZ)
  })

  it('sits one step above .lx-dock, so the active menu never paints under the idle launcher', () => {
    const dockBody = ruleBody(luciusCss, '.lx-dock')
    const dockM = /z-index:\s*calc\(var\(--z-toast\)\s*\+\s*(\d+)\)/.exec(dockBody)
    expect(dockM, '.lx-dock z-index expression not found').not.toBeNull()
    const dockZ = baseTokenValue('toast') + Number(dockM![1])

    const ctxMenuBody = ruleBody(luciusCss, '.lx-ctxmenu')
    const ctxDecl = /--z-lucius-ctxmenu:\s*([^;]+);/.exec(ctxMenuBody)!
    const ctxMenuZ = resolveToastPlusOffset(ctxDecl[1]!.trim())

    expect(ctxMenuZ).toBeGreaterThan(dockZ)
  })
})
