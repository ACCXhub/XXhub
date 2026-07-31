param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = "1.4.0"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Output = Join-Path $Root "output"
$Msi = Join-Path $Output "AutoDy-$Version-x64.msi"
$Work = Join-Path $Output "msi-lifecycle-work"
$ReportJson = Join-Path $Output "msi-lifecycle-report.json"
$ReportMarkdown = Join-Path $Output "msi-lifecycle-report.md"
$InstallRoot = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "Programs\AutoDy"
$InstalledDataRoot = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "AutoDy"
$RepositoryDataRoot = Join-Path $Root "data"
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "AutoDy Management.lnk"
$StartMenuShortcut = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs\AutoDy\AutoDy Management.lnk"
$ShortcutPaths = @($DesktopShortcut, $StartMenuShortcut)

function Assert-OutputChild {
    param([Parameter(Mandatory = $true)][string]$Path)
    $resolvedOutput = [IO.Path]::GetFullPath($Output).TrimEnd('\') + '\'
    $resolvedPath = [IO.Path]::GetFullPath($Path)
    if (-not $resolvedPath.StartsWith($resolvedOutput, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the release output directory."
    }
    return $resolvedPath
}

function Reset-OutputDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    $resolved = Assert-OutputChild $Path
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $resolved | Out-Null
}

function Get-ProductCode {
    param([Parameter(Mandatory = $true)][string]$MsiPath)
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $database = $null
    $view = $null
    $record = $null
    try {
        $database = $installer.OpenDatabase($MsiPath, 0)
        $view = $database.OpenView("SELECT ``Value`` FROM ``Property`` WHERE ``Property`` = 'ProductCode'")
        [void]$view.Execute()
        $record = $view.Fetch()
        if ($record -eq $null) {
            throw "MSI ProductCode is missing."
        }
        return $record.StringData(1)
    } finally {
        if ($record -ne $null) {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($record)
        }
        if ($view -ne $null) {
            [void]$view.Close()
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($view)
        }
        if ($database -ne $null) {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($database)
        }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer)
    }
}

function Get-ProductState {
    param([Parameter(Mandatory = $true)][string]$ProductCode)
    $installer = New-Object -ComObject WindowsInstaller.Installer
    try {
        return $installer.ProductState($ProductCode)
    } finally {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer)
    }
}

function Invoke-MsiChecked {
    param(
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string]$Arguments,
        [Parameter(Mandatory = $true)][string]$LogPath
    )
    $fullArguments = "$Arguments /qn /norestart /l*v `"$LogPath`""
    $process = Start-Process -FilePath "msiexec.exe" -ArgumentList $fullArguments `
        -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -notin @(0, 3010)) {
        throw "$Stage failed with msiexec exit code $($process.ExitCode)."
    }
}

function Get-DataSnapshot {
    param([Parameter(Mandatory = $true)][string]$Path)
    $snapshot = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return $snapshot
    }
    foreach ($file in (Get-ChildItem -LiteralPath $Path -Recurse -Force -File)) {
        $relative = $file.FullName.Substring($Path.Length).TrimStart('\')
        $snapshot[$relative] = "$($file.Length):$($file.LastWriteTimeUtc.Ticks)"
    }
    return $snapshot
}

function Test-SnapshotsEqual {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Before,
        [Parameter(Mandatory = $true)][hashtable]$After
    )
    if ($Before.Count -ne $After.Count) {
        return $false
    }
    foreach ($key in $Before.Keys) {
        if (-not $After.ContainsKey($key) -or $Before[$key] -ne $After[$key]) {
            return $false
        }
    }
    return $true
}

