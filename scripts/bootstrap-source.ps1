param(
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = [IO.Path]::GetFullPath($Root).TrimEnd('\')
$RuntimeContextScript = Join-Path $PSScriptRoot 'resolve-runtime-roots.ps1'
. $RuntimeContextScript
$RuntimeContext = Resolve-AutoDyLaunchContext -ProgramRoot $Root
$Root = $RuntimeContext.ProgramRoot
$DataRoot = $RuntimeContext.DataRoot
$Venv = Join-Path $Root '.venv'
$Python = $RuntimeContext.Python
$Frontend = Join-Path $Root 'frontend'
$DependencyLock = Join-Path $Root 'requirements-dev.lock'
$BrowserPath = $RuntimeContext.BrowserRoot

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    Write-Host "[INFO] $Stage"
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $outputText = (& $FilePath @Arguments 2>&1 | Out-String)
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousPreference
        if ($exitCode -ne 0) {
            $sanitized = $outputText.Replace($Root, '<source>')
            throw "$Stage failed with exit code $exitCode.`n$sanitized"
        }
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Test-Python311 {
    param([Parameter(Mandatory = $true)][string]$FilePath, [string[]]$Prefix = @())

    try {
        $null = & $FilePath @Prefix '-c' `
            'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

$pythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
$pythonPrefix = @('-3.11')
if ($null -eq $pythonLauncher) {
    $pythonLauncher = Get-Command python.exe -ErrorAction SilentlyContinue
    $pythonPrefix = @()
}
$node = Get-Command node.exe -ErrorAction SilentlyContinue
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
$sourceMetadataPresent =
    (Test-Path -LiteralPath (Join-Path $Root 'pyproject.toml') -PathType Leaf) -and
    (Test-Path -LiteralPath (Join-Path $Frontend 'package.json') -PathType Leaf) -and
    (Test-Path -LiteralPath (Join-Path $Frontend 'package-lock.json') -PathType Leaf)
$checks = [ordered]@{
    windows_x64 = if ($env:OS -eq 'Windows_NT' -and [Environment]::Is64BitOperatingSystem) { 'passed' } else { 'failed' }
    python_3_11 = if ($null -ne $pythonLauncher -and (Test-Python311 -FilePath $pythonLauncher.Source -Prefix $pythonPrefix)) { 'passed' } else { 'failed' }
    node = if ($null -ne $node) { 'passed' } else { 'failed' }
    npm = if ($null -ne $npm) { 'passed' } else { 'failed' }
    source_metadata = if ($sourceMetadataPresent) { 'passed' } else { 'failed' }
    dependency_lock = if (Test-Path -LiteralPath $DependencyLock -PathType Leaf) { 'passed' } else { 'failed' }
}
$passed = @($checks.Values | Where-Object { $_ -ne 'passed' }).Count -eq 0

if ($CheckOnly) {
    [ordered]@{ passed = $passed; checks = $checks } | ConvertTo-Json -Depth 4 -Compress
    if (-not $passed) { exit 1 }
    exit 0
}
if (-not $passed) {
    $failed = @($checks.Keys | Where-Object { $checks[$_] -ne 'passed' }) -join ', '
    throw "Source prerequisites failed: $failed"
}

if (Test-Path -LiteralPath $Python -PathType Leaf) {
    if (-not (Test-Python311 -FilePath $Python)) {
        throw 'The existing .venv is not a valid Python 3.11 environment; move it aside and retry.'
    }
    Write-Host '[INFO] Reusing the valid source .venv.'
} else {
    if (Test-Path -LiteralPath $Venv) {
        throw 'The existing .venv is incomplete; move it aside and retry.'
    }
    Invoke-NativeChecked -Stage 'Create Python 3.11 virtual environment' `
        -FilePath $pythonLauncher.Source -Arguments @($pythonPrefix + @('-m', 'venv', $Venv))
}

Invoke-NativeChecked -Stage 'Install pinned Python dependencies' -FilePath $Python `
    -Arguments @('-m', 'pip', '--disable-pip-version-check', 'install', '--requirement', $DependencyLock)
Invoke-NativeChecked -Stage 'Install AutoDy from this source tree' -FilePath $Python `
    -Arguments @('-m', 'pip', '--disable-pip-version-check', 'install', '--no-deps', '--editable', $Root)

Push-Location $Frontend
try {
    Invoke-NativeChecked -Stage 'Install locked frontend dependencies' -FilePath $npm.Source `
        -Arguments @('ci', '--no-audit')
    Invoke-NativeChecked -Stage 'Build frontend assets' -FilePath $npm.Source `
        -Arguments @('run', 'build')
} finally {
    Pop-Location
}

$previousHome = [Environment]::GetEnvironmentVariable('AUTODY_HOME', 'Process')
$previousProgramRoot = [Environment]::GetEnvironmentVariable('AUTODY_PROGRAM_ROOT', 'Process')
$previousBrowsers = [Environment]::GetEnvironmentVariable('AUTODY_BROWSERS_PATH', 'Process')
$previousPlaywright = [Environment]::GetEnvironmentVariable('PLAYWRIGHT_BROWSERS_PATH', 'Process')
$previousGc = [Environment]::GetEnvironmentVariable('PLAYWRIGHT_SKIP_BROWSER_GC', 'Process')
try {
    $env:AUTODY_HOME = $DataRoot
    $env:AUTODY_PROGRAM_ROOT = $Root
    $env:AUTODY_BROWSERS_PATH = $BrowserPath
    $env:PLAYWRIGHT_BROWSERS_PATH = $BrowserPath
    $env:PLAYWRIGHT_SKIP_BROWSER_GC = '1'
    Invoke-NativeChecked -Stage 'Install pinned Playwright Chromium' -FilePath $Python `
        -Arguments @('-m', 'playwright', 'install', 'chromium')
    Invoke-NativeChecked -Stage 'Run source doctor check' -FilePath $Python `
        -Arguments @('-X', 'utf8', '-m', 'autody.cli', 'doctor', '--config', (Join-Path $Root 'config.example.yaml'))
} finally {
    [Environment]::SetEnvironmentVariable('AUTODY_HOME', $previousHome, 'Process')
    [Environment]::SetEnvironmentVariable('AUTODY_PROGRAM_ROOT', $previousProgramRoot, 'Process')
    [Environment]::SetEnvironmentVariable('AUTODY_BROWSERS_PATH', $previousBrowsers, 'Process')
    [Environment]::SetEnvironmentVariable('PLAYWRIGHT_BROWSERS_PATH', $previousPlaywright, 'Process')
    [Environment]::SetEnvironmentVariable('PLAYWRIGHT_SKIP_BROWSER_GC', $previousGc, 'Process')
}

Write-Host ''
Write-Host '[SUCCESS] Source bootstrap completed.'
Write-Host 'Next commands:'
Write-Host '  Copy-Item .\config.example.yaml .\config.yaml'
Write-Host '  .\scripts\start-dashboard.cmd'
