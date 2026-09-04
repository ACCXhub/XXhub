param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = "1.5.4",
    [string]$ArtifactDirectory,
    [string]$ReportDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot 'release-common.ps1')
$Output = Join-Path $Root "output"
$CanonicalRelease = Get-CanonicalReleaseDirectory -Root $Root -Version $Version
$ArtifactRoot = if ([string]::IsNullOrWhiteSpace($ArtifactDirectory)) {
    $CanonicalRelease
} else {
    (Resolve-Path -LiteralPath $ArtifactDirectory).Path
}
$ReportRoot = if ([string]::IsNullOrWhiteSpace($ReportDirectory)) {
    $ArtifactRoot
} else {
    [IO.Path]::GetFullPath($ReportDirectory)
}
if ($ArtifactRoot -match '(?i)\\obj(\\|$)|\\Debug(\\|$)') {
    throw 'Refusing to verify an intermediate or Debug artifact directory for release.'
}
New-Item -ItemType Directory -Force -Path $ReportRoot | Out-Null
$Msi = Join-Path $ArtifactRoot "AutoDy-$Version-x64.msi"
$Portable = Join-Path $ArtifactRoot "AutoDy-Windows-Portable-$Version.zip"
$MsiChecksum = "$Msi.sha256"
$PortableChecksum = "$Portable.sha256"
$Setup = Join-Path $ArtifactRoot "AutoDy-Setup-$Version.exe"
$SetupChecksum = "$Setup.sha256"
$ReportJson = Join-Path $ReportRoot "release-privacy-report.json"
$ReportMarkdown = Join-Path $ReportRoot "release-privacy-report.md"
$Work = Join-Path $Output "work\release-privacy-v$Version"
$AdminExtract = Join-Path $Work "msi-admin"
$PortableExtract = Join-Path $Work "portable"
$ModuleExtract = Join-Path $Work "module"
$AdminLog = Join-Path $Work "msiexec-admin.log"

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

function Test-FileContainsMappedPattern {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$Patterns,
        [Parameter(Mandatory = $true)][Text.Encoding]$ByteMapping
    )
    $maxPatternLength = ($Patterns | Measure-Object -Property Length -Maximum).Maximum
    $buffer = New-Object byte[] (1024 * 1024)
    $tail = New-Object byte[] 0
    $stream = [IO.File]::OpenRead($Path)
    try {
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $window = New-Object byte[] ($tail.Length + $read)
            if ($tail.Length -gt 0) {
                [Array]::Copy($tail, 0, $window, 0, $tail.Length)
            }
            [Array]::Copy($buffer, 0, $window, $tail.Length, $read)
            $mappedChunk = $ByteMapping.GetString($window)
            foreach ($pattern in $Patterns) {
                if ($mappedChunk.Contains($pattern)) {
                    return $true
                }
            }
            $tailLength = [Math]::Min($maxPatternLength - 1, $window.Length)
            $tail = New-Object byte[] $tailLength
            [Array]::Copy($window, $window.Length - $tailLength, $tail, 0, $tailLength)
        }
        return $false
    } finally {
        $stream.Dispose()
    }
}

function Test-ForbiddenEntryPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)
    $normalized = $RelativePath.Replace('\', '/').Trim('/')
    $segments = @($normalized.Split('/') | ForEach-Object { $_.ToLowerInvariant() })
    $forbiddenSegments = @(
        ".git", ".venv", "node_modules", "data", "test", "tests", "fixture",
        "fixtures", "screenshot", "screenshots", "browser-profile",
        "account-profile", "account-profiles", "account-avatar", "avatar-cache",
        "logs", "backups", "__pycache__"
    )
    if ($segments | Where-Object { $_ -in $forbiddenSegments }) {
        return $true
    }
    $leaf = if ($segments.Count -gt 0) { $segments[-1] } else { "" }
    if ($leaf -in @("config.yaml", "messages.txt", "cookies", "cookies.json")) {
        return $true
    }
    return $leaf.EndsWith(".map") -or $leaf.EndsWith(".pyc")
}

function Get-MsiRows {
    param(
        [Parameter(Mandatory = $true)]$Database,
        [Parameter(Mandatory = $true)][string]$Query,
        [Parameter(Mandatory = $true)][int]$FieldCount
    )
    $rows = New-Object System.Collections.ArrayList
    $view = $Database.OpenView($Query)
    try {
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
        [void]$view.Close()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($view)
    }
    return $rows.ToArray()
}

