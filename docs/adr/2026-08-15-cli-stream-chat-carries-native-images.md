---
title: CLI stream chat carries native images
status: Accepted
date: 2026-08-15
deciders: orchestrating Codex agent + repo owner
---

# CLI stream chat carries native images

Supersedes [2026-08-08-cli-transports-accept-images-via-complete](2026-08-08-cli-transports-accept-images-via-complete.md).

## Context

The web and connector conversation seam now applies one image policy before calling a
provider: send the original image when the active chat path supports vision; otherwise run
the configured image reader and send its transcription. Capability metadata still marked
both CLI providers as unable to receive images through `stream_chat`, because their adapters
flattened every image block into an omission placeholder.

That was an adapter limitation, not a CLI protocol limitation. Claude's stream-JSON user
message accepts the same base64 image content blocks already used by `complete()`. Codex
`exec` accepts image paths through `-i`, and the installed Codex app-server's generated
`UserInput` schema accepts `localImage` paths on `turn/start`.

## Decision

We will preserve the flattened conversation transcript as text, leave a numbered attachment
marker at each image's original position, and carry the binary payloads in encounter order
through each CLI's native channel:

- Claude one-shot and live sessions use stream-JSON image content blocks.
- Codex exec uses the existing temporary files and `-i` arguments.
- Codex app-server uses temporary files and `localImage` turn inputs.

A reused Claude live session sends images only from the unseen message tail. A fresh process
or ephemeral Codex thread receives a newest-first bounded image projection: retained images
keep their transcript positions, while older overflow becomes an explicit historical-image
omission marker. Image bytes are part of Claude's session fingerprint, so replacing an
attachment invalidates a stale live session. The model capability ladder remains authoritative:
a known text-only model still uses the configured image-reader fallback.

Native delivery is bounded at both the shared conversation boundary and the CLI conversion
boundary: only PNG, JPEG, WebP, and GIF with strict base64 are accepted, with at most 40 images,
10 MiB per image, and 40 MiB decoded bytes combined per provider request. Codex materializes
files with mode `0600` inside a `0700` temporary directory; setup failure, cancellation, normal
completion, and provider failure all remove the directory.

## Consequences

### Positive

- Web, Telegram, and future channels can send the same provider-ready image blocks without a
  CLI-specific fallback branch.
- Claude and Codex CLI users avoid an extra image-reader model call when their selected model
  can read the attachment directly.
- Codex keeps the preferred streaming app-server transport for image turns.

### Negative

- A stateless provider round must re-materialize and resend the newest bounded historical-image
  projection because that fresh process/thread has no other copy of the transcript. Older images
  remain in stored history but are honestly omitted from that provider request.
- Codex images briefly exist as `0600` files in a dedicated `0700` temporary directory; cleanup
  happens on setup failure, cancellation, normal completion, or provider failure.

### Follow-ups

- Keep the image-reader fallback for models explicitly known or configured as text-only.
- If either upstream CLI changes its image input schema, update its wire-contract test before
  changing capability metadata.

## Alternatives considered

- Always transcribe CLI images first — rejected because it adds latency, cost, and information
  loss even though both native protocols accept the original image.
- Route every Codex image turn to `exec -i` — rejected because app-server already supports
  `localImage`, and silently losing streaming/reasoning would be a transport downgrade.
- Embed base64 data URLs in Codex app-server JSON — rejected because `localImage` is the
  generated first-class local-file contract and avoids very large JSON-RPC frames.

## Agent provenance

- **Decided by:** orchestrating Codex agent with repo-owner direction
- **Lane / branch:** `main`, shared working tree
- **Alternatives weighed:** shared reader fallback, Codex exec downgrade, native per-CLI image channels
- **Driving artifacts:** owner report contrasting web and Telegram images; item 6 of the agreed repair order; superseded ADR 2026-08-08
- **Why this way (one line):** provider adapters should expose capabilities the installed CLI protocols genuinely support, while channel adapters remain transport-only.
