param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = '1.4.2',
    [ValidatePattern('^$|^[0-9a-fA-F]{40}$')]
    [string]$Commit = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'release-common.ps1')
$ReleaseDirectory = Get-CanonicalReleaseDirectory -Root $Root -Version $Version
$Msi = Join-Path $ReleaseDirectory "AutoDy-$Version-x64.msi"
$MsiChecksum = "$Msi.sha256"
$Portable = Join-Path $ReleaseDirectory "AutoDy-Windows-Portable-$Version.zip"
$PortableChecksum = "$Portable.sha256"
$PrivacyReport = Join-Path $ReleaseDirectory 'release-privacy-report.json'
$LifecycleReport = Join-Path $ReleaseDirectory 'msi-lifecycle-report.json'
$ManifestPath = Join-Path $ReleaseDirectory 'release-manifest.json'

if (Test-Path -LiteralPath (Join-Path $Root '.git')) {
    $head = (& git -C $Root rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $head -notmatch '^[0-9a-fA-F]{40}$') {
        throw 'Unable to resolve the current source commit.'
    }
    if ([string]::IsNullOrWhiteSpace($Commit)) {
        $Commit = $head
    }
    if ($Commit -ne $head) {
        throw 'Release manifest commit does not match the current source commit.'
    }
    $trackedChanges = @(& git -C $Root status --porcelain --untracked-files=no)
    if ($LASTEXITCODE -ne 0 -or $trackedChanges.Count -ne 0) {
        throw 'Release manifests require a clean tracked working tree.'
    }
} elseif ([string]::IsNullOrWhiteSpace($Commit)) {
    throw 'A 40-character commit is required when building from an automatic source archive.'
}

$pyproject = Get-Content -Raw -LiteralPath (Join-Path $Root 'pyproject.toml')
if ($pyproject -notmatch "(?m)^version\s*=\s*`"$([regex]::Escape($Version))`"\s*$") {
    throw 'Source version does not match the requested release version.'
}

$artifactPaths = @($Msi, $MsiChecksum, $Portable, $PortableChecksum)
foreach ($path in $artifactPaths) {
    $null = Assert-CanonicalReleaseArtifact -Root $Root -Version $Version -Path $path
}
foreach ($pair in @(@($Msi, $MsiChecksum), @($Portable, $PortableChecksum))) {
    $file = $pair[0]
    $sidecar = $pair[1]
    $expected = "$(Get-ReleaseFileSha256 -Path $file)  $([IO.Path]::GetFileName($file))"
    $actual = (Get-Content -Raw -LiteralPath $sidecar).Trim()
    if ($actual -ne $expected) {
        throw "Checksum sidecar mismatch: $([IO.Path]::GetFileName($sidecar))"
    }
}

$installer = New-Object -ComObject WindowsInstaller.Installer
$database = $null
$summary = $null
try {
    $database = $installer.OpenDatabase($Msi, 0)
    $properties = [ordered]@{}
    foreach ($name in @('ProductVersion', 'ProductCode', 'UpgradeCode')) {
        $view = $database.OpenView("SELECT ``Value`` FROM ``Property`` WHERE ``Property`` = '$name'")
        [void]$view.Execute()
        $record = $view.Fetch()
        if ($null -eq $record) { throw "MSI property is missing: $name" }
        $properties[$name] = $record.StringData(1)
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($record)
        [void]$view.Close()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($view)
    }
    if ($properties.ProductVersion -ne $Version) {
        throw 'MSI ProductVersion does not match the release version.'
    }
    $mediaView = $database.OpenView('SELECT `Cabinet` FROM `Media`')
    [void]$mediaView.Execute()
    $cabinets = @()
    while ($record = $mediaView.Fetch()) {
        $cabinets += $record.StringData(1)
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($record)
    }
    [void]$mediaView.Close()
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($mediaView)
    $streamView = $database.OpenView('SELECT `Name` FROM `_Streams`')
    [void]$streamView.Execute()
    $streams = @()
    while ($record = $streamView.Fetch()) {
        $streams += $record.StringData(1)
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($record)
    }
    [void]$streamView.Close()
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($streamView)
    foreach ($cabinet in $cabinets) {
        if (-not $cabinet.StartsWith('#') -or $cabinet.Substring(1) -notin $streams) {
            throw 'MSI contains a missing or external cabinet.'
        }
    }
    $summary = $database.SummaryInformation(0)
    $summaryData = [ordered]@{
        template = $summary.Property(7)
        package_code = $summary.Property(9)
        created_at = [string]$summary.Property(12)
        last_saved_at = [string]$summary.Property(13)
        word_count = $summary.Property(15)
    }
} finally {
    if ($null -ne $summary) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($summary)
    }
    if ($null -ne $database) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($database)
    }
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer)
}

$privacy = Get-Content -Raw -LiteralPath $PrivacyReport | ConvertFrom-Json
$lifecycle = Get-Content -Raw -LiteralPath $LifecycleReport | ConvertFrom-Json
if (-not $privacy.passed) { throw 'Privacy verification did not pass.' }
if (-not $lifecycle.passed) { throw 'MSI lifecycle verification did not pass.' }

$manifest = New-ReleaseManifestData `
    -Root $Root `
    -Version $Version `
    -Commit $Commit `
    -Configuration 'Release' `
    -ArtifactPaths $artifactPaths `
    -ProductCode $properties.ProductCode `
    -UpgradeCode $properties.UpgradeCode `
    -PrivacyPassed $true `
    -LifecyclePassed $true
$manifest['package_code'] = $summaryData.package_code
$manifest['msi_summary'] = $summaryData
$manifest['embedded_cabinets'] = $cabinets
$manifest['privacy_report'] = [IO.Path]::GetFileName($PrivacyReport)
$manifest['lifecycle_report'] = [IO.Path]::GetFileName($LifecycleReport)
$json = $manifest | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText($ManifestPath, $json, (New-Object Text.UTF8Encoding($false)))
Write-Host "Release manifest: $([IO.Path]::GetFileName($ManifestPath))"
