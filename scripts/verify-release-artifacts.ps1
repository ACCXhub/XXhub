param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = "1.4.0",
    [string]$ArtifactDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Output = Join-Path $Root "output"
$ArtifactRoot = if ([string]::IsNullOrWhiteSpace($ArtifactDirectory)) {
    $Output
} else {
    (Resolve-Path -LiteralPath $ArtifactDirectory).Path
}
$Msi = Join-Path $ArtifactRoot "AutoDy-$Version-x64.msi"
$Portable = Join-Path $ArtifactRoot "AutoDy-Windows-Portable.zip"
$Module = Join-Path $Output "AutoDy-Test-Center.autody-module.zip"
$ReportJson = Join-Path $Output "release-privacy-report.json"
$ReportMarkdown = Join-Path $Output "release-privacy-report.md"
$Work = Join-Path $Output "release-privacy-work"
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

foreach ($artifact in @($Msi, $Portable, $Module)) {
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
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $item.FullName).Hash.ToLowerInvariant()
    }
}

$byteMapping = [Text.Encoding]::GetEncoding(28591)
$userProfile = [Environment]::GetFolderPath("UserProfile")
$privateBytePatterns = @(
    $byteMapping.GetString([Text.Encoding]::UTF8.GetBytes($Root)),
    $byteMapping.GetString([Text.Encoding]::Unicode.GetBytes($Root)),
    $byteMapping.GetString([Text.Encoding]::UTF8.GetBytes($userProfile)),
    $byteMapping.GetString([Text.Encoding]::Unicode.GetBytes($userProfile))
)

function Test-ExtractedTree {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ArtifactLabel
    )
    $files = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -File)
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($Path.Length).TrimStart('\')
        if (Test-ForbiddenEntryPath $relative) {
            [void]$findings.Add([ordered]@{
                category = "forbidden_entry"
                file = "$ArtifactLabel/$($relative.Replace('\', '/'))"
            })
        }
        if (Test-FileContainsMappedPattern $file.FullName $privateBytePatterns $byteMapping) {
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
        if (Test-FileContainsMappedPattern $Msi $privateBytePatterns $byteMapping) {
            [void]$findings.Add([ordered]@{
                category = "private_absolute_path"
                file = Split-Path -Leaf $Msi
            })
        }

        $installer = $null
        $database = $null
        try {
            $installer = New-Object -ComObject WindowsInstaller.Installer
            $database = $installer.OpenDatabase($Msi, 0)
            $fileRows = @(Get-MsiRows $database 'SELECT `File`,`FileName` FROM `File`' 2)
            $shortcutRows = @(Get-MsiRows $database 'SELECT `Shortcut`,`Name`,`Target`,`Arguments` FROM `Shortcut`' 4)
            $customActionTable = @(Get-MsiRows $database "SELECT ``Name`` FROM ``_Tables`` WHERE ``Name`` = 'CustomAction'" 1)
            $customActionRows = @()
            if ($customActionTable.Count -gt 0) {
                $customActionRows = @(Get-MsiRows $database 'SELECT `Action`,`Type`,`Source`,`Target` FROM `CustomAction`' 4)
            }
            $counts.msi_files = $fileRows.Count
            $counts.msi_shortcuts = $shortcutRows.Count
            $counts.msi_custom_actions = $customActionRows.Count

            foreach ($row in $fileRows) {
                if (Test-ForbiddenEntryPath $row.Values[1]) {
                    [void]$findings.Add([ordered]@{
                        category = "forbidden_msi_file"
                        file = "MSI File table"
                    })
                }
            }
            if ($shortcutRows.Count -ne 2) {
                [void]$findings.Add([ordered]@{
                    category = "unexpected_shortcut_count"
                    file = "MSI Shortcut table"
                })
            }
            foreach ($row in $shortcutRows) {
                if ($row.Values[1] -notlike "*AutoDy Management" -or
                    $row.Values[2] -ne "[SystemFolder]wscript.exe" -or
                    $row.Values[3] -ne '"[INSTALLFOLDER]scripts\start-dashboard.vbs"') {
                    [void]$findings.Add([ordered]@{
                        category = "unsafe_shortcut"
                        file = "MSI Shortcut table"
                    })
                }
            }
            if ($customActionRows.Count -ne 0) {
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
            $checks.msi_administrative_extract = "passed"
        }
    }

    if (Test-Path -LiteralPath $Portable -PathType Leaf) {
        New-Item -ItemType Directory -Force -Path $PortableExtract | Out-Null
        Expand-Archive -LiteralPath $Portable -DestinationPath $PortableExtract -Force
        $counts.portable_files = Test-ExtractedTree $PortableExtract "portable"
        $checks.portable_zip = "passed"
    }

    if (Test-Path -LiteralPath $Module -PathType Leaf) {
        New-Item -ItemType Directory -Force -Path $ModuleExtract | Out-Null
        Expand-Archive -LiteralPath $Module -DestinationPath $ModuleExtract -Force
        $counts.module_files = Test-ExtractedTree $ModuleExtract "module"
        $checks.module_zip = "passed"
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
    throw "Release artifact privacy verification failed. See output reports."
}

Write-Host "Release artifact privacy verification passed."
Write-Host "JSON report: release-privacy-report.json"
Write-Host "Markdown report: release-privacy-report.md"
