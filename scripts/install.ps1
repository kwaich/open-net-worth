<#
.SYNOPSIS
  Installs (or upgrades) Open Net Worth on Windows 10/11 x64, for the current user. No admin.

.DESCRIPTION
  irm https://opennetworth.ai/install.ps1 | iex

  Downloads Open-Net-Worth-windows-x64-setup.exe from the public releases repo over HTTPS,
  checks it against the release's SHA256SUMS (fails closed when the file is missing or the hash
  differs), checks its Authenticode signature, then runs the per-user Squirrel installer.
  Running it again upgrades: the installer replaces the installed version in place.

.PARAMETER AllowUnsigned
  Kept for compatibility: unsigned installers are accepted by default (releases are not
  code-signed yet; the SHA256SUMS check vouches for the download).
.PARAMETER RequireSigned
  Refuse an installer without a valid Authenticode signature. When piping through `iex`, set
  $env:ONW_REQUIRE_SIGNED = '1' instead. A signature that is present but invalid is always refused.

.PARAMETER SourceDirectory
  Read the installer and SHA256SUMS from this folder instead of downloading them (offline
  installs and tests).
#>
[CmdletBinding()]
param(
    [switch]$AllowUnsigned,
    [switch]$RequireSigned,
    [string]$SourceDirectory
)

$ErrorActionPreference = 'Stop'
$InstallerName = 'Open-Net-Worth-windows-x64-setup.exe'
$ReleaseBase = 'https://github.com/open-net-worth-ai/open-net-worth-releases/releases/latest/download'

function Assert-Windows64 {
    $architecture = $env:PROCESSOR_ARCHITEW6432
    if (-not $architecture) { $architecture = $env:PROCESSOR_ARCHITECTURE }
    if ($env:OS -ne 'Windows_NT' -or $architecture -ne 'AMD64') {
        throw "Open Net Worth for Windows is built for 64-bit Intel/AMD (x64) only; this machine reports '$architecture'."
    }
}

function Get-ExpectedHash([string]$SumsPath, [string]$Name) {
    if (-not (Test-Path -LiteralPath $SumsPath)) {
        throw 'SHA256SUMS is missing, so the installer cannot be verified. Nothing was installed.'
    }
    foreach ($line in Get-Content -LiteralPath $SumsPath) {
        if ($line -match '^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$' -and $Matches[2] -eq $Name) {
            return $Matches[1].ToLowerInvariant()
        }
    }
    throw "SHA256SUMS has no entry for $Name. Nothing was installed."
}

function Assert-Checksum([string]$File, [string]$SumsPath, [string]$Name) {
    $expected = Get-ExpectedHash $SumsPath $Name
    $actual = (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        throw "Checksum mismatch for $Name (expected $expected, got $actual). The download was corrupted or tampered with. Nothing was installed."
    }
}

function Assert-Signature([string]$File, [bool]$Unsigned) {
    $status = (Get-AuthenticodeSignature -LiteralPath $File).Status
    if ($status -eq 'Valid') { return }
    if ($status -eq 'NotSigned') {
        if (-not $Unsigned) {
            throw 'The installer is not code-signed and -RequireSigned (or $env:ONW_REQUIRE_SIGNED = ''1'') was given. Nothing was installed.'
        }
        # Releases are not code-signed yet: the SHA256SUMS check above is what vouches for the
        # download, so an unsigned installer is accepted unless the caller demands a signature.
        return
    }
    throw "The installer's signature is not valid ($status). Nothing was installed."
}

function Get-Release([string]$Name, [string]$Destination, [string]$Source) {
    if ($Source) {
        Copy-Item -LiteralPath (Join-Path $Source $Name) -Destination $Destination
        return
    }
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -UseBasicParsing -Uri "$ReleaseBase/$Name" -OutFile $Destination
}

# Squirrel's Setup.exe starts the app when it finishes, and `Start-Process -Wait` waits for that
# whole process tree, so it would never return while the app runs. Wait for Setup.exe alone.
function Invoke-Installer([string]$Installer, [int]$TimeoutSeconds = 600) {
    $process = Start-Process -FilePath $Installer -PassThru
    $null = $process.Handle  # keep the handle so ExitCode stays readable after exit
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $process.Kill() } catch { Write-Verbose $_ }
        throw "The installer did not finish within $TimeoutSeconds seconds."
    }
    Write-Host "==> Installer exit code: $($process.ExitCode)"
    if ($process.ExitCode -ne 0) { throw "The installer exited with code $($process.ExitCode)." }
}

function Install-OpenNetWorth([bool]$Unsigned, [string]$Source) {
    Assert-Windows64
    $work = Join-Path ([IO.Path]::GetTempPath()) ("onw-install-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        $installer = Join-Path $work $InstallerName
        $sums = Join-Path $work 'SHA256SUMS'
        Write-Host '==> Downloading Open Net Worth'
        Get-Release $InstallerName $installer $Source
        Get-Release 'SHA256SUMS' $sums $Source
        Write-Host '==> Verifying'
        Assert-Checksum $installer $sums $InstallerName
        Assert-Signature $installer $Unsigned
        Write-Host '==> Installing (per user, no administrator rights needed)'
        Invoke-Installer $installer
        Write-Host "==> Install folder: $env:LOCALAPPDATA\OpenNetWorth"
        Write-Host '==> Installed. Open Net Worth starts from the Start menu; run this command again any time to upgrade.'
    }
    finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (-not $env:ONW_INSTALL_NO_RUN) {
    # -AllowUnsigned / ONW_ALLOW_UNSIGNED are kept for compatibility; unsigned is now the default.
    Install-OpenNetWorth (-not ($RequireSigned.IsPresent -or $env:ONW_REQUIRE_SIGNED -eq '1')) $SourceDirectory
}
