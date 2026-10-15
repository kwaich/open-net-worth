---
number: 2026-10-05
title: Releases are gated, signed, and built in the public releases repo
status: Accepted
date: 2026-10-05
deciders: orchestrating Claude agent + repo owner (billing and build-host decisions given in chat)
---

# 2026-10-05. Releases are gated, signed, and built in the public releases repo

## Context

- Until now a cron in the public `open-net-worth-releases` repo checked out private `main` every
  night, stamped a version on a throwaway copy and published desktop installers, gated only by
  desktop tests. The repo said 0.1.2 while 0.1.5 had shipped; no image or wheel was ever
  published; this repo's own release workflows never ran (Actions are blocked by an unpaid
  account limit the owner is not fixing for now).
- Design: `docs/specs` release-and-distribution (R1-R9) in the portal repo.

## Decision

1. **One version, committed.** `pyproject.toml` is the source; `make release-commit VERSION=x.y.z`
   (`scripts/stamp_version.py`) stamps `desktop/package.json`, its lockfile and the compose file's
   image tag in a `release: vX.Y.Z` commit; `tests/test_release_versions.py` pins agreement. Nothing
   stamps a version at build time any more. Resume at 0.2.0.
2. **One pipeline in the releases repo's `release.yml`**, started by the existing nightly cron or a
   manual dispatch (owner decision: keep the nightly). The nightly picks the next patch version and
   promotes automatically once verified; a dispatch defaults to stopping at the verified draft
   until the `release` environment's reviewer approves. The version is still stamped onto each
   build's throwaway checkout, so the committed stamp (1.) is how a deliberate release pins one. It gates (backend/frontend tests
   against committed known-failure baselines so a pre-existing red suite does not block, but any
   NEW failure does; a security scan; desktop tests per platform), builds desktop x3, the agent
   image (amd64+arm64, pushed by version, digest recorded) and the wheel, and writes a minisign-
   signed `manifest.json` (K5) + `SHA256SUMS` into a DRAFT release. A verify job downloads the
   draft and re-checks everything; `promote`, behind the `release` environment's required
   reviewer, publishes it as latest. The build host is the public repo because Actions are free
   there; the source is checked out with a read-only token, and logs carry ids only.
3. **The same pipeline lives in this repo** (`.github/workflows/release.yml`, `vX.Y.Z` tag), dormant
   until Actions run here again, sharing every script. `desktop-release.yml` and the old publisher
   script are removed: one pipeline at a time.
4. **The image reports its real version.** The Dockerfile bakes pyproject's version into
   `backend/_bundled/VERSION`, which `core/version.py` reads when there is no installed
   distribution. `/api/health` also publishes `contracts: {gateway: 1, move: 1}`.
5. **The image keeps the namespace `ghcr.io/open-net-worth-ai/open-net-worth`** (the portal's
   contract), pushed with the releases repo's token and labelled with the public repo as its
   source. Making the package public is a one-time owner step; verify only warns.

## Consequences

- The nightly still publishes without a human, but only past the gates and the verify job; a bad
  desktop release is superseded by the next patch. The baseline files are debt that must shrink; deleting entries is the only edit.
- Two copies of the pipeline YAML exist (the live one in the releases repo, the dormant one here);
  the logic lives in `scripts/`, which both call, to keep drift small.
- Windows stays unsigned and PyPI is optional until the owner sets them up (`RELEASING.md`).
