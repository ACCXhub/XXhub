function Resolve-AutoDyRuntimeRoots {
    [CmdletBinding()]
    param(
        [string]$ProgramRoot,
        [string]$DataRoot,
        [switch]$DevelopmentMode,
        [Parameter(Mandatory = $true)]
        [string]$ScriptRoot
    )

    $Registration = Get-ItemProperty `
        -LiteralPath "HKCU:\Software\AutoDy" `
        -ErrorAction SilentlyContinue
    $RegisteredProgramRoot = if ($Registration) {
        [string]$Registration.InstallFolder
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
    $ResolvedDataRoot = if ($DataRoot) {
        [IO.Path]::GetFullPath($DataRoot)
    } elseif ($env:AUTODY_HOME) {
        [IO.Path]::GetFullPath($env:AUTODY_HOME)
    } else {
        $ResolvedProgramRoot
    }

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
