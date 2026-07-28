param(
    [string]$ProjectRoot = (Join-Path $PSScriptRoot "..")
)

# The desktop launcher starts the persistent tray host.  The host, rather than
# a short-lived browser command, owns the verified local dashboard lifecycle.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Tray = Join-Path $PSScriptRoot "autody-tray.ps1"
if (-not (Test-Path -LiteralPath $Tray -PathType Leaf)) {
    throw "AutoDy tray controller was not found: $Tray"
}
& $Tray -ProjectRoot $ProjectRoot
exit $LASTEXITCODE
