---
timestamp: 2026-08-31T02:47:35Z
agent: pi-coding-agent
model: gpt-5.6
trigger: user-prompt
status: change-note
ticket: no-ticket
pr: 28
---

# Render structured chat warnings

The transaction change preview now accepts the structured tracking-start warning returned by
the backend and displays its message alongside legacy string warnings.

The dashboard crashed when React received the warning object as a child during chat hydration.

**Verified by:** `npm test -- --run src/components/chat/ChangePreview.txn.test.tsx`, `npm run build`
