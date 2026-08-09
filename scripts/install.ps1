Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root
$env:AUTODY_HOME = $Root
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Root "data\ms-playwright"
$env:PLAYWRIGHT_SKIP_BROWSER_GC = "1"
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Config = Join-Path $Root "config.yaml"

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Stage failed with exit code $LASTEXITCODE."
    }
}

function Test-VirtualEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$ExpectedVenv
    )

    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return $false
    }

    $probe = @(& $PythonPath -c "import sys; print(sys.version_info.major); print(sys.version_info.minor); print(sys.prefix)")
    if ($LASTEXITCODE -ne 0 -or $probe.Count -ne 3) {
        return $false
    }

    try {
        $major = [int]$probe[0]
        $minor = [int]$probe[1]
        $prefix = [IO.Path]::GetFullPath([string]$probe[2]).TrimEnd('\\')
        $expected = [IO.Path]::GetFullPath($ExpectedVenv).TrimEnd('\\')
    } catch {
        return $false
    }

    if ($major -ne 3 -or $minor -lt 11 -or $prefix -ne $expected) {
        return $false
    }

    $null = & $PythonPath -m pip --version
    return $LASTEXITCODE -eq 0
}

function Get-ProjectAutoDyService {
    try {
        $connection = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop |
            Select-Object -First 1
    } catch {
        return $null
    }
    if ($null -eq $connection) {
        return $null
    }

    try {
        $identity = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/service-identity" -TimeoutSec 2
        $identityRoot = [IO.Path]::GetFullPath([string]$identity.project_path).TrimEnd('\\')
        $expectedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\\')
        $packagePath = [IO.Path]::GetFullPath([string]$identity.package_path).TrimEnd('\\')
        $expectedPackage = Join-Path $Root "src\autody"
        if ($identity.application -ne "AutoDy" -or $identityRoot -ne $expectedRoot -or
            $packagePath -ne $expectedPackage) {
            return $null
        }
        return [PSCustomObject]@{ ProcessId = $connection.OwningProcess; Identity = $identity }
    } catch {
        return $null
    }
}

function Stop-ProjectAutoDyService {
    $service = Get-ProjectAutoDyService
    if ($null -eq $service) {
        return $false
    }

    Write-Host "[INFO] Stopping identified AutoDy service (PID $($service.ProcessId))."
    Stop-Process -Id $service.ProcessId -Force -ErrorAction Stop
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 250
        if ($null -eq (Get-ProjectAutoDyService)) {
            return $true
        }
    }
    throw "The identified AutoDy service did not stop."
}