function Get-SanitizedText {
    param([Parameter(Mandatory = $true)][string]$Text)
    $userProfile = [Environment]::GetFolderPath("UserProfile")
    return $Text.Replace($Root, "<repo>").Replace($Output, "<output>").Replace($userProfile, "<profile>")
}

$findings = New-Object System.Collections.ArrayList
$checks = [ordered]@{
    msi_tables = "not_run"
    msi_embedded_cabs = "not_run"
    checksums = "not_run"
    msi_administrative_extract = "not_run"
    portable_zip = "not_run"
    module_zip = "not_run"
    private_path_scan = "not_run"
}
$counts = [ordered]@{
    msi_files = 0
    msi_shortcuts = 0
    msi_custom_actions = 0
    administrative_files = 0
    portable_files = 0
    module_files = 0
}
$artifacts = @()
$d3dCompiler = $null

foreach ($artifact in @($Msi, $MsiChecksum, $Portable, $PortableChecksum, $Setup, $SetupChecksum)) {
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
        [void]$findings.Add([ordered]@{
            category = "missing_artifact"
            file = Split-Path -Leaf $artifact
        })
        continue
    }
    $item = Get-Item -LiteralPath $artifact
    $artifacts += [ordered]@{
        file = $item.Name
        size = $item.Length
        sha256 = Get-ReleaseFileSha256 -Path $item.FullName
        zone_identifier = Test-Path -LiteralPath ($item.FullName + ':Zone.Identifier')
    }
}

if (
    (Test-Path -LiteralPath $Msi -PathType Leaf) -and
    (Test-Path -LiteralPath $MsiChecksum -PathType Leaf) -and
    (Test-Path -LiteralPath $Portable -PathType Leaf) -and
    (Test-Path -LiteralPath $PortableChecksum -PathType Leaf) -and
    (Test-Path -LiteralPath $Setup -PathType Leaf) -and
    (Test-Path -LiteralPath $SetupChecksum -PathType Leaf)
) {
    $checksumMismatch = $false
    foreach ($pair in @(@($Msi, $MsiChecksum), @($Portable, $PortableChecksum), @($Setup, $SetupChecksum))) {
        $expected = "$(Get-ReleaseFileSha256 -Path $pair[0])  $([IO.Path]::GetFileName($pair[0]))"
        if ((Get-Content -Raw -LiteralPath $pair[1]).Trim() -ne $expected) {
            $checksumMismatch = $true
            [void]$findings.Add([ordered]@{
                category = 'checksum_mismatch'
                file = [IO.Path]::GetFileName($pair[1])
            })
        }
    }
    $checks.checksums = if ($checksumMismatch) { 'failed' } else { 'passed' }
}

$byteMapping = [Text.Encoding]::GetEncoding(28591)
$userProfile = [Environment]::GetFolderPath("UserProfile")
$privateBytePatterns = @(
    $byteMapping.GetString([Text.Encoding]::UTF8.GetBytes($Root)),
    $byteMapping.GetString([Text.Encoding]::Unicode.GetBytes($Root)),
    $byteMapping.GetString([Text.Encoding]::UTF8.GetBytes($userProfile)),
    $byteMapping.GetString([Text.Encoding]::Unicode.GetBytes($userProfile))
)

function Test-PrivacyTextFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [IO.Path]::GetExtension($Path).ToLowerInvariant() -in @(
        '.cmd', '.css', '.html', '.ini', '.json', '.md', '.ps1', '.pth',
        '.py', '.txt', '.vbs', '.xml', '.yaml', '.yml'
    )
}

function Test-InitialConfigSeed {
    param(
        [Parameter(Mandatory = $true)][string]$SeedPath,
        [Parameter(Mandatory = $true)][string]$ExamplePath
    )

    $utf8 = [Text.UTF8Encoding]::new($false)
    $seed = [IO.File]::ReadAllText($SeedPath, $utf8) -replace "`r`n?", "`n"
    $example = [IO.File]::ReadAllText($ExamplePath, $utf8) -replace "`r`n?", "`n"
    return $seed -ceq $example
}

