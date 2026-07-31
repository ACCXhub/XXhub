param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = "1.4.1",
    [switch]$ReuseRuntime
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Output = Join-Path $Root "output"
$StageRoot = Join-Path $Output "msi-stage"
$Stage = Join-Path $StageRoot "AutoDy"
$Work = Join-Path $Output "msi-work"
$WixProject = Join-Path $Root "packaging\wix\AutoDy.Package.wixproj"
$GeneratedWxs = Join-Path $Work "GeneratedFiles.wxs"
$Msi = Join-Path $Output "AutoDy-$Version-x64.msi"
$Checksum = "$Msi.sha256"
$EmbeddedPython = Join-Path $Output "python-3.11.9-embed-amd64.zip"
$EmbeddedPythonUri = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
$EmbeddedPythonSha256 = "009D6BF7E3B2DDCA3D784FA09F90FE54336D5B60F0E0F305C37F400BF83CFD3B"
$HostPython = Join-Path $Root ".venv\Scripts\python.exe"

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

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$StageName,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [hashtable]$Environment = @{}
    )
    $previous = @{}
    foreach ($name in $Environment.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, [string]$Environment[$name], "Process")
    }
    try {
        $outputText = (& $FilePath @Arguments 2>&1 | Out-String)
        if ($LASTEXITCODE -ne 0) {
            $sanitized = $outputText.Replace($Root, "<repo>").Replace($Output, "<output>")
            throw "$StageName failed with exit code $LASTEXITCODE.`n$sanitized"
        }
    } finally {
        foreach ($name in $Environment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $previous[$name], "Process")
        }
    }
}

function Copy-ReleaseFile {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRelative,
        [Parameter(Mandatory = $true)][string]$DestinationRelative
    )
    $source = Join-Path $Root $SourceRelative
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required release file is missing: $SourceRelative"
    }
    $destination = Join-Path $Stage $DestinationRelative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Force
}

function Get-StableId {
    param([Parameter(Mandatory = $true)][string]$Value)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value))
        return ([BitConverter]::ToString($bytes).Replace("-", "")).Substring(0, 20)
    } finally {
        $algorithm.Dispose()
    }
}

function Get-StableGuid {
    param([Parameter(Mandatory = $true)][string]$Value)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = $algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes("AutoDy MSI payload:$Value"))
        $guidBytes = New-Object byte[] 16
        [Array]::Copy($hash, $guidBytes, $guidBytes.Length)
        return (New-Object Guid (,$guidBytes)).ToString().ToUpperInvariant()
    } finally {
        $algorithm.Dispose()
    }
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

if (-not (Test-Path -LiteralPath $HostPython -PathType Leaf)) {
    throw "Project Python environment is missing."
}
New-Item -ItemType Directory -Force -Path $Output | Out-Null
Reset-OutputDirectory $Work

$runtimeReady = $ReuseRuntime -and
    (Test-Path -LiteralPath (Join-Path $Stage "runtime\python\python.exe") -PathType Leaf) -and
    (Test-Path -LiteralPath (Join-Path $Stage "runtime\ms-playwright") -PathType Container)
