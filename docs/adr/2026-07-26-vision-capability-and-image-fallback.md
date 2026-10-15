---
title: Image input is a declared per-model capability, checked before the send, with a configured reader as the fallback
status: Accepted
date: 2026-07-26
deciders: Claude, acting on an explicit owner directive ("some models don't support image processing/attachment — for example i'm using glm and it's not supporting them")
---

# Image input is a declared per-model capability, checked before the send

## Context

The owner runs GLM as the chat model. GLM accepts no image input. Attaching a screenshot or
a receipt photo produced this: the turn was assembled with a real base64 `image` block, the
provider rejected it, and `openai_provider.stream_chat` caught the failure, replaced every
image part with `[image omitted — the selected model has no vision]`, and retried once. The
model then answered from a placeholder about a file the user could see attached in their own
transcript.

Three separate defects in that:

1. **Reactive, and conditionally so.** The retry fires only when the provider's error text
   happens to contain `image`, `vision`, or `modal`. A differently-worded refusal fails the
   whole turn.
2. **It costs the full round trip every time**, including re-uploading every image still in
   the conversation history.
3. **It is the failure mode `chat.py:346` already carries a scar for** — "a silent skip here
   once sent the model answering blind about an attachment it never saw" (live incident
   2026-07-17). The retry re-created exactly that, one layer down.

The scanned-PDF paths were worse. A `pdf_pages` upload attached to chat became N image
blocks and hit the same wall. `services/statements/tiers.py::tier3_vision` — the statement
importer's vision tier — burned **two** provider calls per page (`_row_complete` retries once
at a lower token cap) before raising "the AI vision reader is unavailable **or** the
configured model doesn't support images": an ambiguity, naming no model and offering no fix.

### What the field does

Surveyed before designing, at the owner's request.

**OpenClaw** (v2026.6.6, full TS source read) is the mature answer, in four layers:
declared per-model modalities (`model.input: ["text","image"]`); a name-pattern classifier
returning `{supportsImageInput, confidence: "known"|"unknown"}` that **asks the user** on
`unknown` at onboarding; a check at the API boundary raising a typed
`UnsupportedAttachmentError("text-only-image", "active model does not accept image inputs")`
before anything is sent; and a "media understanding" pre-pass (`tools.media`) that describes
the media with an ordered fallback list of other models, **skipped entirely when the primary
model has native vision**, with the rule *"text-only models must not be routed into image
understanding."* Crucially it never loses the attachment: a text-only primary keeps the image
as an offloaded `media://inbound/*` ref an image tool can still inspect.

**Codex CLI** (`--image` + a `view_image` tool) and **Claude Code** offer no precedent — every
model they run has vision, so neither has fallback machinery.

