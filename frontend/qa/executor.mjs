// Playwright executor for Anthropic computer-use tool actions.
//
// The model "sees" a screenshot and emits `computer` tool actions in screen
// coordinates; this maps each action onto a headless Playwright page at the SAME
// viewport size handed to the model, so coordinates line up. No DOM selectors —
// this is real computer use (coordinate clicks on screenshots), actuated by
// Playwright instead of a desktop VM.
//
// Supports the action subset the harness offers: screenshot / left_click /
// right_click / double_click / type / key / scroll / mouse_move / wait.

import { writeFileSync } from 'node:fs'
import { join } from 'node:path'

// Map common Anthropic key names → Playwright key names.
const KEY_MAP = {
  Return: 'Enter',
  KP_Enter: 'Enter',
  Escape: 'Escape',
  BackSpace: 'Backspace',
  Tab: 'Tab',
  space: 'Space',
  Page_Down: 'PageDown',
  Page_Up: 'PageUp',
  Down: 'ArrowDown',
  Up: 'ArrowUp',
  Left: 'ArrowLeft',
  Right: 'ArrowRight',
}

function mapKey(combo) {
  // Anthropic sends e.g. "ctrl+a" or "Return"; translate token-by-token.
  return combo
    .split('+')
    .map((tok) => {
      const t = tok.trim()
      if (KEY_MAP[t]) return KEY_MAP[t]
      if (t.toLowerCase() === 'ctrl') return 'Control'
      if (t.toLowerCase() === 'cmd' || t.toLowerCase() === 'super') return 'Meta'
      if (t.toLowerCase() === 'alt') return 'Alt'
      if (t.toLowerCase() === 'shift') return 'Shift'
      return t.length === 1 ? t : t.charAt(0).toUpperCase() + t.slice(1)
    })
    .join('+')
}

/**
 * Take a screenshot, persist it to outDir, and return a base64 PNG for the API.
 */
export async function captureScreenshot(page, { outDir, index }) {
  const buf = await page.screenshot({ type: 'png' })
  const file = join(outDir, `step-${String(index).padStart(3, '0')}.png`)
  writeFileSync(file, buf)
  return { base64: buf.toString('base64'), file }
}

/**
 * Execute one computer-use action against the page. Returns a short log line and,
 * for screenshot actions, the captured image (base64 + file path).
 *
 * `input` is the tool-call input dict from the model (action + coordinate/text…).
 */
export async function executeAction(page, input, { outDir, shotIndex, log = console.log }) {
  const action = input.action
  switch (action) {
    case 'screenshot': {
      const shot = await captureScreenshot(page, { outDir, index: shotIndex })
      log(`[act] screenshot → ${shot.file}`)
      return { line: 'screenshot', shot }
    }
    case 'left_click':
    case 'right_click':
    case 'middle_click':
    case 'double_click': {
      const [x, y] = input.coordinate || [0, 0]
      const button =
        action === 'right_click' ? 'right' : action === 'middle_click' ? 'middle' : 'left'
      const clickCount = action === 'double_click' ? 2 : 1
      await page.mouse.click(x, y, { button, clickCount })
      log(`[act] ${action} @ ${x},${y}`)
      return { line: `${action} @ ${x},${y}` }
    }
    case 'mouse_move': {
      const [x, y] = input.coordinate || [0, 0]
      await page.mouse.move(x, y)
      log(`[act] mouse_move @ ${x},${y}`)
      return { line: `mouse_move @ ${x},${y}` }
    }
    case 'type': {
      await page.keyboard.type(String(input.text ?? ''))
      log(`[act] type "${String(input.text ?? '').slice(0, 40)}"`)
      return { line: `type "${String(input.text ?? '').slice(0, 40)}"` }
    }
    case 'key': {
      const combo = mapKey(String(input.text ?? input.key ?? ''))
      await page.keyboard.press(combo)
      log(`[act] key ${combo}`)
      return { line: `key ${combo}` }
    }
    case 'scroll': {
      const [x, y] = input.coordinate || [
        page.viewportSize().width / 2,
        page.viewportSize().height / 2,
      ]
      const dir = input.scroll_direction || 'down'
      const amount = Number(input.scroll_amount ?? 3) * 100
      const dx = dir === 'left' ? -amount : dir === 'right' ? amount : 0
      const dy = dir === 'up' ? -amount : dir === 'down' ? amount : 0
      await page.mouse.move(x, y)
      await page.mouse.wheel(dx, dy)
      log(`[act] scroll ${dir} @ ${x},${y}`)
      return { line: `scroll ${dir}` }
    }
    case 'wait': {
      const ms = Math.min(Number(input.duration ?? 1) * 1000, 3000)
      await page.waitForTimeout(ms)
      log(`[act] wait ${ms}ms`)
      return { line: `wait ${ms}ms` }
    }
    case 'cursor_position': {
      log('[act] cursor_position (no-op)')
      return { line: 'cursor_position' }
    }
    default:
      log(`[act] UNSUPPORTED action: ${action}`)
      return { line: `unsupported: ${action}`, unsupported: true }
  }
}
