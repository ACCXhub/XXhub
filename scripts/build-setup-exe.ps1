param(
    [string]$Version = "1.5.4",
    [string]$ArtifactDirectory,
    [string]$MsiPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $ArtifactDirectory) {
    $ArtifactDirectory = Join-Path $Root "output\release\v$Version"
}
if (-not $MsiPath) {
    $MsiPath = Join-Path $ArtifactDirectory "AutoDy-$Version-x64.msi"
}
$ArtifactDirectory = [IO.Path]::GetFullPath($ArtifactDirectory)
$MsiPath = [IO.Path]::GetFullPath($MsiPath)
if (-not (Test-Path -LiteralPath $MsiPath -PathType Leaf)) {
    throw "Canonical MSI payload not found: $MsiPath"
}

$Project = Join-Path $Root "packaging\wix\AutoDy.Bundle.wixproj"
$Work = Join-Path $Root "output\work\setup-exe\v$Version"
$OutputName = "AutoDy-Setup-$Version.exe"
$OutputPath = Join-Path $ArtifactDirectory $OutputName

if (Test-Path -LiteralPath $Work) {
    Remove-Item -LiteralPath $Work -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Work, $ArtifactDirectory | Out-Null

Write-Host "[BUILD] Building AutoDy Setup EXE over canonical MSI."
& dotnet restore $Project --nologo
if ($LASTEXITCODE -ne 0) {
    throw "Restore AutoDy setup bundle failed with exit code $LASTEXITCODE."
}

$binRoot = Join-Path $Work "bin\"
$objRoot = Join-Path $Work "obj\"
& dotnet build $Project `
    -c Release `
    --no-restore `
    --nologo `
    "-p:ProductVersion=$Version" `
    "-p:MsiPath=$MsiPath" `
    "-p:BaseOutputPath=$binRoot" `
    "-p:BaseIntermediateOutputPath=$objRoot"
if ($LASTEXITCODE -ne 0) {
    throw "Build AutoDy setup bundle failed with exit code $LASTEXITCODE."
}

$Built = Get-ChildItem -LiteralPath $Work -Recurse -File -Filter $OutputName | Select-Object -First 1
if ($null -eq $Built) {
    throw "Setup bundle build completed without $OutputName."
}
Copy-Item -LiteralPath $Built.FullName -Destination $OutputPath -Force

$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $OutputPath).Hash.ToLowerInvariant()
$Sidecar = "$OutputPath.sha256"
"$Hash  $OutputName" | Set-Content -LiteralPath $Sidecar -Encoding ascii

Write-Host "[BUILD] Setup EXE: $OutputPath"
Write-Host "[BUILD] SHA-256: $Hash"
$OutputPath