function Start-ProjectAutoDyService {
    if (Get-ProjectAutoDyService) {
        Write-Host "[INFO] AutoDy service is already running."
        return
    }

    Write-Host "[INFO] Restarting AutoDy with the project virtual environment."
    Start-Process -FilePath $Python -ArgumentList @("-m", "autody.cli", "ui", "--no-open", "--config", $Config) `
        -WorkingDirectory $Root -WindowStyle Hidden | Out-Null
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        $service = Get-ProjectAutoDyService
        if ($null -ne $service) {
            return
        }
    }
    throw "AutoDy service did not become healthy after restart."
}

function Update-FrontendBuild {
    $frontendRoot = Join-Path $Root "frontend"
    $frontendPackage = Join-Path $frontendRoot "package.json"
    if (-not (Test-Path -LiteralPath $frontendPackage -PathType Leaf)) {
        Write-Host "[INFO] Packaged frontend assets detected; no local Node.js rebuild is required."
        return
    }

    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($null -eq $npm) {
        $npm = Get-Command npm -ErrorAction SilentlyContinue
    }
    if ($null -eq $npm) {
        throw "Source frontend is present but npm was not found. Install Node.js, then run npm run build from frontend."
    }

    Write-Host "[INFO] Source frontend detected; rebuilding production static assets."
    Push-Location $frontendRoot
    try {
        Invoke-NativeChecked -Stage "Source frontend production build" -FilePath $npm.Source -Arguments @("run", "build")
    } finally {
        Pop-Location
    }
}

function New-ProjectVirtualEnvironment {
    $bootstrap = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $bootstrap) {
        Invoke-NativeChecked -Stage "Create virtual environment" -FilePath $bootstrap.Source `
            -Arguments @("-3.11", "-m", "venv", $Venv)
        return
    }

    $bootstrap = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $bootstrap) {
        throw "Python 3.11 or later was not found."
    }
    Invoke-NativeChecked -Stage "Create virtual environment" -FilePath $bootstrap.Source `
        -Arguments @("-m", "venv", $Venv)
}

$wasRunning = $null -ne (Get-ProjectAutoDyService)
if (Test-VirtualEnvironment -PythonPath $Python -ExpectedVenv $Venv) {
    Write-Host "[INFO] Reusing existing virtual environment."
} else {
    Write-Host "[INFO] Existing virtual environment is missing or invalid; creating a replacement."
    if (Test-Path -LiteralPath $Venv) {
        $backupRoot = Join-Path $Root "data\backups"
        New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
        $backup = Join-Path $backupRoot ("venv-invalid-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
        Move-Item -LiteralPath $Venv -Destination $backup -ErrorAction Stop
        Write-Host "[INFO] Backed up invalid virtual environment."
    }
    New-ProjectVirtualEnvironment
    if (-not (Test-VirtualEnvironment -PythonPath $Python -ExpectedVenv $Venv)) {
        throw "Created virtual environment failed validation."
    }
    Write-Host "[INFO] Created virtual environment."
}

if ($wasRunning -and -not (Stop-ProjectAutoDyService)) {
    throw "Refusing to update while the current AutoDy service could not be identified safely."
}

Invoke-NativeChecked -Stage "Upgrade pip" -FilePath $Python -Arguments @("-m", "pip", "install", "--upgrade", "pip")
Invoke-NativeChecked -Stage "Install editable package" -FilePath $Python -Arguments @("-m", "pip", "install", "-e", ".")
Update-FrontendBuild
Invoke-NativeChecked -Stage "Install Chromium" -FilePath $Python -Arguments @("-m", "playwright", "install", "chromium")

if (-not (Test-Path -LiteralPath $Config)) {
    Copy-Item -LiteralPath (Join-Path $Root "config.example.yaml") -Destination $Config
}
if (-not (Test-Path -LiteralPath (Join-Path $Root "messages.txt"))) {
    Copy-Item -LiteralPath (Join-Path $Root "messages.example.txt") -Destination (Join-Path $Root "messages.txt")
}

Invoke-NativeChecked -Stage "Verify AutoDy installation" -FilePath $Python -Arguments @("-m", "autody.cli", "doctor", "--config", $Config)
$Shortcut = & (Join-Path $Root "scripts\install-shortcut.ps1") -ProjectRoot $Root
if (-not $Shortcut) {
    throw "Shortcut installer returned no shortcut path."
}
$TaskProgramRoot = $Root
$TaskConfig = $Config
$TaskRepairEnabled = $true
$Registration = Get-ItemProperty -LiteralPath "HKCU:\Software\AutoDy" -ErrorAction SilentlyContinue
if ($Registration) {
    $RegisteredProgramRoot = [string]$Registration.InstallFolder
    $RegisteredDataRoot = [string]$Registration.DataRoot
    if (
        [string]::IsNullOrWhiteSpace($RegisteredProgramRoot) -or
        [string]::IsNullOrWhiteSpace($RegisteredDataRoot) -or
        -not (Test-Path -LiteralPath $RegisteredProgramRoot -PathType Container) -or
        -not (Test-Path -LiteralPath $RegisteredDataRoot -PathType Container)
    ) {
        $TaskRepairEnabled = $false
        Write-Warning "Registered AutoDy roots are incomplete; scheduled tasks were not changed. Repair the installed copy from its Dashboard."
    } else {
        $TaskProgramRoot = [IO.Path]::GetFullPath($RegisteredProgramRoot)
        $TaskConfig = Join-Path ([IO.Path]::GetFullPath($RegisteredDataRoot)) "config.yaml"
        Write-Host "[INFO] Repairing scheduled tasks for the registered AutoDy installation."
    }
}
if ($TaskRepairEnabled) {
    $TaskRepairArguments = @(
        "-m", "autody.cli", "repair-scheduler",
        "--config", $TaskConfig,
        "--program-root", $TaskProgramRoot
    )
    if ($Registration) {
        $TaskRepairArguments += "--if-config-exists"
    }
    Invoke-NativeChecked `
        -Stage "Repair scheduled tasks" `
        -FilePath $Python `
        -Arguments $TaskRepairArguments
}

if ($wasRunning) {
    Start-ProjectAutoDyService
}

Write-Host ""
Write-Host "[SUCCESS] AutoDy installation completed."
Write-Host "Desktop shortcut: $Shortcut"
