# Acceptance criteria — Chat (Lucius)

Interaction-level GIVEN/WHEN/THEN for the **Chat panel** (Lucius AI assistant). The QA
agent must exercise every criterion and report PASS/FAIL per ID.

---

### CHAT-OPEN-TOGGLE
- GIVEN I am on any tab with the chat panel closed
- WHEN I click the chat icon / "Ask Lucius" button in the topbar
- THEN the chat panel slides in from the right, the composer textarea is focused, and the
  main content area shifts left by the panel width (no content is hidden behind the panel).

### CHAT-CLOSE-TOGGLE
- GIVEN the chat panel is open
- WHEN I click the X / close button on the panel header
- THEN the chat panel slides closed and the main content area expands back to full width.

### CHAT-SEND-PLAIN
- GIVEN the chat panel is open and the composer has a plain-text message
- WHEN I click Send (or press Ctrl+Enter / Cmd+Enter)
- THEN the user message appears immediately in the message list AND the response starts
  streaming in as a new assistant bubble — the panel does NOT freeze or show a blank state.

### CHAT-SCROLL-ON-SEND
- GIVEN the chat panel has a long message history and I am scrolled up in it
- WHEN I send a new message
- THEN the message list scrolls to the bottom so my new message and the incoming reply are
  visible without manual scrolling.

### CHAT-SCROLL-DURING-STREAM
- GIVEN I am at the bottom of the chat panel while a response is streaming in
- WHEN new tokens arrive (the assistant bubble grows)
- THEN the panel stays pinned to the bottom (auto-scrolls) so the latest token is always
  visible.

### CHAT-SCROLL-PIN-WHILE-READING
- GIVEN a response is streaming and I have manually scrolled up to re-read an older message
- WHEN new tokens arrive
- THEN the panel does NOT jump back to the bottom (the auto-scroll pin is released while
  the user is reading above).

### CHAT-FOLLOWUP-CHIPS
- GIVEN Lucius has just responded with follow-up suggestion chips visible
- WHEN I click one of the chips
- THEN the chip's text is submitted as the next user message and streaming begins for the
  follow-up answer.

### CHAT-MODEL-SELECTOR-REFLECTS-DEFAULT
- GIVEN I navigate to Settings → AI Model and change the instance default model (e.g. pick
  a different model from the "Instance default" panel and click "Save default")
- WHEN I open (or look at) the chat panel's model selector
- THEN the selector shows "Default (new-model-name)" reflecting the model I just saved, NOT
  the old model name. (Regression guard: the model selector must read the live
  MODEL_CONFIG_CHANGED_EVENT and refresh its label.)

### CHAT-MODEL-OVERRIDE-PER-CONVO
- GIVEN the chat panel is open on a conversation
- WHEN I change the model selector to a specific model (not "Default")
- THEN a subsequent message in that conversation is sent with that model override, AND
  switching to a different conversation and back shows the override is still set for that
  first conversation.

### CHAT-PROPOSE-CONFIRM-POSITION
- GIVEN I have at least one account and I ask Lucius "Add 10 shares of AAPL to my
  brokerage" (or equivalent)
- WHEN Lucius proposes the position add
- THEN a confirm card (ChangePreview) appears in the chat with the proposed change diff,
  a Confirm button, and a Discard button — not raw JSON.

### CHAT-CONFIRM-APPLIES-CHANGE
- GIVEN a confirm card for a position add is visible in the chat
- WHEN I click Confirm
- THEN the confirm card is replaced by a success note AND the holding appears in the
  portfolio (GET /api/portfolio reflects the new position on next load).

### CHAT-MENTION-WIDGET
- GIVEN I am on the Overview tab and a net-worth hero card has a "✦" button
- WHEN I click the "✦" button
- THEN the chat panel opens (if closed) and a chip representing the net-worth entity is
  staged in the composer (visible as a non-editable chip in the input area, not raw text).

### CHAT-SETUP-HINT-NO-KEY
- GIVEN no AI API key is configured
- WHEN I send a chat message and the backend returns an auth/setup error
- THEN the error is displayed as a calm info box (not a red error dump) with a link to
  Settings → AI Model — no raw SDK error dict is shown.

### CHAT-EMPTY-NEW-CONVO
- GIVEN the chat panel is open with no prior messages in a fresh conversation
- WHEN I view the message area
- THEN an empty state or welcome prompt is shown (not a blank grey box with no indication
  of what to do).