if (-not $runtimeReady) {
    Reset-OutputDirectory $StageRoot
    New-Item -ItemType Directory -Force -Path $Stage | Out-Null

    if (-not (Test-Path -LiteralPath $EmbeddedPython -PathType Leaf)) {
        Invoke-WebRequest -Uri $EmbeddedPythonUri -OutFile $EmbeddedPython -UseBasicParsing
    }
    $embeddedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $EmbeddedPython).Hash
    if ($embeddedHash -ne $EmbeddedPythonSha256) {
        throw "Embedded Python archive checksum mismatch."
    }

    $pythonRoot = Join-Path $Stage "runtime\python"
    New-Item -ItemType Directory -Force -Path $pythonRoot | Out-Null
    Expand-Archive -LiteralPath $EmbeddedPython -DestinationPath $pythonRoot -Force
    @(
        "python311.zip",
        ".",
        "Lib\site-packages",
        "import site"
    ) | Set-Content -LiteralPath (Join-Path $pythonRoot "python311._pth") -Encoding ascii

    $wheelRoot = Join-Path $Work "wheel"
    New-Item -ItemType Directory -Force -Path $wheelRoot | Out-Null
    Invoke-NativeChecked "Build AutoDy wheel" $HostPython @(
        "-m", "pip", "--disable-pip-version-check", "wheel", "--quiet",
        "--no-deps", "--wheel-dir", $wheelRoot, $Root
    )
    $wheels = @(Get-ChildItem -LiteralPath $wheelRoot -Filter "autody-$Version-*.whl" -File)
    if ($wheels.Count -ne 1) {
        throw "The AutoDy wheel was not produced."
    }
    $wheel = $wheels[0]

    $sitePackages = Join-Path $pythonRoot "Lib\site-packages"
    New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null
    Invoke-NativeChecked "Install pinned runtime dependencies" $HostPython @(
        "-m", "pip", "--disable-pip-version-check", "install", "--quiet",
        "--only-binary=:all:", "--target", $sitePackages,
        "-r", (Join-Path $Root "packaging\runtime-requirements.txt")
    )
    Invoke-NativeChecked "Install AutoDy runtime package" $HostPython @(
        "-m", "pip", "--disable-pip-version-check", "install", "--quiet",
        "--no-deps", "--target", $sitePackages, $wheel.FullName
    )

    Get-ChildItem -LiteralPath $sitePackages -Recurse -Force -Directory |
        Where-Object { $_.Name -in @("__pycache__", "tests", "test") } |
        Sort-Object FullName -Descending |
        Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $sitePackages -Recurse -Force -File |
        Where-Object { $_.Extension -in @(".pyc", ".obj", ".lib", ".pdb") -or $_.Name -eq "direct_url.json" } |
        Remove-Item -Force
    $generatedScripts = Join-Path $sitePackages "bin"
    if (Test-Path -LiteralPath $generatedScripts) {
        Remove-Item -LiteralPath $generatedScripts -Recurse -Force
    }

    $packagedPython = Join-Path $pythonRoot "python.exe"
    Invoke-NativeChecked "Validate embedded Python runtime" $packagedPython @(
        "-c", "from importlib.metadata import version; import autody; assert version('autody') == '$Version'"
    )
    $browserRoot = Join-Path $Stage "runtime\ms-playwright"
    Invoke-NativeChecked "Install packaged Chromium" $packagedPython @(
        "-m", "playwright", "install", "chromium"
    ) @{
        PLAYWRIGHT_BROWSERS_PATH = $browserRoot
        PLAYWRIGHT_SKIP_BROWSER_GC = "1"
    }
}

$sitePackages = Join-Path $Stage "runtime\python\Lib\site-packages"
if (Test-Path -LiteralPath $sitePackages -PathType Container) {
    Get-ChildItem -LiteralPath $sitePackages -Recurse -Force -File |
        Where-Object { $_.Extension -in @(".pyc", ".obj", ".lib", ".pdb") -or $_.Name -eq "direct_url.json" } |
        Remove-Item -Force
    $generatedScripts = Join-Path $sitePackages "bin"
    if (Test-Path -LiteralPath $generatedScripts) {
        Remove-Item -LiteralPath $generatedScripts -Recurse -Force
    }
}
$playwrightLinks = Join-Path $Stage "runtime\ms-playwright\.links"
if (Test-Path -LiteralPath $playwrightLinks) {
    Remove-Item -LiteralPath $playwrightLinks -Recurse -Force
}

$releaseFiles = [ordered]@{
    "scripts\autody-tray.ps1" = "scripts\autody-tray.ps1"
    "scripts\health-check.ps1" = "scripts\health-check.ps1"
    "scripts\install-task.ps1" = "scripts\install-task.ps1"
    "scripts\remove-task.ps1" = "scripts\remove-task.ps1"
    "scripts\repair-playwright.ps1" = "scripts\repair-playwright.ps1"
    "scripts\run-scheduled.ps1" = "scripts\run-scheduled.ps1"
    "scripts\start-dashboard.cmd" = "scripts\start-dashboard.cmd"
    "scripts\start-dashboard.ps1" = "scripts\start-dashboard.ps1"
    "scripts\start-dashboard.vbs" = "scripts\start-dashboard.vbs"
    "assets\icons\autody.ico" = "assets\icons\autody.ico"
    "message-packs\cute-style.txt" = "message-packs\cute-style.txt"
    "message-packs\daily-greeting.txt" = "message-packs\daily-greeting.txt"
    "message-packs\festival.txt" = "message-packs\festival.txt"
    "message-packs\funny-style.txt" = "message-packs\funny-style.txt"
    "message-packs\index.json" = "message-packs\index.json"
    "message-packs\light-care.txt" = "message-packs\light-care.txt"
    "config.example.yaml" = "config.example.yaml"
    "messages.example.txt" = "messages.example.txt"
    "README.md" = "README.md"
    "CHANGELOG.md" = "CHANGELOG.md"
    "LICENSE" = "LICENSE"
    "SECURITY.md" = "SECURITY.md"
    "THIRD_PARTY_NOTICES.md" = "THIRD_PARTY_NOTICES.md"
    "docs\AUTODY_ENGINEERING_MANUAL.md" = "docs\AUTODY_ENGINEERING_MANUAL.md"
    "docs\RELEASE_NOTES.md" = "docs\RELEASE_NOTES.md"
}
foreach ($entry in $releaseFiles.GetEnumerator()) {
    Copy-ReleaseFile $entry.Key $entry.Value
}

