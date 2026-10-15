# 2026-08-06 — The instance default is ONE resolvable model ref, not a three-field trio

**Status:** Accepted
**Owner:** the maintainer (in-session: *"it should be stored in a way that it must be resolvable to
the right [provider], can't be just bare!"*, then *"tell me what can be just moved to the correct
way and that's it — don't tell me we have to handle these cases because the existing code and
flow is like that. we fucking fix the code."*)
**Touches:** `backend/db/model_config_store.py` (`_read_raw`, `_migrate_default_model`,
`resolved`, `public`, `update`, `update_provider`, `configured_provider_keys`),
`backend/models/model_config.py`, `backend/connectors/telegram/handlers.py`,
`frontend/src/components/tabs/settings/sections/ModelSection.tsx`

## Context

`model_config.json` stored the instance default as three sibling fields:

```json
{ "provider": "openai_compatible", "base_url": "https://api.x.ai/v1", "model": "grok-4.5" }
```

Three fields that can disagree, and whose `model` half is **bare** — it names no provider, so
the same string means different things as `provider`/`base_url` change. Two defects fell out of
that shape, both found by the owner:

1. **Drift.** "Set as default" *copied* `providers[key].model` into the top-level `model`. Editing
   that same provider's model afterwards changed only the per-provider entry, so Settings
   displayed `grok-4.3` while every turn ran `grok-4.5`.
2. **A blank default was writable.** "Set as default" on a provider with no remembered model sent
   `model: ""`. `public()` does not validate, so Settings looked fine; `resolved()` then raised on
   the NEXT turn — the failure landing far from the action that caused it.

A third, separate one shared the root cause: Telegram's `/model openrouter:z-ai/glm-5.2`
validated against OpenRouter and then **stripped the prefix** before storing, so the next turn
sent an OpenRouter model id to xAI's endpoint with xAI's key.

The first instinct was to keep the trio and mirror the fields into agreement. That is a guard
around a bad shape, and the owner rejected it.

## Decision

**`default_model` — one string, `"<provider_key>:<model_id>"` — is the whole stored default.**

- `resolved()` and `public()` DERIVE everything from it. `public()` still exposes
  `provider`/`model`/`base_url` because the UI renders them, but they are a **view**, not storage.
- `update()` accepts either `default_model` or the caller-friendly
  `provider`/`base_url`/`model` triple and **composes** the latter into the ref. The
  transport→catalogue-key inference (`_infer_provider_key`) now happens ONCE, at write time,
  instead of on every read.
- The legacy trio is **deleted from disk** on first read, not kept as a compat copy — leaving it
  would reintroduce exactly the disagreement this removes.
- **First boot writes the file.** `_seed_from_env()` used to synthesise a config in memory on
  every read and never persist, so `model_config.json` stayed absent indefinitely and `AI_MODEL`
  lived on as a bare id forever. It is now composed into a ref and written once.
- Telegram stores the ref **as typed** when it names a provider.

### Deliberate sub-decisions

- **`resolved()` does NOT apply the disabled-provider gate.** An earlier draft routed it through
  `resolve_for_provider()`, which raises `ProviderDisabledError` — that made the INSTANCE DEFAULT
  start raising for a state the PUT guard already prevents. An explicit per-conversation pick
  still gates (`resolve_override`); the default reads credentials directly.
- **A supplied `base_url` is persisted on the PROVIDER entry.** An endpoint matching no preset
  lands on `custom`, whose catalogue `base_url` is empty — so a custom gateway URL was silently
  lost when composed away. It belongs on `providers[key]`, which is where every reader looks.
- **An unmappable provider goes into the ref verbatim** (`"bogus:x"`), so `resolved()` can raise
  the precise *"Unknown AI provider 'bogus'"* instead of degrading to *"not configured"*.
- **A blank model is refused at the store**, not the router: REST, the `update_model_config` tool
  and Telegram `/model` all write through `update()`. `codex_cli` keeps its documented exception
  (blank = "use the CLI's own default"), which survives as an empty tail — `codex_cli:` still
  names its provider.

## UI

The provider rows no longer own the default: the radio and the per-row "Set as default" button
are gone. Settings → AI gains a **Default model** panel with the shared `ui/ModelPicker`, which
writes `default_model` directly. Picking a model picks its provider with it, so the two can no
longer be set to contradict each other — and the answer to *"where do I choose the default
model?"* is now a control that exists.

`defaultKey` (which row shows the `Default` badge, and which row may not be disabled) is the
ref's own prefix, rather than a transport+base_url match back against the catalogue that could
land on the wrong entry.

## Consequences

**Good.** The drift bug and the blank-model bug are impossible by construction rather than
guarded. `_infer_provider_key` runs once per write instead of on every read. The default is
self-describing: one string says what runs and where. Producer #1 and #2 of bare model ids are
gone; only legacy stored rows remain, and those are read-only tolerance.

**Costs, accepted.**

- `GET /api/model-config` gained a `default_model` field; two exact-shape tests were updated.
- `update({"model": …})` with no provider and no stored default now RAISES instead of storing a
  bare id. That is the point, but it is a behaviour change for any caller that relied on it.
- 20 test fixtures write the legacy trio. They keep working — that is what the migration is for —
  but they now exercise the migration path rather than the storage path.

## Agent provenance

Implemented by a Claude agent immediately after the auto-model-switching removal
(`2026-08-06-auto-model-switching-removed`) and the picker unification
(`2026-08-06-one-model-picker`), all in one session. The scope was argued down twice by the
owner: an initial six-item blast radius was mostly padding (items that were free or one-line),
and a follow-up plan to keep the trio on disk "for one release" was rejected outright. Both
corrections were right and are reflected above.

**Incident, recorded because it cost real work:** a second Claude session was editing the same
frontend files concurrently. While cleaning up unrelated whole-file reformats, this agent ran
`git checkout HEAD -- settings.css` on a file carrying that session's uncommitted work and
destroyed four rules (`.rd-keyfield`, `.rd-keyeye`, `.rd-provgroup`, `.rd-provgroup--toggle`);
they were re-authored from the markup, so their geometry is reconstructed rather than recovered.
A follow-up rule-removal script then treated `*/` as one character and left five unterminated
comments across two stylesheets before that was caught and fixed. Lesson for the next agent:
`git checkout` is destructive to a working tree you do not exclusively own — check file mtimes
against your own edits first.
