---
title: Native Windows parity behind core seams
status: Accepted
date: 2026-10-03
deciders: Repo owner, Claude
---

# Native Windows parity behind core seams

## Context

The backend could not import on Windows (`fcntl` at module top in `core/io.py` and the automation
events), and process-group kills, `setrlimit`, `dir_fd` anchored reads and POSIX mode bits were used
directly in several layers. The owner wants Windows 10/11 x64 with the same features as macOS, not
a reduced build, and a Windows installer with updates.

## Decision

1. POSIX-only primitives are wrapped in `core/` modules with one portable interface each:
   `file_lock` (shared/exclusive `LockFileEx` locks), `process_tree`, `anchored_dir`, `platform_fs`;
   the sandbox leaf gets `win_job` (Job Object caps). Callers state intent; no caller branches on
   the OS except at those seams.
2. Where Windows cannot match a POSIX guarantee exactly, the closest enforceable mechanism is used
   and the difference is documented in `docs/WINDOWS.md` (polled file-size cap, non-atomic anchored
   open, no handle cap). The capability is not disabled.
3. The desktop ships a Squirrel.Windows installer under the stable name
   `Open-Net-Worth-windows-x64-setup.exe`; updates use `update.electronjs.org` against the public
   releases repo (`RELEASES` and the full `.nupkg` published beside the installer).
4. Signing is conditional on secrets (Azure Trusted Signing or a PFX). Unsigned builds work; an
   unsigned installer is not published by a release run unless the owner opts in with a repository
   variable, and `scripts/install.ps1` refuses an unsigned installer unless told otherwise.
5. The runtime lock is resolved `--universal` so one hashed lock installs on every platform.

## Consequences

- One lock/process/permission implementation per concern, tested on macOS, Linux and Windows CI.
- A Windows job gates the focused suites; the rest of the suite runs informationally until its
  POSIX assumptions are cleared.
- No backward-compat shim was needed: no stored data changed.
