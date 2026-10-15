# AI Model Performance Telemetry — Acceptance Criteria

Status: Draft · Feature: Model performance panel in Settings → AI Model

## Overview

A new read-only panel in `Settings → AI Model` showing per-model latency (TTFT, duration, tokens/sec) and JSON-retry rate over the last 50 recorded events. Backed by `GET /api/ai/model-telemetry`.

## Acceptance Criteria

### TELEMETRY-EMPTY
- GIVEN a fresh QA instance with zero recorded telemetry samples
- WHEN I navigate to Settings → AI Model
- THEN the "Model performance" panel shows an explicit "No telemetry yet" message, not a blank panel, a spinner stuck forever, or an error

### TELEMETRY-LOAD
- GIVEN the QA instance has at least one recorded model telemetry sample
- WHEN I navigate to Settings → AI Model
- THEN the "Model performance" panel displays a comparison table with at least one model row and four metric columns (TTFT p50, Duration p50, Tok/sec p50, and JSON-retry rate)

### TELEMETRY-PROVIDER-KNOWN
- GIVEN a model exists in the provider catalog (e.g. claude-opus-4)
- WHEN the panel displays that model's row
- THEN the model label reads "Anthropic — claude-opus-4" (provider name from catalog + model id), not just the raw model id

### TELEMETRY-PROVIDER-UNKNOWN
- GIVEN a model id is NOT in the provider catalog
- WHEN the panel displays that model's row
- THEN the model label shows the bare model id with no provider prefix (e.g. "custom-model-id"), never "Unknown — custom-model-id"

### TELEMETRY-DURATION-NULL
- GIVEN telemetry samples exist that predate the `duration_ms` field (or samples with null duration)
- WHEN the panel displays the Duration p50 stat tile
- THEN a null duration renders as an em dash (–), not a blank/broken cell; a tooltip explains "Older samples predate duration tracking — fills in as new turns run"; the other metrics still render normally

### TELEMETRY-ERROR-RATE-TONE-GOOD
- GIVEN a model has a JSON-retry rate of 0%
- WHEN the panel displays that model's badge
- THEN the badge tone is "good" (green/positive), and the label reads "0 of N · 0% JSON-retry rate"

### TELEMETRY-ERROR-RATE-TONE-WARN
- GIVEN a model has a JSON-retry rate between 0% (exclusive) and 10% (exclusive)
- WHEN the panel displays that model's badge
- THEN the badge tone is "warn" (yellow/caution), and the label reads "M of N · 1% JSON-retry rate" (or similar nonzero percent)

### TELEMETRY-ERROR-RATE-TONE-BAD
- GIVEN a model has a JSON-retry rate of 10% or greater
- WHEN the panel displays that model's badge
- THEN the badge tone is "bad" (red/negative), and the label reads "N of M · 10% JSON-retry rate" (or similar ≥10% percent)

### TELEMETRY-ERROR-RATE-ROUNDING
- GIVEN a model has a nonzero JSON-retry rate that would mathematically round to 0% (e.g. 0.4%)
- WHEN the panel displays that rate
- THEN the label reads "<1% JSON-retry rate", never "0%"

### TELEMETRY-SAMPLE-COUNT
- GIVEN a model with recorded samples
- WHEN the panel displays that model's row
- THEN the row shows "N sample(s)" (e.g. "5 samples" or "1 sample"), and samples never exceeds 50 for any model

### TELEMETRY-MOBILE-DENSITY
- GIVEN Settings is shown at a phone-sized viewport
- WHEN the Model performance rows stack into mobile cards
- THEN model names use compact body text, sample counts use the smallest metadata size, and
  row padding remains tight enough that ordinary model data does not read like a page heading

### TELEMETRY-ERROR-STATE
- GIVEN `GET /api/ai/model-telemetry` fails (500 or network error)
- WHEN the panel attempts to load and the request fails
- THEN the panel shows an error state with title "Could not load model performance", body "Something went wrong reaching the server.", and a working "Retry" button; no blank panel or console exception

### TELEMETRY-ERROR-RETRY
- GIVEN the panel is in error state with a Retry button
- WHEN I click the Retry button
- THEN the button text changes to "Retrying…", and the panel re-fetches the data; if successful, the data appears; if still failing, the error state remains

### TELEMETRY-HELP-TEXT
- GIVEN the "Model performance" panel is displayed
- WHEN I look at the help text below the title
- THEN the text explains that "model id pooling" occurs (if the same model runs through two providers) and that "JSON-retry rate only counts structured-output retries from background completions, not every kind of failure or every chat turn"

### TELEMETRY-SMOKE-SETTINGS-AI-MODEL
- GIVEN I am on Settings → AI Model
- WHEN the page loads with the new "Model performance" panel
- THEN the rest of the page (provider config section, AI spend panel, etc. above and below the telemetry panel) still renders correctly, with no overlaps, broken layouts, or shifted content
