function Get-AutoDyRegistration {
    [CmdletBinding()]
    param()

    $Registration = Get-ItemProperty `
        -LiteralPath "HKCU:\Software\AutoDy" `
        -ErrorAction SilentlyContinue
    if (-not $Registration) { return $null }
    $ProgramRootProperty = $Registration.PSObject.Properties["InstallFolder"]
    $DataRootProperty = $Registration.PSObject.Properties["DataRoot"]
    $ProgramRoot = if ($null -ne $ProgramRootProperty) {
        [string]$ProgramRootProperty.Value
    } else { "" }
    $DataRoot = if ($null -ne $DataRootProperty) {
        [string]$DataRootProperty.Value
    } else { "" }
    if ([string]::IsNullOrWhiteSpace([string]$ProgramRoot) -and
        [string]::IsNullOrWhiteSpace([string]$DataRoot)) {
        return $null
    }
    [pscustomobject]@{
        ProgramRoot = [string]$ProgramRoot
        DataRoot = [string]$DataRoot
    }
}

function Get-AutoDyDistributionMode {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProgramRoot
    )

    $ResolvedProgramRoot = [IO.Path]::GetFullPath($ProgramRoot)
    $Marker = Join-Path $ResolvedProgramRoot "runtime\distribution-mode.txt"
    if (Test-Path -LiteralPath $Marker -PathType Leaf) {
        $Mode = (Get-Content -Raw -LiteralPath $Marker).Trim().ToLowerInvariant()
        if ($Mode -notin @("installed", "portable")) {
            throw "AutoDy distribution mode marker is invalid."
        }
        return $Mode
    }
    # Backward compatibility for already-installed 1.4.x payloads created
    # before the explicit marker existed. New artifacts always carry it.
    if (Test-Path -LiteralPath (Join-Path $ResolvedProgramRoot "runtime\python\python.exe") -PathType Leaf) {
        return "installed"
    }
    return "source"
}

function Resolve-AutoDyLaunchContext {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProgramRoot,
        [string]$DataRoot
    )

    $ResolvedProgramRoot = [IO.Path]::GetFullPath($ProgramRoot)
    $DistributionMode = Get-AutoDyDistributionMode -ProgramRoot $ResolvedProgramRoot
    if ($DataRoot) {
        $ResolvedDataRoot = [IO.Path]::GetFullPath($DataRoot)
    } elseif ($DistributionMode -eq "portable") {
        $ResolvedDataRoot = $ResolvedProgramRoot
    } else {
        $Registration = Get-AutoDyRegistration
        $RegisteredProgramRoot = if ($Registration) { [string]$Registration.ProgramRoot } else { "" }
        $RegisteredDataRoot = if ($Registration) { [string]$Registration.DataRoot } else { "" }
        $RegistrationMatches = (
            -not [string]::IsNullOrWhiteSpace($RegisteredProgramRoot) -and
            -not [string]::IsNullOrWhiteSpace($RegisteredDataRoot) -and
            [IO.Path]::GetFullPath($RegisteredProgramRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) -ieq
                $ResolvedProgramRoot.TrimEnd([IO.Path]::DirectorySeparatorChar)
        )
        if ($RegistrationMatches) {
            $ResolvedDataRoot = [IO.Path]::GetFullPath($RegisteredDataRoot)
        } else {
            $LocalAppData = [Environment]::GetFolderPath("LocalApplicationData")
            if ([string]::IsNullOrWhiteSpace($LocalAppData)) {
                throw "AutoDy could not resolve the current user's LocalAppData folder."
            }
            $ResolvedDataRoot = Join-Path $LocalAppData "AutoDy"
        }
    }

    $Python = if ($DistributionMode -eq "source") {
        Join-Path $ResolvedProgramRoot ".venv\Scripts\python.exe"
    } else {
        Join-Path $ResolvedProgramRoot "runtime\python\python.exe"
    }
    $BrowserRoot = if ($DistributionMode -eq "source") {
        Join-Path $ResolvedDataRoot "data\ms-playwright"
    } else {
        Join-Path $ResolvedProgramRoot "runtime\ms-playwright"
    }
    [pscustomobject]@{
        ProgramRoot = $ResolvedProgramRoot
        DataRoot = [IO.Path]::GetFullPath($ResolvedDataRoot)
        Python = [IO.Path]::GetFullPath($Python)
        BrowserRoot = [IO.Path]::GetFullPath($BrowserRoot)
        DistributionMode = $DistributionMode
        IsPackaged = $DistributionMode -ne "source"
    }
}

function Resolve-AutoDyRuntimeRoots {
    [CmdletBinding()]
    param(
        [string]$ProgramRoot,
        [string]$DataRoot,
        [switch]$DevelopmentMode,
        [Parameter(Mandatory = $true)]
        [string]$ScriptRoot
    )

    $Registration = Get-AutoDyRegistration
    $RegisteredProgramRoot = if ($Registration) {
        [string]$Registration.ProgramRoot
    } else {
        ""
    }
    $RegisteredDataRoot = if ($Registration) {
        [string]$Registration.DataRoot
    } else {
        ""
    }
    $RegisteredInstalledMode = (
        -not $DevelopmentMode -and
        -not [string]::IsNullOrWhiteSpace($RegisteredProgramRoot) -and
        -not [string]::IsNullOrWhiteSpace($RegisteredDataRoot)
    )
    if ($RegisteredInstalledMode -and (-not $ProgramRoot -or -not $DataRoot)) {
        throw "Registered installed AutoDy requires explicit ProgramRoot and DataRoot. Repair the scheduled task from AutoDy."
    }

    $ResolvedProgramRoot = if ($ProgramRoot) {
        [IO.Path]::GetFullPath($ProgramRoot)
    } else {
        (Resolve-Path (Join-Path $ScriptRoot "..")).Path
    }
    $RequestedDataRoot = if ($DataRoot) {
        [IO.Path]::GetFullPath($DataRoot)
    } elseif ($env:AUTODY_HOME) {
        [IO.Path]::GetFullPath($env:AUTODY_HOME)
    } else { "" }

    $RuntimeContext = Resolve-AutoDyLaunchContext -ProgramRoot $ResolvedProgramRoot -DataRoot $RequestedDataRoot
    $ResolvedProgramRoot = $RuntimeContext.ProgramRoot
    $ResolvedDataRoot = $RuntimeContext.DataRoot

    if ($RegisteredInstalledMode) {
        $ExpectedProgramRoot = [IO.Path]::GetFullPath($RegisteredProgramRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
        $ExpectedDataRoot = [IO.Path]::GetFullPath($RegisteredDataRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
        if (
            $ResolvedProgramRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) -ine $ExpectedProgramRoot -or
            $ResolvedDataRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) -ine $ExpectedDataRoot
        ) {
            throw "Registered installed AutoDy runtime roots do not match. Repair the scheduled task from AutoDy."
        }
    }

    [pscustomobject]@{
        ProgramRoot = $ResolvedProgramRoot
        DataRoot = $ResolvedDataRoot
    }
}
