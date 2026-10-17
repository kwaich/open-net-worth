---
title: Desktop updates download in the background; installing still waits for a restart
status: Accepted
date: 2026-10-17
deciders: repo owner
---

# 2026-10-17. Desktop updates download in the background; installing still waits for a restart

## Context

The desktop app detected a new release on its own, then asked before downloading: a
notification, **Update Available…** in the menu, and a Download and Install / Skip This Version /
Later dialog. Only after **Download and Install** did it call the updater. Downloading was the
only step that needed an answer, and it changes nothing the person can see. The owner asked for
the Claude desktop flow instead: the update is already downloaded, and the app menu says
**Restart to Update to <App> X.Y.Z**.

## Decision

- **A background check downloads quietly.** `UpdateController.detect(false)` calls the updater
  as soon as `hooks.newerVersion` finds a version, unless the version was skipped or the install
  cannot update in place. No dialog, no notification until it lands.
- **Ready reads as a restart.** Once downloaded, the menu and tray read **Restart to Update to Open
  Net Worth X.Y.Z**, and choosing it installs at once. One notification says it's ready. Nothing
  installs before a restart or the next quit, and the app never restarts on its own.
- **A manual check still asks.** **Check for Updates…** keeps the dialog: the person is in front
  of it and may want to skip.
- **Quiet means quiet on failure.** A failed or not-yet-published background download falls back
  to **Update Available…** with no dialog, and the next 4-hour check retries. An `update-manual`
  result (a `.deb`, a read-only AppImage) notifies instead of opening the browser. An install that
  cannot update in place keeps the old notify-and-offer path.
- **Skip still holds.** `skippedUpdateVersion` keeps that version from being downloaded, not
  only from being announced.

## Consequences

- An update can take up its download size on disk before anyone agrees. Squirrel and the Linux
  AppImage swap only stage it; the running app is unchanged until restart.
- On Linux, a writable AppImage is swapped on disk in the background, so the next launch runs the
  new version even without choosing Restart to Update. The signature and SHA-256 checks from
  [2026-10-02](2026-10-02-linux-desktop-appimage-deb-and-self-checked-updates.md) still run
  before the swap.
- Install-on-quit behaviour is unchanged by this decision, and should be confirmed on a packaged
  build like any updater change.