function Assert-Shortcut {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Expected installer shortcut is missing."
    }
    $shell = New-Object -ComObject WScript.Shell
    try {
        $shortcut = $shell.CreateShortcut($Path)
        if ([IO.Path]::GetFileName($shortcut.TargetPath) -ne "wscript.exe" -or
            $shortcut.Arguments -ne "`"$InstallRoot\scripts\start-dashboard.vbs`"" -or
            [IO.Path]::GetFullPath($shortcut.WorkingDirectory).TrimEnd('\') -ne $InstallRoot.TrimEnd('\')) {
            throw "Installer shortcut target or arguments are incorrect."
        }
    } finally {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
    }
}

function Get-SanitizedText {
    param([Parameter(Mandatory = $true)][string]$Text)
    $profile = [Environment]::GetFolderPath("UserProfile")
    return $Text.Replace($Root, "<repo>").Replace($Output, "<output>").Replace($profile, "<profile>")
}

if (-not (Test-Path -LiteralPath $Msi -PathType Leaf)) {
    throw "MSI artifact is missing."
}

Reset-OutputDirectory $Work
$ProductCode = Get-ProductCode $Msi
if ((Get-ProductState $ProductCode) -ne -1) {
    throw "The release MSI is already installed; refusing to overwrite an existing installation."
}

$shortcutBackups = @{}
for ($index = 0; $index -lt $ShortcutPaths.Count; $index++) {
    $path = $ShortcutPaths[$index]
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        $backup = Join-Path $Work "shortcut-$index.lnk"
        Copy-Item -LiteralPath $path -Destination $backup -Force
        $item = Get-Item -LiteralPath $path
        $shortcutBackups[$path] = [ordered]@{
            existed = $true
            backup = $backup
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash
            last_write_time = $item.LastWriteTime
        }
    } else {
        $shortcutBackups[$path] = [ordered]@{ existed = $false }
    }
}

$installedDataBefore = Get-DataSnapshot $InstalledDataRoot
$repositoryDataBefore = Get-DataSnapshot $RepositoryDataRoot
$stages = [ordered]@{
    fresh_install = "not_run"
    repair = "not_run"
    upgrade = "not_testable_no_prior_msi_baseline"
    uninstall = "not_run"
    shortcuts = "not_run"
    data_preservation = "not_run"
    shortcut_restoration = "not_run"
}
$failure = $null
$installedByTest = $false

try {
    Invoke-MsiChecked "Fresh install" "/i `"$Msi`"" (Join-Path $Work "install.log")
    $installedByTest = $true
    if ((Get-ProductState $ProductCode) -ne 5) {
        throw "MSI product did not reach the installed state."
    }
    foreach ($relative in @(
        "runtime\python\python.exe",
        "runtime\ms-playwright",
        "scripts\start-dashboard.vbs",
        "scripts\autody-tray.ps1",
        "assets\icons\autody.ico"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot $relative))) {
            throw "Installed payload is incomplete."
        }
    }
    $stages.fresh_install = "passed"

    Assert-Shortcut $DesktopShortcut
    Assert-Shortcut $StartMenuShortcut
    $stages.shortcuts = "passed"

    Invoke-MsiChecked "Repair" "/fa $ProductCode" (Join-Path $Work "repair.log")
    if ((Get-ProductState $ProductCode) -ne 5) {
        throw "MSI product did not remain installed after repair."
    }
    Assert-Shortcut $DesktopShortcut
    Assert-Shortcut $StartMenuShortcut
    $stages.repair = "passed"

    Invoke-MsiChecked "Uninstall" "/x $ProductCode" (Join-Path $Work "uninstall.log")
    $installedByTest = $false
    if ((Get-ProductState $ProductCode) -ne -1) {
        throw "MSI product remains registered after uninstall."
    }
    if (Test-Path -LiteralPath $InstallRoot) {
        $remainingInstalledFiles = @(Get-ChildItem -LiteralPath $InstallRoot -Recurse -Force -File)
        if ($remainingInstalledFiles.Count -gt 0) {
            throw "Installed program files remain after uninstall."
        }
    }
    $stages.uninstall = "passed"
} catch {
    $failure = Get-SanitizedText $_.Exception.Message
} finally {
    if ($installedByTest -or (Get-ProductState $ProductCode) -ne -1) {
        try {
            Invoke-MsiChecked "Cleanup uninstall" "/x $ProductCode" (Join-Path $Work "cleanup-uninstall.log")
            $installedByTest = $false
        } catch {
            if ($failure -eq $null) {
                $failure = Get-SanitizedText $_.Exception.Message
            }
        }
    }

    foreach ($path in $ShortcutPaths) {
        $backup = $shortcutBackups[$path]
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Remove-Item -LiteralPath $path -Force
        }
        if ($backup.existed) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
            Copy-Item -LiteralPath $backup.backup -Destination $path -Force
            (Get-Item -LiteralPath $path).LastWriteTime = $backup.last_write_time
            if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash -ne $backup.sha256) {
                if ($failure -eq $null) {
                    $failure = "A pre-existing shortcut was not restored byte-for-byte."
                }
            }
        }
    }
    $stages.shortcut_restoration = if ($failure -eq "A pre-existing shortcut was not restored byte-for-byte.") {
        "failed"
    } else {
        "passed"
    }

    $installedDataAfter = Get-DataSnapshot $InstalledDataRoot
    $repositoryDataAfter = Get-DataSnapshot $RepositoryDataRoot
    if ((Test-SnapshotsEqual $installedDataBefore $installedDataAfter) -and
        (Test-SnapshotsEqual $repositoryDataBefore $repositoryDataAfter)) {
        $stages.data_preservation = "passed"
    } else {
        $stages.data_preservation = "failed"
        if ($failure -eq $null) {
            $failure = "AutoDy user data changed during MSI lifecycle verification."
        }
    }
}

$report = [ordered]@{
    schema_version = 1
    version = $Version
    generated_at = [DateTimeOffset]::Now.ToString("o")
    passed = ($failure -eq $null)
    product_state = Get-ProductState $ProductCode
    stages = $stages
    data_file_counts = [ordered]@{
        installed_data_root = $installedDataBefore.Count
        repository_data_root = $repositoryDataBefore.Count
    }
    failure = $failure
}
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ReportJson -Encoding utf8

$markdown = @(
    "# AutoDy $Version MSI Lifecycle Verification",
    "",
    "- Result: $(if ($report.passed) { 'PASS' } else { 'FAIL' })",
    "- Fresh install: $($stages.fresh_install)",
    "- Repair: $($stages.repair)",
    "- Upgrade: $($stages.upgrade)",
    "- Uninstall: $($stages.uninstall)",
    "- Shortcuts: $($stages.shortcuts)",
    "- Data preservation: $($stages.data_preservation)",
    "- Pre-existing shortcut restoration: $($stages.shortcut_restoration)",
    "- Final product state: $($report.product_state)"
)
$markdown -join "`r`n" | Set-Content -LiteralPath $ReportMarkdown -Encoding utf8

$resolvedWork = Assert-OutputChild $Work
if (Test-Path -LiteralPath $resolvedWork) {
    Remove-Item -LiteralPath $resolvedWork -Recurse -Force
}

if (-not $report.passed) {
    throw "MSI lifecycle verification failed. See output reports."
}

Write-Host "MSI lifecycle verification passed."
Write-Host "Fresh install, repair, uninstall, shortcuts, data preservation, and shortcut restoration passed."
