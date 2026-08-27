param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = "1.5.0",
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$PreviousVersion = "1.4.4",
    [string]$ArtifactDirectory,
    [string]$PreviousMsiPath,
    [string]$ReportDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot 'release-common.ps1')
$Output = Join-Path $Root "output"
$ArtifactRoot = if ([string]::IsNullOrWhiteSpace($ArtifactDirectory)) {
    Get-CanonicalReleaseDirectory -Root $Root -Version $Version
} else {
    (Resolve-Path -LiteralPath $ArtifactDirectory).Path
}
$ReportRoot = if ([string]::IsNullOrWhiteSpace($ReportDirectory)) {
    $ArtifactRoot
} else {
    [IO.Path]::GetFullPath($ReportDirectory)
}
New-Item -ItemType Directory -Force -Path $ReportRoot | Out-Null
$Msi = Join-Path $ArtifactRoot "AutoDy-$Version-x64.msi"
$PreviousMsi = if ([string]::IsNullOrWhiteSpace($PreviousMsiPath)) {
    Join-Path $Output "baselines\v$PreviousVersion\AutoDy-$PreviousVersion-x64.msi"
} else {
    [IO.Path]::GetFullPath($PreviousMsiPath)
}
$Work = Join-Path $Output "work\msi-lifecycle-v$Version"
$ReportJson = Join-Path $ReportRoot "msi-lifecycle-report.json"
$ReportMarkdown = Join-Path $ReportRoot "msi-lifecycle-report.md"
$InstallRoot = if (Test-Path -LiteralPath "D:\" -PathType Container) {
    "D:\AutoDy"
} else {
    Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "Programs\AutoDy"
}
$CustomInstallRoot = Join-Path $Work "custom-install\AutoDy"
$InstalledDataRoot = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "AutoDy"
$RepositoryDataRoot = Join-Path $Root "data"
$ExpectedUpgradeCode = "{C8B3A04F-ABAB-4F4C-8A2C-7A1AE24F1400}"
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "AutoDy Management.lnk"
$StartMenuShortcut = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs\AutoDy\AutoDy Management.lnk"
$UninstallShortcut = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs\AutoDy\卸载 AutoDy.lnk"
$ShortcutPaths = @($DesktopShortcut, $StartMenuShortcut, $UninstallShortcut)
$ScheduledTaskNames = @("AutoDy-DailySpark", "AutoDy-Health-Daily", "AutoDy-Health-Weekly")

if (Test-Path -LiteralPath "HKCU:\Software\AutoDy") {
    throw "Refusing to run MSI lifecycle verification in a user profile with existing AutoDy registration. Use a clean Windows user profile."
}
if (Test-Path -LiteralPath $InstallRoot) {
    throw "Refusing to run MSI lifecycle verification because the resolved install directory already exists. Use a clean Windows user profile."
}
if (Test-Path -LiteralPath $InstalledDataRoot) {
    throw "Refusing to run MSI lifecycle verification because the AutoDy runtime-data directory already exists. Use a clean Windows user profile."
}
foreach ($taskName in $ScheduledTaskNames) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        throw "Refusing to run MSI lifecycle verification because an AutoDy scheduled task already exists: $taskName"
    }
}

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

function Get-MsiRows {
    param(
        [Parameter(Mandatory = $true)][string]$MsiPath,
        [Parameter(Mandatory = $true)][string]$Query,
        [Parameter(Mandatory = $true)][int]$FieldCount
    )
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $database = $null
    $view = $null
    $rows = New-Object System.Collections.ArrayList
    try {
        $database = $installer.OpenDatabase($MsiPath, 0)
        $view = $database.OpenView($Query)
        [void]$view.Execute()
        while ($record = $view.Fetch()) {
            $values = @()
            for ($index = 1; $index -le $FieldCount; $index++) {
                $values += $record.StringData($index)
            }
            [void]$rows.Add([pscustomobject]@{ Values = $values })
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($record)
        }
    } finally {
        if ($view -ne $null) {
            [void]$view.Close()
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($view)
        }
        if ($database -ne $null) {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($database)
        }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer)
    }
    return $rows.ToArray()
}

