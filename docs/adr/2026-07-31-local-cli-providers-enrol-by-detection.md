# Local-CLI providers enrol by detection, not by stored credentials

- **Date:** 2026-07-31
- **Status:** Accepted
- **Area:** AI providers / model picker

## Context

`claude_cli` and `codex_cli` are first-class entries in `PROVIDER_CATALOG` with working
providers, a version probe, curated model lists, and full transport wiring. They had been
shipped for a week and were, in practice, unreachable: no group for either CLI ever appeared
in the task-model picker, the chat model picker, the agent picker, or preferred models — on a
machine with both CLIs installed and both toggles on.

The cause is that every picker is fed by `GET /api/models?all=1`, whose provider set comes
from `ModelConfigStore.configured_provider_keys()`. That method's definition of *configured*
is "the `providers` map holds an `api_key`, a `base_url`, or a recorded `key_status`". A CLI
transport has **none of those by design**: it authenticates with the user's own interactive
`claude` / `codex` login, and its `base_url` is a repurposed, optional binary PATH whose
correct value is blank. So the two providers that need the least configuration were the only
two that could never satisfy the configured test.

A user could stumble into enrolment by expanding the row in Settings and pressing Save, which
persisted a `key_status` as a side effect. Nothing in the UI suggested that, and the row's
own status line ("No key required") implied there was nothing left to do.

## Decision

**For a transport whose catalog entry carries `cli_binary`, configured means installed.**

`configured_provider_keys()` gains a detection branch: a CLI key is included when the owner
has not toggled it off *and* `shutil.which(stored_path or cli_binary)` resolves. Detection is
also the **sole** gate for these keys — the stored-credentials branch explicitly skips them,
because a CLI row's stored `base_url`/`key_status` is a path and a past probe result, neither
of which proves the binary is still present today.

Three supporting choices fall out of it:

- **The binary name moves onto the catalog entry** (`cli_binary`). It was previously duplicated
  in `api/models._CLI_TRANSPORTS`, and detection (in `db/`) cannot import `api/`. One
  definition, in the layer that owns the catalog; `_probe_cli` now reads it from there.
- **A failed probe contributes zero models**, instead of falling through to the curated static
  floor as every network provider does. For an HTTP provider the floor means "a known model set
  we merely can't reach right now"; for a CLI a failed probe means the binary is missing or
  below the version floor, so every curated id is unrunnable. Offering them would put models in
  the picker that fail on the first turn.
- **`GET /api/model-config/providers` returns `detected_clis`**, so the Settings row can state
  Detected / Not installed. Detection writes nothing, so a read of the settings page no longer
  has the enrol-as-a-side-effect behaviour the Save button had.

The enable toggle stays authoritative: an installed CLI that the owner switched off is not
offered. That was the owner's explicit instruction — *"if these are enabled, we should
auto-detect."*

## Consequences

- Installing the Claude Code or Codex CLI makes its models selectable everywhere with no setup
  step, and uninstalling it removes them within one request — the PATH check runs per request,
  ahead of the 6-hour models cache.
- `configured_provider_keys()` is no longer a pure function of the config file; for two keys it
  reads the filesystem. This is a `shutil.which` PATH lookup (no subprocess), and the expensive
  version probe stays where it was, behind the models cache.
- Tests that assert on provider sets must stub detection rather than depend on the runner's
  PATH — `tests/test_cli_provider_autodetect.py` establishes that pattern.

## Alternatives rejected

- **Manual enrolment with a "Detect" button.** Honest, but it makes the owner perform a setup
  step for the one provider kind that has nothing to set up, forever, on every machine.
- **Auto-detect ignoring the enable toggle.** Would have made the toggle a lie for two rows.
- **Treating a stored `key_status` as enrolment for CLIs.** Cheapest patch, but it enrols a CLI
  that has since been uninstalled or repointed at a dead path.

## Agent provenance

Built by Claude Opus 5 on 2026-07-31 at the owner's request ("i can't select models from claude
code cli and codex cli here — support them"). The auto-detect-vs-manual-enrol choice was put to
the owner, who chose auto-detect gated on the enable toggle. The stored-entry hole (a stale path
enrolling an uninstalled CLI) was found while writing the tests and closed in the same change.
