# Pester 5 tests for scripts/install.ps1. Run on windows-latest CI: Invoke-Pester scripts/install.Tests.ps1
BeforeAll {
    $env:ONW_INSTALL_NO_RUN = '1'
    . "$PSScriptRoot/install.ps1"
    # PowerShell on Linux/macOS (how this is also run locally) has no Authenticode cmdlet to mock.
    if (-not (Get-Command Get-AuthenticodeSignature -ErrorAction SilentlyContinue)) {
        function global:Get-AuthenticodeSignature { param($LiteralPath) }
    }
    function New-Fixture([string]$Hash) {
        $dir = Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $dir | Out-Null
        Set-Content -LiteralPath (Join-Path $dir $InstallerName) -Value 'not really an installer' -NoNewline
        $real = (Get-FileHash -LiteralPath (Join-Path $dir $InstallerName) -Algorithm SHA256).Hash.ToLowerInvariant()
        if (-not $Hash) { $Hash = $real }
        Set-Content -LiteralPath (Join-Path $dir 'SHA256SUMS') -Value "$Hash  $InstallerName"
        return $dir
    }
}

Describe 'checksum verification' {
    It 'accepts a matching hash' {
        $dir = New-Fixture $null
        { Assert-Checksum (Join-Path $dir $InstallerName) (Join-Path $dir 'SHA256SUMS') $InstallerName } | Should -Not -Throw
    }
    It 'refuses a mismatching hash' {
        $dir = New-Fixture ('0' * 64)
        { Assert-Checksum (Join-Path $dir $InstallerName) (Join-Path $dir 'SHA256SUMS') $InstallerName } | Should -Throw '*Checksum mismatch*'
    }
    It 'refuses when SHA256SUMS is missing' {
        $dir = New-Fixture $null
        Remove-Item (Join-Path $dir 'SHA256SUMS')
        { Assert-Checksum (Join-Path $dir $InstallerName) (Join-Path $dir 'SHA256SUMS') $InstallerName } | Should -Throw '*SHA256SUMS is missing*'
    }
    It 'refuses when SHA256SUMS has no entry for the installer' {
        $dir = New-Fixture $null
        Set-Content -LiteralPath (Join-Path $dir 'SHA256SUMS') -Value ('a' * 64 + '  something-else.exe')
        { Assert-Checksum (Join-Path $dir $InstallerName) (Join-Path $dir 'SHA256SUMS') $InstallerName } | Should -Throw '*no entry*'
    }
}

Describe 'signature policy' {
    # The fixture is not a real executable, so the status is supplied rather than computed.
    It 'refuses an unsigned installer when a signature is required' {
        Mock Get-AuthenticodeSignature { [pscustomobject]@{ Status = 'NotSigned' } }
        { Assert-Signature 'x.exe' $false } | Should -Throw '*not code-signed*'
    }
    It 'accepts an unsigned installer by default' {
        Mock Get-AuthenticodeSignature { [pscustomobject]@{ Status = 'NotSigned' } }
        { Assert-Signature 'x.exe' $true } | Should -Not -Throw
    }
    It 'accepts a valid signature' {
        Mock Get-AuthenticodeSignature { [pscustomobject]@{ Status = 'Valid' } }
        { Assert-Signature 'x.exe' $false } | Should -Not -Throw
    }
    It 'refuses an invalid signature even when unsigned installers are allowed' {
        Mock Get-AuthenticodeSignature { [pscustomobject]@{ Status = 'HashMismatch' } }
        { Assert-Signature 'x.exe' $true } | Should -Throw '*not valid*'
    }
}

Describe 'Install-OpenNetWorth' {
    BeforeEach { Mock Assert-Windows64 {} }
    It 'never runs an installer whose checksum does not match' {
        $dir = New-Fixture ('0' * 64)
        Mock Get-AuthenticodeSignature { [pscustomobject]@{ Status = 'Valid' } }
        Mock Start-Process { throw 'the installer must not run' }
        { Install-OpenNetWorth $true $dir } | Should -Throw '*Checksum mismatch*'
        Should -Invoke Start-Process -Times 0
    }
    It 'runs a verified installer' {
        $dir = New-Fixture $null
        Mock Get-AuthenticodeSignature { [pscustomobject]@{ Status = 'Valid' } }
        Mock Start-Process { $p = [pscustomobject]@{ ExitCode = 0; Handle = 1 }; $p | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($ms) $true }; $p }
        { Install-OpenNetWorth $false $dir } | Should -Not -Throw
        Should -Invoke Start-Process -Times 1
    }
}

Describe 'Invoke-Installer' {
    It 'does not pass -Wait, which would wait for the app the installer launches' {
        Mock Start-Process { $p = [pscustomobject]@{ ExitCode = 0; Handle = 1 }; $p | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($ms) $true }; $p }
        Invoke-Installer 'setup.exe'
        Should -Invoke Start-Process -Times 1 -ParameterFilter { -not $Wait }
    }
    It 'reports a non-zero exit code' {
        Mock Start-Process { $p = [pscustomobject]@{ ExitCode = 5; Handle = 1 }; $p | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($ms) $true }; $p }
        { Invoke-Installer 'setup.exe' } | Should -Throw '*code 5*'
    }
    It 'kills the installer and fails when it does not finish in time' {
        $global:OnwKilled = $false
        Mock Start-Process { $p = [pscustomobject]@{ ExitCode = 0; Handle = 1 }; $p | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($ms) $false }; $p | Add-Member -MemberType ScriptMethod -Name Kill -Value { $global:OnwKilled = $true }; $p }
        { Invoke-Installer 'setup.exe' 1 } | Should -Throw '*did not finish*'
        $global:OnwKilled | Should -BeTrue
    }
}
