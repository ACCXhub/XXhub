param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = '1.4.2',
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$PreviousVersion = '1.4.1',
    [string]$PreviousMsiPath,
    [ValidatePattern('^$|^[0-9a-fA-F]{40}$')]
    [string]$Commit = '',
    [switch]$BuildOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'release-common.ps1')
$ReleaseDirectory = Get-CanonicalReleaseDirectory -Root $Root -Version $Version

if (Test-Path -LiteralPath (Join-Path $Root '.git')) {
    $head = (& git -C $Root rev-parse HEAD).Trim()
    if ([string]::IsNullOrWhiteSpace($Commit)) { $Commit = $head }
    if ($Commit -ne $head) { throw 'Requested commit does not match HEAD.' }
    $changes = @(& git -C $Root status --porcelain --untracked-files=no)
    if ($LASTEXITCODE -ne 0 -or $changes.Count -ne 0) {
        throw 'Clean-source release builds require a clean tracked working tree.'
    }
} elseif ([string]::IsNullOrWhiteSpace($Commit)) {
    throw 'Pass -Commit when building from a GitHub automatic source archive.'
}

& (Join-Path $PSScriptRoot 'bootstrap-source.ps1') -Root $Root
if ($LASTEXITCODE -ne 0) { throw 'Source bootstrap failed.' }

$Python = Join-Path $Root '.venv\Scripts\python.exe'
& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Python tests failed.' }
Push-Location (Join-Path $Root 'frontend')
try {
    & npm.cmd test
    if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed.' }
} finally {
    Pop-Location
}

& (Join-Path $PSScriptRoot 'build-portable.ps1') -Version $Version
if ($LASTEXITCODE -ne 0) { throw 'Portable build failed.' }
& (Join-Path $PSScriptRoot 'build-msi.ps1') -Version $Version
if ($LASTEXITCODE -ne 0) { throw 'MSI Release build failed.' }
& (Join-Path $PSScriptRoot 'verify-release-artifacts.ps1') `
    -Version $Version -ArtifactDirectory $ReleaseDirectory -ReportDirectory $ReleaseDirectory
if ($LASTEXITCODE -ne 0) { throw 'Release artifact verification failed.' }

if ($BuildOnly) {
    Write-Host "[SUCCESS] Canonical build completed without lifecycle publication approval."
    return
}
if ([string]::IsNullOrWhiteSpace($PreviousMsiPath)) {
    throw 'Pass -PreviousMsiPath to run the required upgrade lifecycle acceptance.'
}
& (Join-Path $PSScriptRoot 'verify-msi-lifecycle.ps1') `
    -Version $Version `
    -PreviousVersion $PreviousVersion `
    -ArtifactDirectory $ReleaseDirectory `
    -PreviousMsiPath $PreviousMsiPath `
    -ReportDirectory $ReleaseDirectory
if ($LASTEXITCODE -ne 0) { throw 'MSI lifecycle acceptance failed.' }
& (Join-Path $PSScriptRoot 'write-release-manifest.ps1') -Version $Version -Commit $Commit
if ($LASTEXITCODE -ne 0) { throw 'Release manifest generation failed.' }
Write-Host "[SUCCESS] Verified release output: output\release\v$Version"