$moduleArchive = Join-Path $Stage "optional-modules\AutoDy-Test-Center.autody-module.zip"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $moduleArchive) | Out-Null
$moduleCommand = "from pathlib import Path; from autody.modules import build_official_module_archive; build_official_module_archive(Path(r'$moduleArchive'))"
Invoke-NativeChecked "Build optional Test Center package" $HostPython @("-c", $moduleCommand)

$allowedExact = @($releaseFiles.Values) + @("optional-modules\AutoDy-Test-Center.autody-module.zip")
$stagedFiles = @(Get-ChildItem -LiteralPath $Stage -Recurse -Force -File)
foreach ($file in $stagedFiles) {
    $relative = $file.FullName.Substring($Stage.Length + 1)
    if ($relative -notlike "runtime\*" -and $relative -notin $allowedExact) {
        throw "MSI staging contains a file outside the explicit allowlist."
    }
    if ($relative -match '(^|\\)(data|tests?|fixtures?|screenshots?|browser-profile|account-profiles?|logs?|backups?)(\\|$)' -or
        $relative -match '(^|\\)(config\.yaml|messages\.txt|cookies?)(\\|$)') {
        throw "MSI staging contains an excluded runtime or development path."
    }
}

$userProfile = [Environment]::GetFolderPath("UserProfile")
$byteMapping = [Text.Encoding]::GetEncoding(28591)
$privatePatterns = @(
    $byteMapping.GetString([Text.Encoding]::UTF8.GetBytes($Root)),
    $byteMapping.GetString([Text.Encoding]::Unicode.GetBytes($Root)),
    $byteMapping.GetString([Text.Encoding]::UTF8.GetBytes($userProfile)),
    $byteMapping.GetString([Text.Encoding]::Unicode.GetBytes($userProfile))
)
foreach ($file in $stagedFiles) {
    if (Test-FileContainsMappedPattern $file.FullName $privatePatterns $byteMapping) {
        throw "MSI staging contains a private absolute path."
    }
}

