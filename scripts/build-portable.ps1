param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = "1.4.3",
    [switch]$ReuseRuntime,
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
$SharedRuntimeStage = Join-Path $Output "work\msi-stage-v$Version\AutoDy"
$ArchiveName = "AutoDy-Windows-Portable-$Version.zip"
$ChecksumName = "$ArchiveName.sha256"
$Archive = Join-Path $ReleaseDirectory $ArchiveName
$Checksum = Join-Path $ReleaseDirectory $ChecksumName

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

& (Join-Path $PSScriptRoot "build-msi.ps1") `
    -Version $Version `
    -StageOnly `
    -ReuseRuntime:$ReuseRuntime
if ($LASTEXITCODE -ne 0) { throw "Standalone runtime staging failed." }
if (-not (Test-Path -LiteralPath $SharedRuntimeStage -PathType Container)) {
    throw "Standalone runtime staging did not produce the expected directory."
}

if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
if (Test-Path -LiteralPath $Archive) { Remove-Item -LiteralPath $Archive -Force }
if (Test-Path -LiteralPath $Checksum) { Remove-Item -LiteralPath $Checksum -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Get-ChildItem -LiteralPath $SharedRuntimeStage -Force | Copy-Item -Destination $Stage -Recurse -Force
"portable" | Set-Content -LiteralPath (Join-Path $Stage "runtime\distribution-mode.txt") -Encoding ascii -NoNewline

# Portable contains the same embedded Python, native dependencies, production
# frontend, Chromium and message packs as MSI. It intentionally excludes source
# checkout and local data: .venv, src, data, config.yaml, messages.txt,
# node_modules, browser-profile, avatar-cache, discovered_friends,
# account-profile, account-profiles, account-avatar, screenshots and logs.
$required = @(
    "AutoDy.cmd",
    "runtime\distribution-mode.txt",
    "runtime\python\python.exe",
    "runtime\python\Lib\site-packages\autody\web\static\index.html",
    "runtime\ms-playwright",
    "message-packs\index.json",
    "optional-modules\AutoDy-Test-Center.autody-module.zip",
    "scripts\autody-tray.ps1",
    "scripts\run-scheduled.ps1"
)
foreach ($relative in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $Stage $relative))) {
        throw "Portable staging is missing required standalone payload: $relative"
    }
}

$entries = @(Get-ChildItem -LiteralPath $Stage -Recurse -Force | ForEach-Object {
    $_.FullName.Substring($Stage.Length).TrimStart('\')
})
$forbiddenSegments = @(
    ".venv", "src", "node_modules", "data", "browser-profile",
    "avatar-cache", "account-profile", "account-profiles", "account-avatar",
    "screenshots", "logs", "history", "preflight"
)
$forbiddenFiles = @(
    "config.yaml", "messages.txt", "discovered_friends.json",
    "capture-doc-screenshots.py", "PROJECT_HANDOFF.md", "build-msi.ps1",
    "verify-msi-lifecycle.ps1", "verify-release-artifacts.ps1"
)
foreach ($entry in $entries) {
    $segments = @($entry.Split('\') | ForEach-Object { $_.ToLowerInvariant() })
    $leaf = if ($segments.Count) { $segments[-1] } else { "" }
    if (($segments | Where-Object { $_ -in $forbiddenSegments }) -or $leaf -in $forbiddenFiles) {
        throw "Portable archive staging contains excluded local or development data: $entry"
    }
}

Convert-ReleaseTextFilesToLf -Root $Stage
New-ReproducibleZipFromDirectory -SourceDirectory $Stage -DestinationPath $Archive
$hash = Get-ReleaseFileSha256 -Path $Archive
"$hash  $ArchiveName" | Set-Content -Encoding ascii -NoNewline $Checksum
$null = Assert-CanonicalReleaseArtifact -Root $Root -Version $Version -Path $Archive
$null = Assert-CanonicalReleaseArtifact -Root $Root -Version $Version -Path $Checksum
Write-Host "Portable archive: $Archive"
Write-Host "SHA-256: $Checksum"
