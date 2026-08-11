param(
    [string]$ProgramRoot = (Join-Path $PSScriptRoot ".."),
    [string]$DataRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "resolve-runtime-roots.ps1")
$RuntimeContext = Resolve-AutoDyLaunchContext -ProgramRoot $ProgramRoot -DataRoot $DataRoot
$Root = $RuntimeContext.ProgramRoot
$DataRoot = $RuntimeContext.DataRoot
$Python = $RuntimeContext.Python
$BrowserRoot = $RuntimeContext.BrowserRoot
$Config = Join-Path $DataRoot "config.yaml"
$env:AUTODY_HOME = $DataRoot
$env:AUTODY_PROGRAM_ROOT = $Root
$env:AUTODY_BROWSERS_PATH = $BrowserRoot
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserRoot
$env:PLAYWRIGHT_SKIP_BROWSER_GC = "1"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "AutoDy Python runtime was not found. Repair or reinstall this AutoDy distribution."
}
if (-not (Test-Path -LiteralPath $Config -PathType Leaf)) {
    throw "AutoDy configuration was not found in the resolved data root."
}

& $Python -m autody.cli repair-playwright --config $Config
if ($LASTEXITCODE -ne 0) {
    throw "Playwright repair failed with exit code $LASTEXITCODE."
}
