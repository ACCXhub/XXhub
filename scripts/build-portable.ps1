Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Output = Join-Path $Root "output"
$Stage = Join-Path $Output "AutoDy-Windows"
$Archive = Join-Path $Output "AutoDy-Windows-Portable.zip"
$Checksum = Join-Path $Output "AutoDy-Windows-Portable.zip.sha256"
$ModuleArchive = Join-Path $Output "AutoDy-Test-Center.autody-module.zip"
$ModuleChecksum = Join-Path $Output "AutoDy-Test-Center.autody-module.zip.sha256"

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
    "src", "scripts", "docs", "assets", "message-packs", ".github",
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
# Python bytecode is generated locally and is neither needed nor appropriate in
# a portable source package.  Removing it from the staging tree also prevents
# stale machine-local artifacts from being distributed.
Get-ChildItem -LiteralPath $Stage -Recurse -Force -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $Stage -Recurse -Force -File -Filter *.pyc | Remove-Item -Force
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}
& $Python -c "from pathlib import Path; import sys; sys.path.insert(0, str(Path(r'$Root') / 'src')); from autody.modules import build_module_archive; build_module_archive(Path(r'$ModuleArchive'), version='1.1.0', core_version='1.3.0')"
if ($LASTEXITCODE -ne 0) { throw "Optional module package build failed." }
$moduleStage = Join-Path $Stage "optional-modules"
New-Item -ItemType Directory -Force -Path $moduleStage | Out-Null
Copy-Item -LiteralPath $ModuleArchive -Destination $moduleStage -Force

# Sensitive/runtime paths intentionally excluded: .venv, data, browser-profile,
# avatar-cache, discovered_friends.json, account-profile.json, account-avatar,
# config.yaml, messages.txt, node_modules, .git.
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $Archive -Force
$forbidden = @("config.yaml", "messages.txt", ".venv", "node_modules", "browser-profile", "avatar-cache", "account-profile", "account-avatar", "discovered_friends", "data\logs", "data\history", "data\preflight")
$entries = Get-ChildItem -LiteralPath $Stage -Recurse -Force | ForEach-Object { $_.FullName.Substring($Stage.Length).TrimStart('\\') }
foreach ($item in $forbidden) {
    if ($entries | Where-Object { $_ -like "*$item*" }) { throw "Portable archive staging contains excluded local data: $item" }
}
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash.ToLowerInvariant()
"$hash  AutoDy-Windows-Portable.zip" | Set-Content -Encoding ascii -NoNewline $Checksum
$moduleHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ModuleArchive).Hash.ToLowerInvariant()
"$moduleHash  AutoDy-Test-Center.autody-module.zip" | Set-Content -Encoding ascii -NoNewline $ModuleChecksum
Write-Host "Portable archive: $Archive"
Write-Host "SHA-256: $Checksum"
