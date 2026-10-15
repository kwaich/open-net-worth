#!/usr/bin/env bash
# Runs scripts/install.Tests.ps1 (Pester 5) under PowerShell Core in a container, for hosts
# without Windows. Authenticode and Windows-only checks are mocked by the tests themselves, so
# this proves the script's logic (checksum refusal, signature policy, never running a bad
# installer), not a real install.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
docker run --rm -v "$repo/scripts:/scripts:ro" mcr.microsoft.com/powershell:lts-ubuntu-22.04 \
  pwsh -NoProfile -Command '
    Install-Module Pester -MinimumVersion 5.5 -Force -Scope CurrentUser -SkipPublisherCheck | Out-Null
    Import-Module Pester
    Invoke-Pester -Path /scripts/install.Tests.ps1 -CI -Output Detailed
  '
