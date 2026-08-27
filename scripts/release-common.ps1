Set-StrictMode -Version Latest
Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop

function Get-ReleaseFileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try {
        $hash = $algorithm.ComputeHash($stream)
        return [BitConverter]::ToString($hash).Replace('-', '').ToLowerInvariant()
    } finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
}

function Get-ReproducibleMsiGuid {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('product', 'package')]
        [string]$Purpose,
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^\d+\.\d+\.\d+$')]
        [string]$Version,
        [ValidatePattern('^$|^[0-9a-fA-F]{40}$')]
        [string]$IdentitySeed = ''
    )

    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $value = "AutoDy MSI $Purpose identity:$Version"
        if ($Purpose -eq 'package') {
            if ([string]::IsNullOrWhiteSpace($IdentitySeed)) {
                throw 'MSI package identity requires a source revision.'
            }
            $value += ":$($IdentitySeed.ToLowerInvariant())"
        }
        $hash = $algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($value))
        $guidBytes = New-Object byte[] 16
        [Array]::Copy($hash, $guidBytes, $guidBytes.Length)
        return (New-Object Guid (,$guidBytes)).ToString().ToUpperInvariant()
    } finally {
        $algorithm.Dispose()
    }
}

function New-ReproducibleZipFromDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$SourceDirectory,
        [Parameter(Mandatory = $true)][string]$DestinationPath
    )

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $source = [IO.Path]::GetFullPath($SourceDirectory).TrimEnd('\')
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw 'Reproducible ZIP source directory is missing.'
    }
    $destination = [IO.Path]::GetFullPath($DestinationPath)
    New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($destination)) | Out-Null
    $stream = [IO.File]::Open(
        $destination,
        [IO.FileMode]::Create,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::None
    )
    try {
        $archive = New-Object IO.Compression.ZipArchive(
            $stream,
            [IO.Compression.ZipArchiveMode]::Create,
            $false,
            [Text.Encoding]::UTF8
        )
        try {
            $timestamp = New-Object DateTimeOffset(2000, 1, 1, 0, 0, 0, [TimeSpan]::Zero)
            $files = @(Get-ChildItem -LiteralPath $source -Recurse -Force -File |
                Sort-Object { $_.FullName.Substring($source.Length + 1).Replace('\', '/') })
            foreach ($file in $files) {
                $relative = $file.FullName.Substring($source.Length + 1).Replace('\', '/')
                $entry = $archive.CreateEntry($relative, [IO.Compression.CompressionLevel]::Optimal)
                $entry.LastWriteTime = $timestamp
                $entry.ExternalAttributes = 0
                $input = [IO.File]::OpenRead($file.FullName)
                $output = $entry.Open()
                try {
                    $input.CopyTo($output)
                } finally {
                    $output.Dispose()
                    $input.Dispose()
                }
            }
        } finally {
            $archive.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Convert-ReleaseTextFilesToLf {
    param([Parameter(Mandatory = $true)][string]$Root)

    $resolvedRoot = [IO.Path]::GetFullPath($Root)
    if (-not (Test-Path -LiteralPath $resolvedRoot -PathType Container)) {
        throw 'Release text normalization root is missing.'
    }
    $extensions = @(
        '.cmd', '.css', '.html', '.js', '.json', '.md', '.ps1', '.psd1',
        '.psm1', '.py', '.toml', '.ts', '.tsx', '.txt', '.vbs', '.yaml',
        '.yml'
    )
    $names = @('LICENSE', 'SECURITY')
    $utf8 = New-Object Text.UTF8Encoding($false)
    $utf8Bom = New-Object Text.UTF8Encoding($true)
    $windowsScriptExtensions = @('.ps1', '.psd1', '.psm1')
    Get-ChildItem -LiteralPath $resolvedRoot -Recurse -Force -File | ForEach-Object {
        if ($_.Extension.ToLowerInvariant() -notin $extensions -and $_.Name -notin $names) {
            return
        }
        $text = [IO.File]::ReadAllText($_.FullName, [Text.Encoding]::UTF8)
        if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) {
            $text = $text.Substring(1)
        }
        $normalized = $text.Replace("`r`n", "`n").Replace("`r", "`n")
        $encoding = if ($_.Extension.ToLowerInvariant() -in $windowsScriptExtensions) {
            $utf8Bom
        } else {
            $utf8
        }
        [IO.File]::WriteAllText($_.FullName, $normalized, $encoding)
    }
}

function Get-CanonicalReleaseDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^\d+\.\d+\.\d+$')]
        [string]$Version
    )

    $resolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    return [IO.Path]::GetFullPath(
        (Join-Path $resolvedRoot ("output\release\v{0}" -f $Version))
    )
}

function Resolve-ReleaseWorkDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $resolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $workRoot = [IO.Path]::GetFullPath((Join-Path $resolvedRoot 'output\work')).TrimEnd('\')
    $resolvedPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (
        $resolvedPath.Equals($workRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not $resolvedPath.StartsWith($workRoot + '\', [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw 'Release work cleanup may only modify a direct child of output\\work.'
    }
    if (
        -not [IO.Path]::GetDirectoryName($resolvedPath).TrimEnd('\').Equals(
            $workRoot, [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw 'Release work cleanup may only modify a direct child of output\\work.'
    }
    return $resolvedPath
}

function Remove-ReleaseWorkDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $resolved = Resolve-ReleaseWorkDirectory -Root $Root -Path $Path
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

function Reset-ReleaseWorkDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $resolved = Resolve-ReleaseWorkDirectory -Root $Root -Path $Path
    Remove-ReleaseWorkDirectory -Root $Root -Path $resolved
    New-Item -ItemType Directory -Force -Path $resolved | Out-Null
    return $resolved
}

function Clear-SupersededReleaseWorkDirectories {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^\d+\.\d+\.\d+$')]
        [string]$Version
    )

    $resolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $workRoot = Join-Path $resolvedRoot 'output\work'
    if (-not (Test-Path -LiteralPath $workRoot -PathType Container)) {
        return
    }
    $current = @(
        'msi-stage',
        "msi-v$Version",
        "portable-v$Version",
        "msi-lifecycle-v$Version"
    )
    foreach ($directory in (Get-ChildItem -LiteralPath $workRoot -Force -Directory)) {
        $isVersionedReleaseWork = $directory.Name -match '^(msi-stage-v|msi-v|portable-v|msi-lifecycle-v)\d+\.\d+\.\d+$'
        $isCiLifecycleDiagnostic = $directory.Name -match '^ci-lifecycle-\d+$'
        if (-not $isVersionedReleaseWork -and -not $isCiLifecycleDiagnostic) {
            continue
        }
        if ($isCiLifecycleDiagnostic -or $directory.Name -notin $current) {
            Remove-ReleaseWorkDirectory -Root $Root -Path $directory.FullName
        }
    }
}

function Assert-CanonicalReleaseArtifact {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^\d+\.\d+\.\d+$')]
        [string]$Version,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $canonical = (Get-CanonicalReleaseDirectory -Root $Root -Version $Version).TrimEnd('\')
    $resolved = [IO.Path]::GetFullPath($Path)
    $parent = [IO.Path]::GetDirectoryName($resolved).TrimEnd('\')
    if (-not $parent.Equals($canonical, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Release artifacts must come directly from output\release\v$Version."
    }
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "Release artifact is missing."
    }
    return $resolved
}

function New-ReleaseManifestData {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^\d+\.\d+\.\d+$')]
        [string]$Version,
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[0-9a-fA-F]{40}$')]
        [string]$Commit,
        [Parameter(Mandatory = $true)]
        [ValidateSet('Release')]
        [string]$Configuration,
        [Parameter(Mandatory = $true)][string[]]$ArtifactPaths,
        [Parameter(Mandatory = $true)][string]$ProductCode,
        [Parameter(Mandatory = $true)][string]$UpgradeCode,
        [Parameter(Mandatory = $true)][bool]$PrivacyPassed,
        [Parameter(Mandatory = $true)][bool]$LifecyclePassed
    )

    $artifacts = @()
    foreach ($path in $ArtifactPaths) {
        $resolved = Assert-CanonicalReleaseArtifact `
            -Root $Root -Version $Version -Path $path
        $item = Get-Item -LiteralPath $resolved
        $artifacts += [ordered]@{
            file = $item.Name
            size = $item.Length
            sha256 = Get-ReleaseFileSha256 -Path $resolved
        }
    }

    return [ordered]@{
        schema_version = 1
        version = $Version
        commit = $Commit.ToLowerInvariant()
        configuration = $Configuration
        build_timestamp = [DateTimeOffset]::UtcNow.ToString('o')
        product_code = $ProductCode
        upgrade_code = $UpgradeCode
        privacy_scan = if ($PrivacyPassed) { 'passed' } else { 'failed' }
        lifecycle_test = if ($LifecyclePassed) { 'passed' } else { 'failed' }
        artifacts = $artifacts
    }
}