**LibreChat** [decided *not* to track per-model vision](https://github.com/danny-avila/LibreChat/issues/11321);
uploads are disabled per endpoint in config instead. The consequences are documented in their
own tracker: the upload button shows on non-vision models, and
[switching from a vision model to a text one permanently bricks the conversation](https://github.com/danny-avila/LibreChat/discussions/13339)
because the image stays in history. **Open WebUI** has
[the same unresolved problem](https://github.com/open-webui/open-webui/issues/23263), and their
own diagnosis names the root cause precisely: the provider APIs mostly don't return
capabilities, and there is no admin-settable capability config.

The convergence: the durable fix is **declared capability metadata consulted before the
send**. The describe-pass is the value-add on top of it, not a substitute for it.

## Decision

### 1. Vision is a resolved capability, not an error we recover from

`services/vision_capability.py::supports_vision(model_ref) -> True | False | None` resolves
four sources, most-trusted first:

| Source | Origin |
|---|---|
| `user` | an explicit override the owner set |
| `learned` | recorded the first time a provider actually refused image input for this model |
| `declared` | the provider's own `architecture.input_modalities` (OpenRouter publishes it; recorded when the model list is fetched, so the runtime needs no live call) |
| *inferred* | a name-pattern heuristic (`core/vision_names.py`) |

Stored under `vision` in `model_config.json`, keyed `"<provider_key>:<model_id>"` — the same
compound form model overrides and `task_models` already use. `record_vision_capability`
enforces a provenance ratchet: a weaker source never overwrites a stronger one.

**We deviate from OpenClaw on the unknown case: we fail OPEN.** They fail closed (a model
absent from the catalog is treated as text-only), which is safe *for them* because their
offload path means nothing is lost. We have no such path, so failing closed would silently
strip image input from a genuinely vision-capable custom endpoint — a wrong `False` removes a
working feature, while a wrong `True` costs one round trip *and teaches us the answer* via
`learned`. So `None` means "send it and find out", and the reactive retry stays as the
backstop for exactly that case.

This is also why the tri-state must survive to the wire: `_model_row` in `api/models.py`
emits `vision` **only when it is a bool**, so ABSENT means "no claim". Coercing it like the
neighbouring `can_disable` would make the UI refuse attachments on unknown models.

### 2. The name heuristic lives in `core/`, the ladder in `services/`

Three layers need this question answered — `api/chat.py` before building an image block,
`ai/models/openai_provider.py` before the wire call, and `services/statements/tiers.py`
before parsing a scan. Under `api → ai → services → db → core` only `services/` can serve
all three, so the ladder lives there and the pure regex classifier is a `core/` leaf. It
could not stay in `ai/models/capabilities.py`: that module imports `openai_provider`, which
would have dragged the whole `ai` layer into `services`.

### 3. The fallback is EXTRACTION, not captioning

New `chat_vision` task pin (`VALID_TASKS`) + `services/image_extract.py`. When the chat model
is blind, one bounded call to a vision model turns the image into text that rides the turn.

OpenClaw's pre-pass defaults to a ≤500-char *description*. That is right for a general
assistant and wrong here: nobody attaches a photo to a net-worth app to be told "a receipt
from a café" — they attach it to get the merchant, date, total and line items booked. So the
prompt asks for verbatim figures and table rows, and the cap is 4000 chars.

Two OpenClaw rules are kept verbatim because both are load-bearing: **skipped entirely when
the chat model has native vision** (don't pay twice for a worse result), and **never routed
to the blind model itself**. `resolve_extractor_ref` returns `""` rather than the instance
default when that default is itself known-blind.

**Every page, or none — never a silent partial (corrected after owner review).** The first
cut of `image_extract.py` capped at 4 images and, past that, truncated with a "first 4 of 30
pages were read" note. That was wrong twice over. The number was copied from OpenClaw's
attachment policy, which governs *independent* attachments, not pages of one document — and
page 4 of a 30-page statement is not a quarter of the answer, it is a wrong answer delivered
in a confident tone. It also silently contradicted the policy this codebase had already
settled for exactly this situation: `statements/parser._VISION_PAGE_CAP` reads **every** page
up to 40 and past that **refuses loudly**.

So `MAX_IMAGES` is now 40 — the same number, reused with the same rationale, and pinned equal
to `_VISION_PAGE_CAP` by test so the two cannot drift. Past it, `extract_image_text` refuses
without making a single provider call and the user is pointed at Transactions → Import, whose
per-page pipeline (reconciliation, dedup, review) is strictly better for a document that big.
The reads are now **one call per page** with bounded concurrency, mirroring
`tiers.tier3_vision`: a single call carrying N images shares one token budget between them,
which truncates a long document silently and caps how much of each page the model bothers to
transcribe. Whenever any page is missing for any reason — unreadable, oversize, or the
whole-attachment transcript ceiling — `ExtractOutcome.gaps` names it and the caller states the
incompleteness explicitly, including "do not treat any total here as the document's total".
The remaining bound (`MAX_TOTAL_CHARS`) exists because this text rides every later turn of the
conversation, and when it bites it discloses which pages it excluded rather than trimming
quietly.

**We did NOT build a `describe_image(ref_id, question)` tool**, despite it scoring better on
the AI-native design test — an optional call the model must remember to make. This codebase's
own corpus is the argument against it: 24 sessions hand-booked 1,597 rows with zero
`import_transactions` calls. Eager beats discoverable.

### 4. Nothing is ever silently dropped, and the fix is always named

Every outcome states what happened and what to do:

- transcription available → the extracted text, fenced as untrusted, prefaced with the
  reader model's name;
- extraction failed → "You have NOT seen this — say so, and do not guess at its contents";
- no reader configured → the same, plus the two concrete remedies (switch model, or pin
  `chat_vision`);
- a scanned PDF past the 40-page ceiling → refused outright, with a pointer to Transactions →
  Import, which reads every page through its own pipeline;
- any page missing from a transcription (unreadable / oversize / transcript ceiling) → named
  individually, plus an explicit instruction not to treat any total as the document's total;
- the statement importer refuses a scan **up front** when the `statement_vision` model is
  known-blind, naming that model, naming the setting, and stating "Nothing was imported" —
  replacing an ambiguous message that cost two provider calls to produce.

The composer warns **before** the send, and distinguishes the two materially different cases
rather than hedging between them ("X will read it" vs "no model is set to read them — Lucius
won't see this attachment"). That distinction requires knowing whether a reader exists, so
`GET /api/model-config` gained a derived `image_reader` field.

## Consequences

- A GLM (or any text-only) user attaching a receipt gets its contents transcribed if they
  configure a reader, and a plain honest refusal naming the remedy if they don't. Neither
  outcome is a model answering blind.
- One wasted round trip per unknown model, once — then it is `learned`.
- A `false` guess is correctable from Settings, which is the escape hatch Open WebUI's issue
  identifies as missing.
- `TaskModelsUpdate` gained `chat_vision`; the panel gained an "Image reading" row. The panel
  still covers only 5 of 13 `VALID_TASKS` (`price_monitor`, the four `statement_*` stages and
  `news_screen` remain REST-only) — a pre-existing gap, deliberately left alone.
- The name heuristic will drift as models ship. That is why `learned` and `user` outrank it,
  and why an unrecognised name is `None` rather than a guess.

## Alternatives rejected

- **Fail closed on unknown** — OpenClaw's choice, unsafe without their offload path.
- **Disable uploads per provider** — LibreChat's choice; the bricked-conversation bug is the
  documented outcome.
- **Keep only the reactive retry** — it *is* the bug.
- **Auto-switch the whole turn to a vision model** — loses the model the user picked and its
  prompt cache, for one attachment.
- **A caller-chosen projection of what to extract** — the `_project_txn` scar (2026-07-09)
  applies: server-chosen shape only.

## Verification

`tests/test_image_extract.py` (20) pins the read policy: every page read for a 30-page scan,
a refusal with ZERO provider calls past the ceiling, `MAX_IMAGES == _VISION_PAGE_CAP`, page
ordering under concurrency, and a named gap for every unreadable/oversize/excluded page (plus
that a transcription cannot forge its own fence).
`tests/test_vision_capability.py` (16) pins the ladder, the ratchet, the fail-open unknown,
the `glm-4v` vs `glm-4.6` discrimination, and that a blind default is never offered as the
reader. `tests/test_vision_fallback_chat.py` (12) pins what the model actually receives in
each branch, including both scanned-PDF cases and the statement refusal.
`frontend/src/hooks/modelRejectsImages.test.ts` (10) and
`Composer.noVision.test.tsx` (5) pin the UI: only an explicit `false` warns, and which of the
two messages appears. `visionModelOptions.test.ts` (7) pins the Settings row — it cannot offer
a model known to reject images, it still offers an unknown one, and when the app default is
itself blind the sentinel says so instead of naming it as the reader (an owner-reported bug:
the row read "App default (z-ai/glm-5.2)" for a model with no vision).
