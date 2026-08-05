param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = "1.4.2",
    [switch]$PlanOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot 'release-common.ps1')
$Output = Join-Path $Root "output"
$ReleaseDirectory = Get-CanonicalReleaseDirectory -Root $Root -Version $Version
$Work = Join-Path $Output "work\portable-v$Version"
$Stage = Join-Path $Work "AutoDy-Windows"
$ArchiveName = "AutoDy-Windows-Portable-$Version.zip"
$ChecksumName = "$ArchiveName.sha256"
$Archive = Join-Path $ReleaseDirectory $ArchiveName
$Checksum = Join-Path $ReleaseDirectory $ChecksumName
$ModuleArchive = Join-Path $Work "AutoDy-Test-Center.autody-module.zip"
$ModuleChecksum = Join-Path $Work "AutoDy-Test-Center.autody-module.zip.sha256"

if ($PlanOnly) {
    [ordered]@{
        version = $Version
        configuration = 'Release'
        release_directory = $ReleaseDirectory
        artifact = $Archive
    } | ConvertTo-Json -Depth 3 -Compress
    return
}

New-Item -ItemType Directory -Force -Path $ReleaseDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $Work | Out-Null

Get-ChildItem -LiteralPath (Join-Path $Root "scripts") -Filter *.ps1 | ForEach-Object {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count -gt 0) {
        throw "PowerShell parser validation failed for $($_.Name): $($errors.Message -join '; ')"
    }
}

if (Test-Path $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
if (Test-Path $Archive) { Remove-Item -LiteralPath $Archive -Force }
if (Test-Path $Checksum) { Remove-Item -LiteralPath $Checksum -Force }
if (Test-Path $ModuleArchive) { Remove-Item -LiteralPath $ModuleArchive -Force }
if (Test-Path $ModuleChecksum) { Remove-Item -LiteralPath $ModuleChecksum -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

$items = @(
    "src", "scripts", "docs", "assets", "message-packs",
    "pyproject.toml", "README.md", "LICENSE", "SECURITY.md", "THIRD_PARTY_NOTICES.md",
    "config.example.yaml", "messages.example.txt", "install.cmd"
)
foreach ($item in $items) {
    $source = Join-Path $Root $item
    if (Test-Path $source) {
        Copy-Item -LiteralPath $source -Destination $Stage -Recurse -Force
    }
}
# Documentation screenshot fixtures are development-only.  They are excluded
# from portable packages so their generated runtime tree cannot be mistaken for
# user data by the release privacy scan.
$fixtureStage = Join-Path $Stage "docs\fixtures"
if (Test-Path $fixtureStage) { Remove-Item -LiteralPath $fixtureStage -Recurse -Force }
$fixtureAppStage = Join-Path $Stage "docs\fixture_app.py"
if (Test-Path $fixtureAppStage) { Remove-Item -LiteralPath $fixtureAppStage -Force }
$screenshotStage = Join-Path $Stage "docs\screenshots"
if (Test-Path $screenshotStage) { Remove-Item -LiteralPath $screenshotStage -Recurse -Force }
$screenshotToolStage = Join-Path $Stage "scripts\capture-doc-screenshots.py"
if (Test-Path $screenshotToolStage) { Remove-Item -LiteralPath $screenshotToolStage -Force }
# Repository handoff notes and release-only packaging tools are not user
# runtime content and may contain machine-specific verification paths.
foreach ($releaseOnlyPath in @(
    "docs\PROJECT_HANDOFF.md",
    "scripts\bootstrap-source.ps1",
    "scripts\build-msi.ps1",
    "scripts\build-release-from-clean-source.ps1",
    "scripts\release-common.ps1",
    "scripts\verify-msi-lifecycle.ps1",
    "scripts\verify-release-artifacts.ps1",
    "scripts\write-release-manifest.ps1"
)) {
    $releaseOnlyStage = Join-Path $Stage $releaseOnlyPath
    if (Test-Path -LiteralPath $releaseOnlyStage) {
        Remove-Item -LiteralPath $releaseOnlyStage -Force
    }
}
# Python bytecode is generated locally and is neither needed nor appropriate in
# a portable source package.  Removing it from the staging tree also prevents
# stale machine-local artifacts from being distributed.
Get-ChildItem -LiteralPath $Stage -Recurse -Force -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $Stage -Recurse -Force -File -Filter *.pyc | Remove-Item -Force
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}
& $Python -c "from pathlib import Path; import sys; sys.path.insert(0, str(Path(r'$Root') / 'src')); from autody.modules import build_official_module_archive; build_official_module_archive(Path(r'$ModuleArchive'))"
if ($LASTEXITCODE -ne 0) { throw "Optional module package build failed." }
$moduleStage = Join-Path $Stage "optional-modules"
New-Item -ItemType Directory -Force -Path $moduleStage | Out-Null
Copy-Item -LiteralPath $ModuleArchive -Destination $moduleStage -Force

# Sensitive/runtime paths intentionally excluded: .venv, data, browser-profile,
# avatar-cache, discovered_friends.json, account-profile.json, account-profiles,
# account-avatar, screenshots,
# config.yaml, messages.txt, node_modules, .git.
Convert-ReleaseTextFilesToLf -Root $Stage
New-ReproducibleZipFromDirectory -SourceDirectory $Stage -DestinationPath $Archive
$forbidden = @("config.yaml", "messages.txt", ".venv", "node_modules", "browser-profile", "avatar-cache", "account-profile", "account-profiles", "account-avatar", "discovered_friends", "screenshots", "data\logs", "data\history", "data\preflight")
$entries = Get-ChildItem -LiteralPath $Stage -Recurse -Force | ForEach-Object { $_.FullName.Substring($Stage.Length).TrimStart('\\') }
foreach ($item in $forbidden) {
    if ($entries | Where-Object { $_ -like "*$item*" }) { throw "Portable archive staging contains excluded local data: $item" }
}
$hash = Get-ReleaseFileSha256 -Path $Archive
"$hash  $ArchiveName" | Set-Content -Encoding ascii -NoNewline $Checksum
$null = Assert-CanonicalReleaseArtifact -Root $Root -Version $Version -Path $Archive
$null = Assert-CanonicalReleaseArtifact -Root $Root -Version $Version -Path $Checksum
$moduleHash = Get-ReleaseFileSha256 -Path $ModuleArchive
"$moduleHash  AutoDy-Test-Center.autody-module.zip" | Set-Content -Encoding ascii -NoNewline $ModuleChecksum
Write-Host "Portable archive: $Archive"
Write-Host "SHA-256: $Checksum"
