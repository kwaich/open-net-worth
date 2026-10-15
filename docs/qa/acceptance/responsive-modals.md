# Acceptance — library-backed responsive modals

### RM-MOBILE-SHEET

- GIVEN I am at a 390×844 viewport on any screen with a canonical modal action
- WHEN I open the modal
- THEN it is bottom-docked, no wider than the viewport, no taller than 92dvh, and its close
  control and action footer remain reachable while its body scrolls

### RM-MOBILE-MORE

- GIVEN I am at a 390×844 viewport
- WHEN I tap More in the mobile tab bar
- THEN the navigation sheet opens over an inert page, traps focus, closes with Escape or its close
  control, and restores focus to More

### RM-PLAN-LIBRARY

- GIVEN I am at a 390×844 viewport on Plan
- WHEN I open FIRE settings, Manage categories, a target, or an envelope action
- THEN every presentation is hosted by the shared Radix-backed Planning adapter, with an inert
  background, contained/restored focus, body scroll lock, top-layer Escape, and the same
  bottom-docked viewport bounds without a per-modal responsive override

### RM-DESKTOP-DIALOG

- GIVEN I am at a 1440px viewport with Lucius closed or open
- WHEN I open a canonical modal
- THEN it remains centered in the visible app region at its declared size and never renders under
  the Lucius panel

### RM-ACCESSIBILITY

- GIVEN a modal was opened from a keyboard-focusable control
- WHEN I press Tab, Shift+Tab, or Escape and then close the modal
- THEN focus stays inside while open, only the top nested modal handles Escape, and focus returns
  to the opener