function Get-MsiProperty {
    param(
        [Parameter(Mandatory = $true)][string]$MsiPath,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if ($Name -notmatch '^[A-Za-z0-9_]+$') {
        throw "Invalid MSI property name."
    }
    $rows = @(Get-MsiRows $MsiPath `
        "SELECT ``Value`` FROM ``Property`` WHERE ``Property`` = '$Name'" 1)
    if ($rows.Count -ne 1) {
        throw "MSI property is missing or duplicated: $Name"
    }
    return $rows[0].Values[0]
}

function Assert-MsiRow {
    param(
        [Parameter(Mandatory = $true)][object[]]$Rows,
        [Parameter(Mandatory = $true)][string[]]$Expected,
        [Parameter(Mandatory = $true)][string]$Message
    )
    foreach ($row in $Rows) {
        $matches = $true
        for ($index = 0; $index -lt $Expected.Count; $index++) {
            if ($row.Values[$index] -ne $Expected[$index]) {
                $matches = $false
                break
            }
        }
        if ($matches) {
            return
        }
    }
    throw $Message
}

function Assert-MsiUi {
    param([Parameter(Mandatory = $true)][string]$MsiPath)
    $properties = @{
        ProductVersion = Get-MsiProperty $MsiPath "ProductVersion"
        UpgradeCode = Get-MsiProperty $MsiPath "UpgradeCode"
        WIXUI_INSTALLDIR = Get-MsiProperty $MsiPath "WIXUI_INSTALLDIR"
        ALLUSERS = Get-MsiProperty $MsiPath "ALLUSERS"
        SecureCustomProperties = Get-MsiProperty $MsiPath "SecureCustomProperties"
    }
    if ($properties.ProductVersion -ne $Version) {
        throw "MSI ProductVersion does not match the requested release version."
    }
    if ($properties.UpgradeCode -ne $ExpectedUpgradeCode) {
        throw "MSI UpgradeCode changed unexpectedly."
    }
    if ($properties.WIXUI_INSTALLDIR -ne "INSTALLFOLDER") {
        throw "MSI install-directory UI is not bound to INSTALLFOLDER."
    }
    if ($properties.ALLUSERS -ne "1") {
        throw "MSI package is not configured for per-machine installation."
    }
    foreach ($required in @("AUTODY_INTERACTIVE_USER_SID", "AUTODY_INTERACTIVE_LOCALAPPDATA")) {
        if ($required -notin ($properties.SecureCustomProperties -split ';')) {
            throw "MSI secure interactive-user property is missing: $required"
        }
    }

    $directories = @(Get-MsiRows $MsiPath `
        'SELECT `Directory`,`Directory_Parent`,`DefaultDir` FROM `Directory`' 3)
    Assert-MsiRow $directories @("ProgramsFolder", "LocalAppDataFolder", "Programs") `
        "MSI ProgramsFolder is not under LocalAppDataFolder."
    Assert-MsiRow $directories @("INSTALLFOLDER", "ProgramsFolder", "AutoDy") `
        "MSI default install folder is incorrect."
    Assert-MsiRow $directories @("AUTODYDATAROOT", "LocalAppDataFolder", "AutoDy") `
        "MSI runtime data folder is not separate from INSTALLFOLDER."

    $components = @(Get-MsiRows $MsiPath `
        'SELECT `Component`,`Directory_`,`Attributes` FROM `Component`' 3)
    $dataComponent = @($components | Where-Object { $_.Values[0] -eq "DataRootComponent" })
    if ($dataComponent.Count -ne 1 -or $dataComponent[0].Values[1] -ne "AUTODYDATAROOT") {
        throw "MSI initial config component is not bound to AUTODYDATAROOT."
    }
    $files = @(Get-MsiRows $MsiPath `
        'SELECT `File`,`Component_`,`FileName`,`Attributes` FROM `File`' 4)
    $configFiles = @($files | Where-Object {
        $encodedConfigName = [string]$_.Values[2]
        $longConfigName = $encodedConfigName.Substring(
            $encodedConfigName.LastIndexOf('|') + 1
        )
        $_.Values[0] -eq "InitialConfigFile" -and
        $_.Values[1] -eq "DataRootComponent" -and
        $longConfigName -eq "config.yaml"
    })
    if ($configFiles.Count -ne 1) {
        throw "MSI does not seed config.yaml in the dedicated data-root component."
    }

    $customActions = @(Get-MsiRows $MsiPath `
        'SELECT `Action`,`Type`,`Source`,`Target` FROM `CustomAction`' 4)
    $repairData = @($customActions | Where-Object {
        $_.Values[0] -eq "SetRepairInstalledAutoDyTasksData" -and
        $_.Values[3] -like '* -B -m autody.cli repair-scheduler*' -and
        $_.Values[3] -like '*--data-root*' -and
        $_.Values[3] -like '*--task-user-id*' -and
        $_.Values[3] -notlike '*--if-config-exists*'
    })
    $repair = @($customActions | Where-Object {
        $_.Values[0] -eq "RepairInstalledAutoDyTasks" -and
        $_.Values[3] -eq "[CustomActionData]"
    })
    if ($repairData.Count -ne 1 -or $repair.Count -ne 1) {
        throw "MSI Scheduler repair CustomActionData contract is invalid."
    }
    $rollbackData = @($customActions | Where-Object {
        $_.Values[0] -eq "SetRollbackFreshInstallAutoDyTasksData" -and
        $_.Values[3] -like '*scripts\remove-task.ps1*'
    })
    $rollback = @($customActions | Where-Object {
        $_.Values[0] -eq "RollbackFreshInstallAutoDyTasks" -and
        $_.Values[3] -eq "[CustomActionData]"
    })
    if ($rollbackData.Count -ne 1 -or $rollback.Count -ne 1) {
        throw "MSI Scheduler fresh-install rollback CustomActionData contract is invalid."
    }
    $removeData = @($customActions | Where-Object {
        $_.Values[0] -eq "SetRemoveInstalledAutoDyTasksData" -and
        $_.Values[3] -like '*scripts\remove-task.ps1*'
    })
    $remove = @($customActions | Where-Object {
        $_.Values[0] -eq "RemoveInstalledAutoDyTasks" -and
        $_.Values[3] -eq "[CustomActionData]"
    })
    if ($removeData.Count -ne 1 -or $remove.Count -ne 1) {
        throw "MSI Scheduler uninstall CustomActionData contract is invalid."
    }

    $executeRows = @(Get-MsiRows $MsiPath `
        'SELECT `Action`,`Condition`,`Sequence` FROM `InstallExecuteSequence`' 3)
    $rollbackDataRow = @($executeRows | Where-Object {
        $_.Values[0] -eq "SetRollbackFreshInstallAutoDyTasksData"
    })
    $rollbackRow = @($executeRows | Where-Object {
        $_.Values[0] -eq "RollbackFreshInstallAutoDyTasks"
    })
    $repairRow = @($executeRows | Where-Object {
        $_.Values[0] -eq "RepairInstalledAutoDyTasks"
    })
    $expectedRollbackCondition = 'NOT Installed AND NOT WIX_UPGRADE_DETECTED AND NOT REMOVE~="ALL"'
    if ($rollbackDataRow.Count -ne 1 -or $rollbackRow.Count -ne 1 -or $repairRow.Count -ne 1 -or
        $rollbackDataRow[0].Values[1] -ne $expectedRollbackCondition -or
        $rollbackRow[0].Values[1] -ne $expectedRollbackCondition -or
        [int]$rollbackDataRow[0].Values[2] -ge [int]$rollbackRow[0].Values[2] -or
        [int]$rollbackRow[0].Values[2] -ge [int]$repairRow[0].Values[2]) {
        throw "MSI Scheduler fresh-install rollback contract is invalid."
    }
    $removeDataRow = @($executeRows | Where-Object { $_.Values[0] -eq "SetRemoveInstalledAutoDyTasksData" })
    $removeRow = @($executeRows | Where-Object { $_.Values[0] -eq "RemoveInstalledAutoDyTasks" })
    $removeFilesRow = @($executeRows | Where-Object { $_.Values[0] -eq "RemoveFiles" })
    $expectedRemoveCondition = 'REMOVE~="ALL" AND NOT UPGRADINGPRODUCTCODE'
    if ($removeDataRow.Count -ne 1 -or $removeRow.Count -ne 1 -or $removeFilesRow.Count -ne 1 -or
        $removeDataRow[0].Values[1] -ne $expectedRemoveCondition -or
        $removeRow[0].Values[1] -ne $expectedRemoveCondition -or
        [int]$removeDataRow[0].Values[2] -ge [int]$removeRow[0].Values[2] -or
        [int]$removeRow[0].Values[2] -ge [int]$removeFilesRow[0].Values[2]) {
        throw "MSI Scheduler uninstall actions are not sequenced before RemoveFiles."
    }

    $registry = @(Get-MsiRows $MsiPath `
        'SELECT `Root`,`Key`,`Name`,`Value` FROM `Registry`' 4)
    foreach ($name in @("InstallFolder", "DataRoot", "InstalledVersion")) {
        if (-not ($registry | Where-Object {
            $_.Values[0] -eq "3" -and
            $_.Values[1] -eq "[AUTODY_INTERACTIVE_USER_SID]\Software\AutoDy" -and
            $_.Values[2] -eq $name
        })) {
            throw "MSI original-user registry contract is missing: $name"
        }
    }

    $dialogs = @(Get-MsiRows $MsiPath `
        'SELECT `Dialog`,`Control_First`,`Control_Default`,`Control_Cancel` FROM `Dialog`' 4)
    foreach ($dialog in @(
        "WelcomeDlg",
        "LicenseAgreementDlg",
        "InstallDirDlg",
        "BrowseDlg",
        "VerifyReadyDlg",
        "ProgressDlg",
        "ExitDialog",
        "CancelDlg"
    )) {
        if (-not ($dialogs | Where-Object { $_.Values[0] -eq $dialog })) {
            throw "MSI wizard dialog is missing: $dialog"
        }
    }

    $events = @(Get-MsiRows $MsiPath `
        'SELECT `Dialog_`,`Control_`,`Event`,`Argument`,`Condition`,`Ordering` FROM `ControlEvent`' 6)
    Assert-MsiRow $events @("WelcomeDlg", "Next", "NewDialog", "LicenseAgreementDlg") `
        "MSI welcome dialog does not advance to the license dialog."
    Assert-MsiRow $events @("LicenseAgreementDlg", "Next", "NewDialog", "InstallDirDlg") `
        "MSI license dialog does not advance to the install-directory dialog."
    Assert-MsiRow $events @("InstallDirDlg", "ChangeFolder", "SpawnDialog", "BrowseDlg") `
        "MSI install-directory dialog does not expose Browse."
    Assert-MsiRow $events @("InstallDirDlg", "Next", "SetTargetPath", "[WIXUI_INSTALLDIR]") `
        "MSI install-directory dialog does not apply the selected path."
    Assert-MsiRow $events @("InstallDirDlg", "Next", "NewDialog", "VerifyReadyDlg") `
        "MSI install-directory dialog does not advance to Ready to Install."
    Assert-MsiRow $events @("VerifyReadyDlg", "Install", "EndDialog", "Return") `
        "MSI Ready to Install dialog does not start installation."
    Assert-MsiRow $events @("ExitDialog", "Finish", "EndDialog", "Return") `
        "MSI completion dialog does not finish cleanly."
    foreach ($dialog in @(
        "WelcomeDlg",
        "LicenseAgreementDlg",
        "InstallDirDlg",
        "VerifyReadyDlg",
        "ProgressDlg"
    )) {
        Assert-MsiRow $events @($dialog, "Cancel", "SpawnDialog", "CancelDlg") `
            "MSI cancel confirmation is not reachable from $dialog."
    }
    Assert-MsiRow $events @("CancelDlg", "Yes", "EndDialog", "Exit") `
        "MSI cancel confirmation does not exit installation."
    Assert-MsiRow $events @("CancelDlg", "No", "EndDialog", "Return") `
        "MSI cancel confirmation cannot return to the wizard."
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
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedInstallRoot
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Expected installer shortcut is missing."
    }
    $shell = New-Object -ComObject WScript.Shell
    try {
        $shortcut = $shell.CreateShortcut($Path)
        if ([IO.Path]::GetFileName($shortcut.TargetPath) -ne "wscript.exe" -or
            $shortcut.Arguments -ne "`"$ExpectedInstallRoot\scripts\start-dashboard.vbs`"" -or
            [IO.Path]::GetFullPath($shortcut.WorkingDirectory).TrimEnd('\') -ne
                [IO.Path]::GetFullPath($ExpectedInstallRoot).TrimEnd('\')) {
            throw "Installer shortcut target or arguments are incorrect."
        }
    } finally {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
    }
}

function Assert-UninstallShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedProductCode
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Expected MSI uninstall shortcut is missing."
    }
    $shell = New-Object -ComObject WScript.Shell
    try {
        $shortcut = $shell.CreateShortcut($Path)
        if ([IO.Path]::GetFileName($shortcut.TargetPath) -ne "msiexec.exe" -or
            $shortcut.Arguments -ne "/x $ExpectedProductCode") {
            throw "MSI uninstall shortcut target or arguments are incorrect."
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

function Assert-InstalledPayload {
    param([Parameter(Mandatory = $true)][string]$ExpectedInstallRoot)
    foreach ($relative in @(
        "runtime\python\python.exe",
        "runtime\ms-playwright",
        "scripts\start-dashboard.vbs",
        "scripts\autody-tray.ps1",
        "assets\icons\autody.ico"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $ExpectedInstallRoot $relative))) {
            throw "Installed payload is incomplete."
        }
    }
}

function Assert-ScheduledTasksPresent {
    foreach ($taskName in $ScheduledTaskNames) {
        if (-not (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
            throw "Expected installed scheduled task is missing: $taskName"
        }
    }
}

function Assert-ScheduledTasksAbsent {
    foreach ($taskName in $ScheduledTaskNames) {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            throw "Scheduled task remains after MSI uninstall: $taskName"
        }
    }
}

if (-not (Test-Path -LiteralPath $Msi -PathType Leaf)) {
    throw "MSI artifact is missing."
}
if (-not (Test-Path -LiteralPath $PreviousMsi -PathType Leaf)) {
    throw "Previous-version MSI baseline is missing."
}

Reset-OutputDirectory $Work
$ProductCode = Get-MsiProperty $Msi "ProductCode"
$PreviousProductCode = Get-MsiProperty $PreviousMsi "ProductCode"
$PreviousUpgradeCode = Get-MsiProperty $PreviousMsi "UpgradeCode"
if ($ProductCode -eq $PreviousProductCode) {
    throw "Current and previous MSI packages must use different ProductCodes."
}
if ($PreviousUpgradeCode -ne $ExpectedUpgradeCode) {
    throw "Previous MSI UpgradeCode does not match the current release."
}
if ((Get-ProductState $ProductCode) -ne -1) {
    throw "The release MSI is already installed; refusing to overwrite an existing installation."
}
$previousInitiallyInstalled = (Get-ProductState $PreviousProductCode) -eq 5

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
            sha256 = Get-ReleaseFileSha256 -Path $path
            last_write_time = $item.LastWriteTime
        }
    } else {
        $shortcutBackups[$path] = [ordered]@{ existed = $false }
    }
}

$installedDataBefore = Get-DataSnapshot $InstalledDataRoot
$repositoryDataBefore = Get-DataSnapshot $RepositoryDataRoot
$stages = [ordered]@{
    ui_wizard = "not_run"
    cancel_path = "not_run"
    fresh_install = "not_run"
    custom_install = "not_run"
    repair = "not_run"
    upgrade = "not_run"
    uninstall = "not_run"
    shortcuts = "not_run"
    data_preservation = "not_run"
    shortcut_restoration = "not_run"
    prior_installation_restoration = "not_run"
}
$failure = $null

try {
    Assert-MsiUi $Msi
    $stages.ui_wizard = "passed"
    $stages.cancel_path = "passed"

    if ((Get-ProductState $PreviousProductCode) -eq 5) {
        Invoke-MsiChecked "Remove baseline before fresh-install checks" `
            "/x $PreviousProductCode" (Join-Path $Work "baseline-precheck-uninstall.log")
    }

    Invoke-MsiChecked "Fresh install" "/i `"$Msi`"" (Join-Path $Work "install.log")
    if ((Get-ProductState $ProductCode) -ne 5) {
        throw "MSI product did not reach the installed state."
    }
    Assert-InstalledPayload $InstallRoot
    Assert-ScheduledTasksPresent
    $stages.fresh_install = "passed"

    Assert-Shortcut $DesktopShortcut $InstallRoot
    Assert-Shortcut $StartMenuShortcut $InstallRoot
    Assert-UninstallShortcut $UninstallShortcut $ProductCode
    $stages.shortcuts = "passed"

    Invoke-MsiChecked "Repair" "/fa $ProductCode" (Join-Path $Work "repair.log")
    if ((Get-ProductState $ProductCode) -ne 5) {
        throw "MSI product did not remain installed after repair."
    }
    Assert-InstalledPayload $InstallRoot
    Assert-ScheduledTasksPresent
    Assert-Shortcut $DesktopShortcut $InstallRoot
    Assert-Shortcut $StartMenuShortcut $InstallRoot
    Assert-UninstallShortcut $UninstallShortcut $ProductCode
    $stages.repair = "passed"

    Invoke-MsiChecked "Uninstall after default install" `
        "/x $ProductCode" (Join-Path $Work "default-uninstall.log")
    if ((Get-ProductState $ProductCode) -ne -1) {
        throw "MSI product remains registered after uninstall."
    }
    Assert-ScheduledTasksAbsent
    if (Test-Path -LiteralPath $InstallRoot) {
        $remainingInstalledFiles = @(Get-ChildItem -LiteralPath $InstallRoot -Recurse -Force -File)
        if ($remainingInstalledFiles.Count -gt 0) {
            throw "Installed program files remain after uninstall."
        }
    }

    Invoke-MsiChecked "Custom-path install" `
        "/i `"$Msi`" INSTALLFOLDER=`"$CustomInstallRoot`"" `
        (Join-Path $Work "custom-install.log")
    if ((Get-ProductState $ProductCode) -ne 5) {
        throw "Custom-path MSI install did not reach the installed state."
    }
    Assert-InstalledPayload $CustomInstallRoot
    Assert-ScheduledTasksPresent
    Assert-Shortcut $DesktopShortcut $CustomInstallRoot
    Assert-Shortcut $StartMenuShortcut $CustomInstallRoot
    Assert-UninstallShortcut $UninstallShortcut $ProductCode
    if (Test-Path -LiteralPath (Join-Path $InstallRoot "runtime\python\python.exe") -PathType Leaf) {
        throw "Custom-path MSI install unexpectedly wrote payload to the default install folder."
    }
    $stages.custom_install = "passed"

    Invoke-MsiChecked "Uninstall after custom-path install" `
        "/x $ProductCode" (Join-Path $Work "custom-uninstall.log")
    if ((Get-ProductState $ProductCode) -ne -1) {
        throw "Custom-path MSI product remains registered after uninstall."
    }
    Assert-ScheduledTasksAbsent
    if (Test-Path -LiteralPath $CustomInstallRoot) {
        $remainingCustomFiles = @(Get-ChildItem -LiteralPath $CustomInstallRoot -Recurse -Force -File)
        if ($remainingCustomFiles.Count -gt 0) {
            $sample = @($remainingCustomFiles | Select-Object -First 5 | ForEach-Object {
                $_.FullName.Substring($CustomInstallRoot.Length).TrimStart('\')
            })
            throw "Custom install path still contains $($remainingCustomFiles.Count) program files after uninstall: $($sample -join ', ')."
        }
    }

    Invoke-MsiChecked "Install previous-version baseline" `
        "/i `"$PreviousMsi`"" (Join-Path $Work "baseline-install.log")
    if ((Get-ProductState $PreviousProductCode) -ne 5) {
        throw "Previous-version MSI baseline did not reach the installed state."
    }
    # Windows Installer does not discover a per-user related product from a
    # per-machine package. The supported scope migration therefore removes the
    # legacy product registration/program payload first; its permanent DataRoot
    # remains in place for the new per-machine install.
    Invoke-MsiChecked "Remove previous per-user baseline for scope migration" `
        "/x $PreviousProductCode" (Join-Path $Work "upgrade-baseline-uninstall.log")
    if ((Get-ProductState $PreviousProductCode) -ne -1) {
        throw "Previous per-user MSI remains registered after migration uninstall."
    }
    Invoke-MsiChecked "Install current per-machine package after scope migration" `
        "/i `"$Msi`"" (Join-Path $Work "upgrade.log")
    if ((Get-ProductState $ProductCode) -ne 5) {
        throw "MSI scope migration did not install the current product."
    }
    Assert-InstalledPayload $InstallRoot
    Assert-ScheduledTasksPresent
    Assert-Shortcut $DesktopShortcut $InstallRoot
    Assert-Shortcut $StartMenuShortcut $InstallRoot
    Assert-UninstallShortcut $UninstallShortcut $ProductCode
    $stages.upgrade = "passed"

    Invoke-MsiChecked "Uninstall after upgrade" `
        "/x $ProductCode" (Join-Path $Work "upgrade-uninstall.log")
    if ((Get-ProductState $ProductCode) -ne -1) {
        throw "Upgraded MSI product remains registered after uninstall."
    }
    Assert-ScheduledTasksAbsent
    $stages.uninstall = "passed"
} catch {
    $failure = Get-SanitizedText $_.Exception.Message
} finally {
    if ((Get-ProductState $ProductCode) -ne -1) {
        try {
            Invoke-MsiChecked "Cleanup current-version uninstall" `
                "/x $ProductCode" (Join-Path $Work "cleanup-current-uninstall.log")
        } catch {
            if ($failure -eq $null) {
                $failure = Get-SanitizedText $_.Exception.Message
            }
        }
    }
    $previousState = Get-ProductState $PreviousProductCode
    if ($previousInitiallyInstalled -and $previousState -ne 5) {
        try {
            Invoke-MsiChecked "Restore pre-existing previous-version install" `
                "/i `"$PreviousMsi`"" (Join-Path $Work "restore-baseline-install.log")
        } catch {
            if ($failure -eq $null) {
                $failure = Get-SanitizedText $_.Exception.Message
            }
        }
    } elseif (-not $previousInitiallyInstalled -and $previousState -ne -1) {
        try {
            Invoke-MsiChecked "Cleanup previous-version baseline" `
                "/x $PreviousProductCode" (Join-Path $Work "cleanup-baseline-uninstall.log")
        } catch {
            if ($failure -eq $null) {
                $failure = Get-SanitizedText $_.Exception.Message
            }
        }
    }
    $expectedPreviousState = if ($previousInitiallyInstalled) { 5 } else { -1 }
    if ((Get-ProductState $PreviousProductCode) -eq $expectedPreviousState -and
        (Get-ProductState $ProductCode) -eq -1) {
        $stages.prior_installation_restoration = "passed"
    } else {
        $stages.prior_installation_restoration = "failed"
        if ($failure -eq $null) {
            $failure = "Pre-existing MSI installation state was not restored."
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
            if ((Get-ReleaseFileSha256 -Path $path) -ne $backup.sha256) {
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
    previous_version = $PreviousVersion
    generated_at = [DateTimeOffset]::Now.ToString("o")
    passed = ($failure -eq $null)
    product_state = [ordered]@{
        current = Get-ProductState $ProductCode
        previous = Get-ProductState $PreviousProductCode
        previous_initially_installed = $previousInitiallyInstalled
    }
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
    "- UI wizard tables: $($stages.ui_wizard)",
    "- Cancel path tables: $($stages.cancel_path)",
    "- Fresh install: $($stages.fresh_install)",
    "- Custom install path: $($stages.custom_install)",
    "- Repair: $($stages.repair)",
    "- Upgrade: $($stages.upgrade)",
    "- Uninstall: $($stages.uninstall)",
    "- Shortcuts: $($stages.shortcuts)",
    "- Data preservation: $($stages.data_preservation)",
    "- Pre-existing shortcut restoration: $($stages.shortcut_restoration)",
    "- Pre-existing installation restoration: $($stages.prior_installation_restoration)",
    "- Final current product state: $($report.product_state.current)",
    "- Final previous product state: $($report.product_state.previous)"
)
$markdown -join "`r`n" | Set-Content -LiteralPath $ReportMarkdown -Encoding utf8

$resolvedWork = Assert-OutputChild $Work
if ($report.passed -and (Test-Path -LiteralPath $resolvedWork)) {
    Remove-Item -LiteralPath $resolvedWork -Recurse -Force
}

if (-not $report.passed) {
    throw "MSI lifecycle verification failed. See output reports."
}

Write-Host "MSI lifecycle verification passed."
Write-Host "Wizard, cancel path, fresh/default and custom installs, repair, upgrade, uninstall, privacy-safe data preservation, and restoration passed."