function Test-ExtractedTree {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ArtifactLabel
    )
    $files = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -File)
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($Path.Length).TrimStart('\')
        $normalizedRelative = $relative.Replace('\', '/')
        $allowedInitialConfig = (
            $ArtifactLabel -eq 'msi-admin' -and
            $normalizedRelative -ieq 'LocalApp/AutoDy/config.yaml' -and
            (Test-InitialConfigSeed -SeedPath $file.FullName -ExamplePath (Join-Path $Root 'config.example.yaml'))
        )
        if ((Test-ForbiddenEntryPath $relative) -and -not $allowedInitialConfig) {
            [void]$findings.Add([ordered]@{
                category = "forbidden_entry"
                file = "$ArtifactLabel/$($relative.Replace('\', '/'))"
            })
        }
        if ((Test-PrivacyTextFile $file.FullName) -and
            (Test-FileContainsMappedPattern $file.FullName $privateBytePatterns $byteMapping)) {
            [void]$findings.Add([ordered]@{
                category = "private_absolute_path"
                file = "$ArtifactLabel/$($relative.Replace('\', '/'))"
            })
        }
    }
    return $files.Count
}

Reset-OutputDirectory $Work
try {
    if (Test-Path -LiteralPath $Msi -PathType Leaf) {

        $installer = $null
        $database = $null
        try {
            $installer = New-Object -ComObject WindowsInstaller.Installer
            $database = $installer.OpenDatabase($Msi, 0)
            $fileRows = @(Get-MsiRows $database 'SELECT `File`,`FileName` FROM `File`' 2)
            $productRows = @(Get-MsiRows $database "SELECT ``Value`` FROM ``Property`` WHERE ``Property`` = 'ProductVersion'" 1)
            $mediaRows = @(Get-MsiRows $database 'SELECT `DiskId`,`LastSequence`,`Cabinet` FROM `Media`' 3)
            $streamRows = @(Get-MsiRows $database 'SELECT `Name` FROM `_Streams`' 1)
            $shortcutRows = @(Get-MsiRows $database 'SELECT `Shortcut`,`Name`,`Target`,`Arguments` FROM `Shortcut`' 4)
            $customActionTable = @(Get-MsiRows $database "SELECT ``Name`` FROM ``_Tables`` WHERE ``Name`` = 'CustomAction'" 1)
            $customActionRows = @()
            if ($customActionTable.Count -gt 0) {
                $customActionRows = @(Get-MsiRows $database 'SELECT `Action`,`Type`,`Source`,`Target` FROM `CustomAction`' 4)
            }
            $counts.msi_files = $fileRows.Count
            $counts.msi_shortcuts = $shortcutRows.Count
            $counts.msi_custom_actions = $customActionRows.Count

            if ($productRows.Count -ne 1 -or $productRows[0].Values[0] -ne $Version) {
                [void]$findings.Add([ordered]@{
                    category = "wrong_product_version"
                    file = "MSI Property table"
                })
            }
            $streamNames = @($streamRows | ForEach-Object { $_.Values[0] })
            foreach ($media in $mediaRows) {
                $cabinet = $media.Values[2]
                if (-not $cabinet.StartsWith('#') -or $cabinet.Substring(1) -notin $streamNames) {
                    [void]$findings.Add([ordered]@{
                        category = "missing_or_external_cab"
                        file = "MSI Media table"
                    })
                }
            }
            $checks.msi_embedded_cabs = if (
                @($findings | Where-Object { $_.category -eq 'missing_or_external_cab' }).Count -eq 0
            ) { 'passed' } else { 'failed' }

            foreach ($row in $fileRows) {
                if (Test-ForbiddenEntryPath $row.Values[1]) {
                    [void]$findings.Add([ordered]@{
                        category = "forbidden_msi_file"
                        file = "MSI File table"
                    })
                }
            }
            $expectedShortcuts = @(
                [pscustomobject]@{
                    Id = 'DesktopShortcut'
                    Target = '[SystemFolder]wscript.exe'
                    Arguments = '"[INSTALLFOLDER]scripts\start-dashboard.vbs"'
                },
                [pscustomobject]@{
                    Id = 'StartMenuShortcut'
                    Target = '[SystemFolder]wscript.exe'
                    Arguments = '"[INSTALLFOLDER]scripts\start-dashboard.vbs"'
                },
                [pscustomobject]@{
                    Id = 'UninstallShortcut'
                    Target = '[SystemFolder]msiexec.exe'
                    Arguments = '/x [ProductCode]'
                }
            )
            if ($shortcutRows.Count -ne $expectedShortcuts.Count) {
                [void]$findings.Add([ordered]@{
                    category = "unexpected_shortcut_count"
                    file = "MSI Shortcut table"
                })
            }
            foreach ($expectedShortcut in $expectedShortcuts) {
                $matches = @($shortcutRows | Where-Object {
                    $_.Values[0] -eq $expectedShortcut.Id -and
                    $_.Values[2] -eq $expectedShortcut.Target -and
                    $_.Values[3] -eq $expectedShortcut.Arguments
                })
                if ($matches.Count -ne 1) {
                    [void]$findings.Add([ordered]@{
                        category = "unsafe_shortcut"
                        file = "MSI Shortcut table"
                    })
                }
            }
            $expectedCustomActions = @(
                [pscustomobject]@{ Action = 'CaptureInteractiveUserSid'; Type = '307'; Source = 'AUTODY_INTERACTIVE_USER_SID'; Target = '[UserSID]' },
                [pscustomobject]@{ Action = 'CaptureInteractiveLocalAppData'; Type = '307'; Source = 'AUTODY_INTERACTIVE_LOCALAPPDATA'; Target = '[LocalAppDataFolder]' },
                [pscustomobject]@{ Action = 'SetAutoDyDataRoot'; Type = '51'; Source = 'AUTODYDATAROOT'; Target = '[AUTODY_INTERACTIVE_LOCALAPPDATA]AutoDy\' },
                [pscustomobject]@{ Action = 'SetExistingInstallFolder'; Type = '51'; Source = 'INSTALLFOLDER'; Target = '[AUTODY_EXISTING_INSTALLFOLDER]' },
                [pscustomobject]@{ Action = 'SetDDriveInstallFolder'; Type = '51'; Source = 'INSTALLFOLDER'; Target = 'D:\AutoDy' },
                [pscustomobject]@{ Action = 'StopExistingAutoDyTray'; Type = '1058'; Source = 'INSTALLFOLDER'; Target = '"[SystemFolder]WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "[INSTALLFOLDER]scripts\autody-tray.ps1" -StopExisting' },
                [pscustomobject]@{ Action = 'StopExistingAutoDyForRemove'; Type = '1058'; Source = 'INSTALLFOLDER'; Target = '"[SystemFolder]WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "[INSTALLFOLDER]scripts\autody-tray.ps1" -StopExisting' },
                [pscustomobject]@{ Action = 'SetRepairInstalledAutoDyTasksData'; Type = '51'; Source = 'RepairInstalledAutoDyTasks'; Target = '"[INSTALLFOLDER]runtime\python\python.exe" -B -m autody.cli repair-scheduler --config "[AUTODYDATAROOT]config.yaml" --program-root "[INSTALLFOLDER]." --data-root "[AUTODYDATAROOT]." --task-user-id "[AUTODY_INTERACTIVE_USER_SID]"' },
                [pscustomobject]@{ Action = 'SetRollbackFreshInstallAutoDyTasksData'; Type = '51'; Source = 'RollbackFreshInstallAutoDyTasks'; Target = '"[SystemFolder]WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "[INSTALLFOLDER]scripts\remove-task.ps1"' },
                [pscustomobject]@{ Action = 'RollbackFreshInstallAutoDyTasks'; Type = '3393'; Source = 'Wix4UtilCA_X64'; Target = 'WixQuietExec' },
                [pscustomobject]@{ Action = 'RepairInstalledAutoDyTasks'; Type = '3073'; Source = 'Wix4UtilCA_X64'; Target = 'WixQuietExec' },
                [pscustomobject]@{ Action = 'SetRemoveInstalledAutoDyTasksData'; Type = '51'; Source = 'RemoveInstalledAutoDyTasks'; Target = '"[SystemFolder]WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "[INSTALLFOLDER]scripts\remove-task.ps1"' },
                [pscustomobject]@{ Action = 'RemoveInstalledAutoDyTasks'; Type = '3073'; Source = 'Wix4UtilCA_X64'; Target = 'WixQuietExec' },
                [pscustomobject]@{ Action = 'Wix4RemoveFoldersEx_X64'; Type = '65'; Source = 'Wix4UtilCA_X64'; Target = 'WixRemoveFoldersEx' }
            )
            $unexpectedCustomAction = $customActionRows.Count -ne $expectedCustomActions.Count
            foreach ($expectedAction in $expectedCustomActions) {
                $matches = @($customActionRows | Where-Object {
                    $_.Values[0] -eq $expectedAction.Action -and
                    $_.Values[1] -eq $expectedAction.Type -and
                    $_.Values[2] -eq $expectedAction.Source -and
                    $_.Values[3] -eq $expectedAction.Target
                })
                if ($matches.Count -ne 1) { $unexpectedCustomAction = $true }
            }
            if ($unexpectedCustomAction) {
                [void]$findings.Add([ordered]@{
                    category = "unexpected_custom_action"
                    file = "MSI CustomAction table"
                })
            }
            $checks.msi_tables = "passed"
        } catch {
            $checks.msi_tables = "failed"
            [void]$findings.Add([ordered]@{
                category = "msi_table_inspection_failed"
                file = "MSI database"
                detail = Get-SanitizedText $_.Exception.Message
            })
        } finally {
            if ($database -ne $null) {
                [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($database)
            }
            if ($installer -ne $null) {
                [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer)
            }
        }

        New-Item -ItemType Directory -Force -Path $AdminExtract | Out-Null
        $arguments = "/a `"$Msi`" /qn TARGETDIR=`"$AdminExtract`" /l*v `"$AdminLog`""
        $process = Start-Process -FilePath "msiexec.exe" -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
        if ($process.ExitCode -ne 0) {
            $checks.msi_administrative_extract = "failed"
            [void]$findings.Add([ordered]@{
                category = "msi_administrative_extract_failed"
                file = Split-Path -Leaf $Msi
                detail = "msiexec exit code $($process.ExitCode)"
            })
        } else {
            $counts.administrative_files = Test-ExtractedTree $AdminExtract "msi-admin"
            $seedConfigs = @(Get-ChildItem -LiteralPath $AdminExtract -Recurse -File -Filter 'config.yaml')
            if (
                $seedConfigs.Count -ne 1 -or
                -not (Test-InitialConfigSeed -SeedPath $seedConfigs[0].FullName -ExamplePath (Join-Path $Root 'config.example.yaml'))
            ) {
                [void]$findings.Add([ordered]@{
                    category = "invalid_initial_config_seed"
                    file = "MSI administrative image"
                })
            }
            if ($counts.administrative_files -ne ($counts.msi_files + 1)) {
                [void]$findings.Add([ordered]@{
                    category = "administrative_payload_count_mismatch"
                    file = Split-Path -Leaf $Msi
                })
            }
            $dlls = @(Get-ChildItem -LiteralPath $AdminExtract -Recurse -File -Filter 'D3DCompiler_47.dll' |
                Where-Object { $_.FullName -like '*chromium-*\chrome-win64\D3DCompiler_47.dll' })
            if ($dlls.Count -ne 1) {
                [void]$findings.Add([ordered]@{
                    category = "d3dcompiler_payload_missing"
                    file = Split-Path -Leaf $Msi
                })
            } else {
                $d3dCompiler = [ordered]@{
                    size = $dlls[0].Length
                    sha256 = Get-ReleaseFileSha256 -Path $dlls[0].FullName
                }
            }
            $checks.msi_administrative_extract = "passed"
        }
    }

    if (Test-Path -LiteralPath $Portable -PathType Leaf) {
        New-Item -ItemType Directory -Force -Path $PortableExtract | Out-Null
        Expand-Archive -LiteralPath $Portable -DestinationPath $PortableExtract -Force
        $counts.portable_files = Test-ExtractedTree $PortableExtract "portable"
        $checks.portable_zip = "passed"
        $portableRequired = @(
            'AutoDy.cmd',
            'runtime\distribution-mode.txt',
            'runtime\python\python.exe',
            'runtime\python\Lib\site-packages\autody\web\static\index.html',
            'runtime\python\Lib\site-packages\pydantic_core\_pydantic_core.cp311-win_amd64.pyd',
            'runtime\ms-playwright',
            'message-packs\index.json',
            'scripts\autody-tray.ps1',
            'scripts\run-scheduled.ps1'
        )
        foreach ($relative in $portableRequired) {
            if (-not (Test-Path -LiteralPath (Join-Path $PortableExtract $relative))) {
                [void]$findings.Add([ordered]@{
                    category = 'portable_standalone_payload_missing'
                    file = $relative
                })
                $checks.portable_zip = 'failed'
            }
        }
        $portableMode = Join-Path $PortableExtract 'runtime\distribution-mode.txt'
        if ((Test-Path -LiteralPath $portableMode -PathType Leaf) -and
            (Get-Content -Raw -LiteralPath $portableMode).Trim() -ne 'portable') {
            [void]$findings.Add([ordered]@{
                category = 'portable_distribution_mode_invalid'
                file = 'runtime\distribution-mode.txt'
            })
            $checks.portable_zip = 'failed'
        }
        $portableChromium = @(
            Get-ChildItem -LiteralPath (Join-Path $PortableExtract 'runtime\ms-playwright') `
                -Recurse -File -Filter 'chrome.exe' -ErrorAction SilentlyContinue
        )
        if ($portableChromium.Count -lt 1) {
            [void]$findings.Add([ordered]@{
                category = 'portable_chromium_missing'
                file = 'runtime\ms-playwright'
            })
            $checks.portable_zip = 'failed'
        }
        $modules = @(Get-ChildItem -LiteralPath $PortableExtract -Recurse -File `
            -Filter 'AutoDy-Test-Center.autody-module.zip')
        if ($modules.Count -ne 1) {
            [void]$findings.Add([ordered]@{
                category = "embedded_module_missing_or_duplicated"
                file = Split-Path -Leaf $Portable
            })
            $checks.module_zip = 'failed'
        } else {
            New-Item -ItemType Directory -Force -Path $ModuleExtract | Out-Null
            Expand-Archive -LiteralPath $modules[0].FullName -DestinationPath $ModuleExtract -Force
            $counts.module_files = Test-ExtractedTree $ModuleExtract "module"
            $checks.module_zip = "passed"
        }
    }
    $checks.private_path_scan = if (
        @($findings | Where-Object { $_.category -eq "private_absolute_path" }).Count -eq 0
    ) { "passed" } else { "failed" }
} catch {
    [void]$findings.Add([ordered]@{
        category = "verification_exception"
        file = "release artifacts"
        detail = Get-SanitizedText $_.Exception.Message
    })
} finally {
    $resolvedWork = Assert-OutputChild $Work
    if (Test-Path -LiteralPath $resolvedWork) {
        Remove-Item -LiteralPath $resolvedWork -Recurse -Force
    }
}

if ($findings.Count -gt 0) {
    foreach ($name in @($checks.Keys)) {
        if ($checks[$name] -eq "not_run") {
            $checks[$name] = "failed"
        }
    }
}

$report = [ordered]@{
    schema_version = 1
    version = $Version
    generated_at = [DateTimeOffset]::Now.ToString("o")
    passed = ($findings.Count -eq 0)
    artifacts = $artifacts
    checks = $checks
    counts = $counts
    d3dcompiler_47 = $d3dCompiler
    findings = @($findings)
}
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportJson -Encoding utf8

$markdown = @(
    "# AutoDy $Version Release Privacy Verification",
    "",
    "- Result: $(if ($report.passed) { 'PASS' } else { 'FAIL' })",
    "- MSI File rows: $($counts.msi_files)",
    "- MSI Shortcut rows: $($counts.msi_shortcuts)",
    "- MSI CustomAction rows: $($counts.msi_custom_actions)",
    "- Administrative image files: $($counts.administrative_files)",
    "- Portable files: $($counts.portable_files)",
    "- Module files: $($counts.module_files)",
    "",
    "## Artifacts",
    ""
)
foreach ($artifact in $artifacts) {
    $markdown += "- $($artifact.file): $($artifact.size) bytes, SHA-256 $($artifact.sha256)"
}
$markdown += @("", "## Findings", "")
if ($findings.Count -eq 0) {
    $markdown += "- None"
} else {
    foreach ($finding in $findings) {
        $markdown += "- $($finding.category): $($finding.file)"
    }
}
$markdown -join "`r`n" | Set-Content -LiteralPath $ReportMarkdown -Encoding utf8

if (-not $report.passed) {
    Write-Host "Release artifact privacy verification failed:"
    foreach ($finding in $findings) {
        Write-Host "- [$($finding.category)] $($finding.file)"
    }
    Write-Host "JSON report: $ReportJson"
    Write-Host "Markdown report: $ReportMarkdown"
    throw "Release artifact privacy verification failed. See output reports."
}

Write-Host "Release artifact privacy verification passed."
Write-Host "JSON report: release-privacy-report.json"
Write-Host "Markdown report: release-privacy-report.md"
