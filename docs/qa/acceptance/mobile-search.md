# Mobile Search acceptance

Context screenshots:

- `/Users/owner/.mission-control/attachments/task-219/file_52.jpg`
- `/Users/owner/.mission-control/attachments/task-219/file_53.jpg`

Run:

```bash
cd frontend
npm run build
npm run e2e:mobile-search
```

The disposable Playwright path uses fabricated demo data and verifies:

- More → Search opens a focused field at the top of a 390 × 844 viewport.
- Opening the palette does not scroll the document.
- Typing `investments` produces and selects a real palette result.
- Reducing the visible viewport to 390 × 510 keeps the field and a complete result visible.
- Results scroll independently without moving the field or document.
- Selecting Investments closes the palette and navigates.
- At 1280 × 800, `Cmd+K` remains a floating dialog with its keyboard footer, and Escape closes it.

Evidence is written to `/tmp/onw-task-219-mobile-search/`:

- `mobile-search-open.png`
- `mobile-search-keyboard-height.png`
- `desktop-cmdk-preserved.png`