$xmlSettings = New-Object System.Xml.XmlWriterSettings
$xmlSettings.Indent = $true
$xmlSettings.Encoding = New-Object System.Text.UTF8Encoding($false)
$writer = [System.Xml.XmlWriter]::Create($GeneratedWxs, $xmlSettings)
try {
    $writer.WriteStartDocument()
    $writer.WriteStartElement("Wix", "http://wixtoolset.org/schemas/v4/wxs")
    $writer.WriteStartElement("Fragment")

    $directorySet = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $stagedFiles | ForEach-Object {
        $relative = $_.FullName.Substring($Stage.Length + 1)
        $directory = Split-Path -Parent $relative
        while ($directory) {
            $null = $directorySet.Add($directory)
            $directory = Split-Path -Parent $directory
        }
    }
    $directories = @($directorySet | Sort-Object { ($_ -split '\\').Count }, { $_ })
    foreach ($directory in $directories) {
        $parent = Split-Path -Parent $directory
        $parentId = if ($parent) { "Dir_$(Get-StableId $parent)" } else { "INSTALLFOLDER" }
        $writer.WriteStartElement("DirectoryRef")
        $writer.WriteAttributeString("Id", $parentId)
        $writer.WriteStartElement("Directory")
        $writer.WriteAttributeString("Id", "Dir_$(Get-StableId $directory)")
        $writer.WriteAttributeString("Name", (Split-Path -Leaf $directory))
        $writer.WriteEndElement()
        $writer.WriteEndElement()
    }
    $writer.WriteEndElement()

    $writer.WriteStartElement("Fragment")
    $writer.WriteStartElement("ComponentGroup")
    $writer.WriteAttributeString("Id", "PayloadComponents")
    foreach ($file in ($stagedFiles | Sort-Object FullName)) {
        $relative = $file.FullName.Substring($Stage.Length + 1)
        $directory = Split-Path -Parent $relative
        $directoryId = "INSTALLFOLDER"
        if ($directory) {
            $directoryId = "Dir_$(Get-StableId $directory)"
        }
        $fileHash = Get-StableId $relative
        $writer.WriteStartElement("Component")
        $writer.WriteAttributeString("Id", "Cmp_$fileHash")
        $writer.WriteAttributeString("Directory", $directoryId)
        $writer.WriteAttributeString("Guid", (Get-StableGuid $relative))
        $writer.WriteStartElement("File")
        $writer.WriteAttributeString("Id", "Fil_$fileHash")
        $writer.WriteAttributeString("Source", $file.FullName)
        $writer.WriteEndElement()
        $writer.WriteStartElement("RegistryValue")
        $writer.WriteAttributeString("Root", "HKCU")
        $writer.WriteAttributeString("Key", "Software\AutoDy\Installer\Components")
        $writer.WriteAttributeString("Name", $fileHash)
        $writer.WriteAttributeString("Value", "1")
        $writer.WriteAttributeString("Type", "integer")
        $writer.WriteAttributeString("KeyPath", "yes")
        $writer.WriteEndElement()
        $writer.WriteEndElement()
    }
    $writer.WriteStartElement("Component")
    $writer.WriteAttributeString("Id", "PayloadDirectoryCleanup")
    $writer.WriteAttributeString("Directory", "INSTALLFOLDER")
    $writer.WriteAttributeString("Guid", (Get-StableGuid "__payload_directories__"))
    $directoriesForRemoval = $directories | Sort-Object `
        @{ Expression = { ($_ -split '\\').Count }; Descending = $true },
        @{ Expression = { $_ }; Descending = $false }
    foreach ($directory in $directoriesForRemoval) {
        $directoryHash = Get-StableId $directory
        $writer.WriteStartElement("RemoveFolder")
        $writer.WriteAttributeString("Id", "Rmv_$directoryHash")
        $writer.WriteAttributeString("Directory", "Dir_$directoryHash")
        $writer.WriteAttributeString("On", "uninstall")
        $writer.WriteEndElement()
    }
    $writer.WriteStartElement("RemoveFolder")
    $writer.WriteAttributeString("Id", "RemoveInstallFolder")
    $writer.WriteAttributeString("Directory", "INSTALLFOLDER")
    $writer.WriteAttributeString("On", "uninstall")
    $writer.WriteEndElement()
    $writer.WriteStartElement("RemoveFolder")
    $writer.WriteAttributeString("Id", "RemoveProgramsFolderIfEmpty")
    $writer.WriteAttributeString("Directory", "ProgramsFolder")
    $writer.WriteAttributeString("On", "uninstall")
    $writer.WriteEndElement()
    $writer.WriteStartElement("RegistryValue")
    $writer.WriteAttributeString("Root", "HKCU")
    $writer.WriteAttributeString("Key", "Software\AutoDy\Installer")
    $writer.WriteAttributeString("Name", "PayloadDirectories")
    $writer.WriteAttributeString("Value", "1")
    $writer.WriteAttributeString("Type", "integer")
    $writer.WriteAttributeString("KeyPath", "yes")
    $writer.WriteEndElement()
    $writer.WriteEndElement()
    $writer.WriteEndElement()
    $writer.WriteEndElement()
    $writer.WriteEndElement()
    $writer.WriteEndDocument()
} finally {
    $writer.Dispose()
}

$dotnetOutput = Join-Path $Work "wix-output"
New-Item -ItemType Directory -Force -Path $dotnetOutput | Out-Null
Invoke-NativeChecked "Build WiX MSI" "dotnet" @(
    "build", $WixProject, "--nologo", "--verbosity", "quiet",
    "-p:ProductVersion=$Version",
    "-p:StageDir=$Stage",
    "-p:GeneratedWxs=$GeneratedWxs",
    "-p:AcceptEula=wix7",
    "-p:OutputPath=$dotnetOutput"
)
$builtMsiFiles = @(Get-ChildItem -LiteralPath $dotnetOutput -Filter "AutoDy-$Version-x64.msi" -Recurse -File)
if ($builtMsiFiles.Count -ne 1) {
    throw "WiX build did not produce the expected MSI."
}
$builtMsi = $builtMsiFiles[0]
Copy-Item -LiteralPath $builtMsi.FullName -Destination $Msi -Force
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Msi).Hash.ToLowerInvariant()
"$hash  AutoDy-$Version-x64.msi" | Set-Content -LiteralPath $Checksum -Encoding ascii -NoNewline

Write-Host "MSI built: AutoDy-$Version-x64.msi"
Write-Host "MSI checksum: AutoDy-$Version-x64.msi.sha256"
