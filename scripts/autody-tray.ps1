param(
    [string]$ProjectRoot = (Join-Path $PSScriptRoot ".."),
    [string]$DataRoot,
    [switch]$DefineOnly,
    [switch]$StopExisting,
    [switch]$OpenDashboardOnly
)

# A small first-party Windows Forms host. Scheduled send/health tasks keep their
# independent Task Scheduler ownership; this host only supervises the dashboard.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
. (Join-Path $PSScriptRoot "resolve-runtime-roots.ps1")
$RuntimeContext = Resolve-AutoDyLaunchContext -ProgramRoot $ProjectRoot -DataRoot $DataRoot
$ProjectRoot = $RuntimeContext.ProgramRoot
$DataRoot = $RuntimeContext.DataRoot
$Python = $RuntimeContext.Python
$BrowserRoot = $RuntimeContext.BrowserRoot
$DistributionMode = $RuntimeContext.DistributionMode
$IsPackaged = $RuntimeContext.IsPackaged
New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
$Config = Join-Path $DataRoot "config.yaml"
$PackagePath = if ($IsPackaged) {
    Join-Path $ProjectRoot "runtime\python\Lib\site-packages\autody"
} else {
    Join-Path $ProjectRoot "src\autody"
}
$PreferredPort = 8765
$PortStatePath = Join-Path $DataRoot "data\service-port.json"
$ManualStopRegistryPath = "HKCU:\Software\AutoDy"
$WatchdogHealthGraceSeconds = 20
$WatchdogRecoveryWindowMinutes = 10
$WatchdogRecoveryLimit = 3
$script:ServicePort = $PreferredPort
$script:Url = "http://127.0.0.1:$PreferredPort"
$script:Repairing = $false
$script:StartupPageOpened = $false
$script:WatchdogSuppressed = $false
$script:WatchdogNeedsAttention = $false
$script:WatchdogFailureSince = $null
$script:WatchdogRecoveryAttempts = @()
$script:ManagedPid = $null
$script:ManagedProcessIdentity = $null
$script:ServiceControlToken = [Guid]::NewGuid().ToString("N")
$env:AUTODY_HOME = $DataRoot
$env:AUTODY_PROGRAM_ROOT = $ProjectRoot
$env:AUTODY_BROWSERS_PATH = $BrowserRoot
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserRoot
$env:PLAYWRIGHT_SKIP_BROWSER_GC = "1"
$env:AUTODY_SERVICE_CONTROL_TOKEN = $script:ServiceControlToken
$LogDir = Join-Path $DataRoot "data\logs"
$TrayLog = Join-Path $LogDir "tray.log"

if ($IsPackaged) {
    foreach ($seed in @(
        @{ Source = (Join-Path $ProjectRoot "config.example.yaml"); Destination = $Config },
        @{ Source = (Join-Path $ProjectRoot "messages.example.txt"); Destination = (Join-Path $DataRoot "messages.txt") }
    )) {
        if (-not (Test-Path -LiteralPath $seed.Destination) -and (Test-Path -LiteralPath $seed.Source)) {
            Copy-Item -LiteralPath $seed.Source -Destination $seed.Destination
        }
    }
    $packSource = Join-Path $ProjectRoot "message-packs"
    $packDestination = Join-Path $DataRoot "message-packs"
    if ($packSource -ine $packDestination -and (Test-Path -LiteralPath $packSource -PathType Container)) {
        New-Item -ItemType Directory -Force -Path $packDestination | Out-Null
        Get-ChildItem -LiteralPath $packSource -File | Copy-Item -Destination $packDestination -Force
    }
    $moduleSource = Join-Path $ProjectRoot "optional-modules\AutoDy-Test-Center.autody-module.zip"
    $moduleDestination = Join-Path $DataRoot "optional-modules\AutoDy-Test-Center.autody-module.zip"
    if ($moduleSource -ine $moduleDestination -and (Test-Path -LiteralPath $moduleSource -PathType Leaf)) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $moduleDestination) | Out-Null
        Copy-Item -LiteralPath $moduleSource -Destination $moduleDestination -Force
    }
}

$script:TrayRendererInitialized = $false
function Initialize-TrayRenderer {
    if ($script:TrayRendererInitialized) { return }
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        if (-not ("AutoDyMenuRenderer" -as [type])) {
    Add-Type -ReferencedAssemblies @("System.Windows.Forms", "System.Drawing") -TypeDefinition @"
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public sealed class AutoDyMenuColorTable : ProfessionalColorTable {
    private readonly bool dark;
    public AutoDyMenuColorTable(bool darkMode) { dark = darkMode; UseSystemColors = false; }
    public override Color ToolStripDropDownBackground { get { return dark ? Color.FromArgb(43, 43, 43) : Color.FromArgb(249, 249, 249); } }
    public override Color ImageMarginGradientBegin { get { return ToolStripDropDownBackground; } }
    public override Color ImageMarginGradientMiddle { get { return ToolStripDropDownBackground; } }
    public override Color ImageMarginGradientEnd { get { return ToolStripDropDownBackground; } }
    public override Color MenuItemSelected { get { return dark ? Color.FromArgb(62, 62, 62) : Color.FromArgb(232, 232, 232); } }
    public override Color MenuItemBorder { get { return MenuItemSelected; } }
    public override Color SeparatorDark { get { return dark ? Color.FromArgb(72, 72, 72) : Color.FromArgb(220, 220, 220); } }
    public override Color SeparatorLight { get { return SeparatorDark; } }
}

public sealed class AutoDyMenuRenderer : ToolStripProfessionalRenderer {
    private readonly bool dark;
    public AutoDyMenuRenderer(bool darkMode) : base(new AutoDyMenuColorTable(darkMode)) {
        dark = darkMode;
        RoundedEdges = true;
    }
    protected override void OnRenderMenuItemBackground(ToolStripItemRenderEventArgs e) {
        if (!e.Item.Selected) { return; }
        Rectangle bounds = new Rectangle(5, 2, Math.Max(1, e.Item.Width - 10), Math.Max(1, e.Item.Height - 4));
        using (GraphicsPath path = Rounded(bounds, 5))
        using (SolidBrush brush = new SolidBrush(dark ? Color.FromArgb(62, 62, 62) : Color.FromArgb(232, 232, 232))) {
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            e.Graphics.FillPath(brush, path);
        }
    }
    protected override void OnRenderItemText(ToolStripItemTextRenderEventArgs e) {
        e.TextColor = !e.Item.Enabled
            ? (dark ? Color.FromArgb(145, 145, 145) : Color.FromArgb(145, 145, 145))
            : (dark ? Color.FromArgb(245, 245, 245) : Color.FromArgb(32, 32, 32));
        base.OnRenderItemText(e);
    }
    protected override void OnRenderSeparator(ToolStripSeparatorRenderEventArgs e) {
        using (Pen pen = new Pen(dark ? Color.FromArgb(72, 72, 72) : Color.FromArgb(220, 220, 220))) {
            int y = e.Item.Height / 2;
            e.Graphics.DrawLine(pen, 10, y, Math.Max(10, e.Item.Width - 10), y);
        }
    }
    protected override void OnRenderToolStripBorder(ToolStripRenderEventArgs e) {
        using (Pen pen = new Pen(dark ? Color.FromArgb(76, 76, 76) : Color.FromArgb(214, 214, 214))) {
            e.Graphics.DrawRectangle(pen, 0, 0, Math.Max(0, e.ToolStrip.Width - 1), Math.Max(0, e.ToolStrip.Height - 1));
        }
    }
    private static GraphicsPath Rounded(Rectangle bounds, int radius) {
        GraphicsPath path = new GraphicsPath();
        int diameter = radius * 2;
        path.AddArc(bounds.Left, bounds.Top, diameter, diameter, 180, 90);
        path.AddArc(bounds.Right - diameter, bounds.Top, diameter, diameter, 270, 90);
        path.AddArc(bounds.Right - diameter, bounds.Bottom - diameter, diameter, diameter, 0, 90);
        path.AddArc(bounds.Left, bounds.Bottom - diameter, diameter, diameter, 90, 90);
        path.CloseFigure();
        return path;
    }
}

public static class AutoDyMenuWindow {
    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);
    public static void ApplyRoundedCorners(ContextMenuStrip menu) {
        try {
            int preference = 2;
            DwmSetWindowAttribute(menu.Handle, 33, ref preference, sizeof(int));
        } catch { }
    }
}
"@
        }
        $script:TrayRendererInitialized = $true
    } catch {
        throw "AutoDy tray menu renderer could not be initialized."
    }
}

function Write-TrayLog([string]$Message) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message" | Add-Content -LiteralPath $TrayLog -Encoding UTF8
}

function Get-ManualStopDate {
    try {
        return [string](Get-ItemPropertyValue -LiteralPath $ManualStopRegistryPath -Name "ManualStopDate" -ErrorAction Stop)
    } catch {
        return $null
    }
}

function Set-ManualStopToday {
    New-Item -Path $ManualStopRegistryPath -Force | Out-Null
    Set-ItemProperty -LiteralPath $ManualStopRegistryPath -Name "ManualStopDate" -Value (Get-Date -Format "yyyy-MM-dd") -Type String
}

function Clear-ManualStopDate {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Software\AutoDy", $true)
    if ($null -eq $key) { return }
    try {
        $key.DeleteValue("ManualStopDate", $false)
    } finally {
        $key.Dispose()
    }
}

function Test-ManualStopToday {
    return (Get-ManualStopDate) -eq (Get-Date -Format "yyyy-MM-dd")
}

function Test-ExactTrayHostCommandLine(
    [string]$CommandLine,
    [string]$ExpectedScript
) {
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    $escaped = [Regex]::Escape([IO.Path]::GetFullPath($ExpectedScript))
    $pattern = '(?i)(?:^|\s)-File\s+(?:"' + $escaped + '"|' + $escaped + ')(?:\s|$)'
    return [Regex]::IsMatch($CommandLine, $pattern)
}

function Stop-ExistingAutoDyTrayHosts {
    $expectedScript = Join-Path $ProjectRoot "scripts\autody-tray.ps1"
    $currentOwner = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $stopped = 0
    $processes = Get-CimInstance -ClassName Win32_Process -ErrorAction Stop |
        Where-Object {
            $_.ProcessId -ne $PID -and
            ($_.Name -eq "powershell.exe" -or $_.Name -eq "pwsh.exe") -and
            (Test-ExactTrayHostCommandLine $_.CommandLine $expectedScript)
        }
    foreach ($process in $processes) {
        $ownerResult = Invoke-CimMethod -InputObject $process -MethodName GetOwner -ErrorAction Stop
        $owner = if ($ownerResult.ReturnValue -eq 0 -and $ownerResult.User) {
            if ($ownerResult.Domain) {
                "$($ownerResult.Domain)\$($ownerResult.User)"
            } else {
                [string]$ownerResult.User
            }
        } else {
            $null
        }
        if ($owner -ne $currentOwner) { continue }
        Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
        $stopped += 1
    }
    return $stopped
}

function Set-ServicePort([int]$Port) {
    $script:ServicePort = $Port
    $script:Url = "http://127.0.0.1:$Port"
}

function Get-PersistedServicePort {
    try {
        $saved = Get-Content -LiteralPath $PortStatePath -Raw -ErrorAction Stop | ConvertFrom-Json
        $port = [int]$saved.port
        if ($port -ge $PreferredPort -and $port -le 8799) { return $port }
    } catch { }
    return $null
}

function Save-ServicePort([int]$Port) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $PortStatePath) | Out-Null
    @{ port = $Port } | ConvertTo-Json | Set-Content -LiteralPath $PortStatePath -Encoding utf8
}

function Get-ListeningServicePorts {
    try {
        return @(
            Get-NetTCPConnection -State Listen -ErrorAction Stop |
                Where-Object { $_.LocalPort -ge $PreferredPort -and $_.LocalPort -le 8799 } |
                Select-Object -ExpandProperty LocalPort -Unique
        )
    } catch {
        return @()
    }
}

function Get-ServicePortCandidates([int[]]$ListeningPorts = @(Get-ListeningServicePorts)) {
    $ports = New-Object 'System.Collections.Generic.List[int]'
    $persisted = Get-PersistedServicePort
    if ($null -ne $persisted -and $ListeningPorts -contains $persisted) {
        $ports.Add($persisted)
    }
    foreach ($port in @($ListeningPorts | Sort-Object)) {
        if (-not $ports.Contains($port)) { $ports.Add($port) }
    }
    return $ports
}

function Get-AvailableServicePort([int[]]$ListeningPorts = @(Get-ListeningServicePorts)) {
    foreach ($port in $PreferredPort..8799) {
        if ($ListeningPorts -notcontains $port) { return $port }
    }
    return $null
}

function Test-SystemDarkTheme {
    try {
        $value = Get-ItemPropertyValue -LiteralPath "HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize" -Name "AppsUseLightTheme" -ErrorAction Stop
        return [int]$value -eq 0
    } catch {
        return $false
    }
}

function Set-AutoDyMenuTheme {
    param([Parameter(Mandatory = $true)][System.Windows.Forms.ContextMenuStrip]$Menu)

    Initialize-TrayRenderer
    $dark = Test-SystemDarkTheme
    $Menu.Renderer = New-Object AutoDyMenuRenderer -ArgumentList ([bool]$dark)
    $Menu.Font = New-Object System.Drawing.Font -ArgumentList @("Segoe UI", 10.0)
    $Menu.Padding = New-Object System.Windows.Forms.Padding -ArgumentList @(6, 6, 6, 6)
    $Menu.ShowImageMargin = $false
    $Menu.ShowCheckMargin = $false
    $Menu.BackColor = if ($dark) { [Drawing.Color]::FromArgb(43, 43, 43) } else { [Drawing.Color]::FromArgb(249, 249, 249) }
    $Menu.ForeColor = if ($dark) { [Drawing.Color]::FromArgb(245, 245, 245) } else { [Drawing.Color]::FromArgb(32, 32, 32) }
    foreach ($item in $Menu.Items) {
        if ($item -isnot [System.Windows.Forms.ToolStripSeparator]) {
            $item.Padding = New-Object System.Windows.Forms.Padding -ArgumentList @(4, 3, 8, 3)
        }
    }
}

function Get-Listener([int]$Port = $script:ServicePort) {
    try { return Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1 } catch { return $null }
}

function Get-ProcessOwner($Process) {
    try {
        $ownerResult = Invoke-CimMethod -InputObject $Process -MethodName GetOwner -ErrorAction Stop
        if ($ownerResult.ReturnValue -eq 0 -and $ownerResult.User) {
            if ($ownerResult.Domain) { return "$($ownerResult.Domain)\$($ownerResult.User)" }
            return [string]$ownerResult.User
        }
    } catch { }
    return $null
}

function Get-ProcessIdentitySnapshot([int]$ProcessId) {
    try {
        $process = Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId = {0}" -f $ProcessId) -ErrorAction Stop
        return [pscustomobject]@{
            Pid = [int]$process.ProcessId
            ProcessPath = [string]$process.ExecutablePath
            Owner = Get-ProcessOwner $process
            CreationDate = [string]$process.CreationDate
        }
    } catch {
        return $null
    }
}

function Get-ServiceSnapshot([int]$Port = $script:ServicePort) {
    $listener = Get-Listener $Port
    if ($null -eq $listener) { return $null }
    $process = Get-ProcessIdentitySnapshot ([int]$listener.OwningProcess)
    if ($null -eq $process) { return $null }
    $url = "http://127.0.0.1:$Port"
    $identity = $null
    $modules = $null
    try { $identity = Invoke-RestMethod -Uri "$url/api/service-identity" -TimeoutSec 2 -ErrorAction Stop } catch { }
    try { $modules = Invoke-RestMethod -Uri "$url/api/modules" -TimeoutSec 2 -ErrorAction Stop } catch { }
    return [pscustomobject]@{
        Pid = $process.Pid
        ProcessPath = $process.ProcessPath
        Owner = $process.Owner
        CreationDate = $process.CreationDate
        Identity = $identity
        Modules = $modules
    }
}

function Test-OwnedAutoDyIdentity($Snapshot) {
    if ($null -eq $Snapshot -or $null -eq $Snapshot.Identity) { return $false }
    try {
        return $Snapshot.Identity.application -eq "AutoDy" -and
            $Snapshot.Pid -gt 0 -and
            ([IO.Path]::GetFullPath([string]$Snapshot.ProcessPath).TrimEnd('\\') -eq [IO.Path]::GetFullPath($Python).TrimEnd('\\')) -and
            $Snapshot.Owner -eq [Security.Principal.WindowsIdentity]::GetCurrent().Name -and
            ([IO.Path]::GetFullPath([string]$Snapshot.Identity.project_path).TrimEnd('\\') -eq [IO.Path]::GetFullPath($DataRoot).TrimEnd('\\')) -and
            ([IO.Path]::GetFullPath([string]$Snapshot.Identity.package_path).TrimEnd('\\') -eq [IO.Path]::GetFullPath($PackagePath).TrimEnd('\\')) -and
            ([IO.Path]::GetFullPath([string]$Snapshot.Identity.python_executable).TrimEnd('\\') -eq [IO.Path]::GetFullPath($Python).TrimEnd('\\'))
    } catch { return $false }
}

function Test-OwnedAutoDy($Snapshot, [string]$ExpectedVersion) {
    if ($null -eq $Snapshot -or $null -eq $Snapshot.Identity) { return $false }
    try {
        return $Snapshot.Identity.application -eq "AutoDy" -and
            $Snapshot.Identity.version -eq $ExpectedVersion -and
            $Snapshot.Pid -gt 0 -and
            ([IO.Path]::GetFullPath([string]$Snapshot.ProcessPath).TrimEnd('\\') -eq [IO.Path]::GetFullPath($Python).TrimEnd('\\')) -and
            $Snapshot.Owner -eq [Security.Principal.WindowsIdentity]::GetCurrent().Name -and
            ([IO.Path]::GetFullPath([string]$Snapshot.Identity.project_path).TrimEnd('\\') -eq [IO.Path]::GetFullPath($DataRoot).TrimEnd('\\')) -and
            ([IO.Path]::GetFullPath([string]$Snapshot.Identity.package_path).TrimEnd('\\') -eq [IO.Path]::GetFullPath($PackagePath).TrimEnd('\\')) -and
            ([IO.Path]::GetFullPath([string]$Snapshot.Identity.python_executable).TrimEnd('\\') -eq [IO.Path]::GetFullPath($Python).TrimEnd('\\'))
    } catch { return $false }
}

function Set-ManagedServiceSnapshot($Snapshot) {
    if ($null -eq $Snapshot -or -not (Test-OwnedAutoDyIdentity $Snapshot)) {
        throw "Cannot manage an unverified AutoDy service."
    }
    $script:ManagedPid = [int]$Snapshot.Pid
    $script:ManagedProcessIdentity = [pscustomobject]@{
        Pid = [int]$Snapshot.Pid
        ProcessPath = [string]$Snapshot.ProcessPath
        Owner = [string]$Snapshot.Owner
        CreationDate = [string]$Snapshot.CreationDate
    }
}

function Clear-ManagedServiceSnapshot {
    $script:ManagedPid = $null
    $script:ManagedProcessIdentity = $null
}

function Test-ManagedProcessStillCurrent {
    if ($null -eq $script:ManagedPid -or $null -eq $script:ManagedProcessIdentity) { return $false }
    $current = Get-ProcessIdentitySnapshot ([int]$script:ManagedPid)
    if ($null -eq $current) { return $false }
    try {
        return $current.Pid -eq $script:ManagedProcessIdentity.Pid -and
            ([IO.Path]::GetFullPath([string]$current.ProcessPath).TrimEnd('\\') -eq [IO.Path]::GetFullPath([string]$script:ManagedProcessIdentity.ProcessPath).TrimEnd('\\')) -and
            $current.Owner -eq $script:ManagedProcessIdentity.Owner -and
            $current.CreationDate -eq $script:ManagedProcessIdentity.CreationDate
    } catch { return $false }
}

function Get-ExpectedVersions {
    $values = @(& $Python -c "from importlib.metadata import version; from autody.modules import OFFICIAL_TEST_CENTER_VERSION; print(version('autody')); print(OFFICIAL_TEST_CENTER_VERSION)")
    if ($LASTEXITCODE -ne 0 -or $values.Count -ne 2) { throw "Cannot read current AutoDy package versions." }
    return [pscustomobject]@{ Core = $values[0].Trim(); Module = $values[1].Trim() }
}

function Test-HealthyAutoDy($Snapshot, $Expected) {
    if (-not (Test-OwnedAutoDy $Snapshot $Expected.Core) -or $null -eq $Snapshot.Modules) { return $false }
    $module = @($Snapshot.Modules.modules | Where-Object { $_.id -eq "autody-test-center" }) | Select-Object -First 1
    return $Snapshot.Identity.version -eq $Expected.Core -and $module.bundled_version -eq $Expected.Module -and
        $module.bundled_package.required_autody_version -eq ">=1.3.0,<2.0.0" -and $module.bundled_available
}

function Stop-ManagedService {
    param([switch]$Silent)

    if ($null -eq $script:ManagedPid) { return $false }
    $ManagedPid = [int]$script:ManagedPid
    $pidToStop = $ManagedPid
    $snapshot = Get-ServiceSnapshot
    if ($null -ne $snapshot) {
        if ($snapshot.Pid -ne $ManagedPid -or -not (Test-OwnedAutoDyIdentity $snapshot)) {
            if (-not (Test-ManagedProcessStillCurrent)) {
                if (-not $Silent) { Write-TrayLog "Refused to stop an unverified listener PID $ManagedPid." }
                return $false
            }
        } else {
            Set-ManagedServiceSnapshot $snapshot
        }
    } elseif (-not (Test-ManagedProcessStillCurrent)) {
        if (-not $Silent) { Write-TrayLog "Refused to stop an unverified listener PID $ManagedPid." }
        return $false
    }

    try {
        Invoke-RestMethod -Uri "$script:Url/api/service-shutdown" -Method Post -Headers @{ "X-AutoDy-Control-Token" = $script:ServiceControlToken } -TimeoutSec 2 -ErrorAction Stop | Out-Null
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            Start-Sleep -Milliseconds 100
            if ($null -eq (Get-ProcessIdentitySnapshot $pidToStop)) {
                if (-not $Silent) { Write-TrayLog "Stopped managed AutoDy service PID $pidToStop gracefully." }
                Clear-ManagedServiceSnapshot
                return $true
            }
        }
    } catch {
        if (-not $Silent) { Write-TrayLog "Graceful service stop was unavailable for verified PID $pidToStop." }
    }

    if (-not (Test-ManagedProcessStillCurrent)) {
        if ($null -eq (Get-ProcessIdentitySnapshot $pidToStop)) {
            Clear-ManagedServiceSnapshot
            return $true
        }
        if (-not $Silent) { Write-TrayLog "Refused forced stop because verified PID identity changed." }
        return $false
    }
    Stop-Process -Id $ManagedPid -Force -ErrorAction Stop
    if (-not $Silent) { Write-TrayLog "Force-stopped verified AutoDy service PID $pidToStop." }
    Clear-ManagedServiceSnapshot
    return $true
}

function Stop-VerifiedInstalledAutoDyServices {
    $stopped = 0
    foreach ($port in Get-ServicePortCandidates) {
        $snapshot = Get-ServiceSnapshot $port
        if ($null -eq $snapshot -or -not (Test-OwnedAutoDyIdentity $snapshot)) { continue }
        Set-ServicePort $port
        Set-ManagedServiceSnapshot $snapshot
        if (Stop-ManagedService) { $stopped += 1 }
    }
    return $stopped
}

function New-StartupWaitPage {
    param(
        [Parameter(Mandatory = $true)][string]$DashboardUrl,
        [string]$Destination
    )

    if ([string]::IsNullOrWhiteSpace($Destination)) {
        $key = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($ProjectRoot)).Replace('=','').Replace('/','_').Replace('+','-')
        $Destination = Join-Path ([IO.Path]::GetTempPath()) "AutoDy-startup-$key.html"
    }
    $parent = Split-Path -Parent $Destination
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }

    $logsPath = [IO.Path]::GetFullPath($LogDir).TrimEnd('\') + [IO.Path]::DirectorySeparatorChar
    $logsUrl = (New-Object System.Uri -ArgumentList $logsPath).AbsoluteUri
    $dashboardJson = ConvertTo-Json -Compress -InputObject $DashboardUrl
    $logsJson = ConvertTo-Json -Compress -InputObject $logsUrl
    $html = @'
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoDy 正在启动…</title>
<style>
html,body{height:100%;margin:0}body{display:grid;place-items:center;background:#f7f8fa;color:#222;font:15px "Segoe UI","Microsoft YaHei",sans-serif}.card{text-align:center}.spinner{width:22px;height:22px;margin:0 auto 14px;border:2px solid #d8dbe1;border-top-color:#5d6b82;border-radius:50%;animation:spin .8s linear infinite}h1{margin:0;font-size:18px;font-weight:600}.failure{display:none;max-width:360px}p{color:#626b78;line-height:1.6}.actions{display:flex;justify-content:center;gap:10px}button,a{box-sizing:border-box;padding:7px 14px;border:1px solid #cfd3da;border-radius:6px;background:#fff;color:#273142;text-decoration:none;cursor:pointer;font:inherit}@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<main class="card" id="starting"><div class="spinner"></div><h1>AutoDy 正在启动…</h1></main>
<main class="card failure" id="failure"><h1>AutoDy 启动未完成</h1><p>后台服务暂时无法访问。你可以重试，或打开日志查看原因。</p><div class="actions"><button id="retry" type="button">重试</button><a id="logs" target="_blank" rel="noopener">打开日志</a></div></main>
<script>
const dashboardUrl = __DASHBOARD_URL__;
const logsUrl = __LOGS_URL__;
let deadline = Date.now() + 30000;
let polling = true;
const starting = document.getElementById("starting");
const failure = document.getElementById("failure");
document.getElementById("logs").href = logsUrl;
async function poll() {
  if (!polling) return;
  if (Date.now() >= deadline) {
    polling = false;
    starting.style.display = "none";
    failure.style.display = "block";
    return;
  }
  try {
    await fetch(dashboardUrl + "/?autody-startup=" + Date.now(), {cache: "no-store", mode: "no-cors"});
    location.replace(dashboardUrl);
  } catch (_error) {
    setTimeout(poll, 250);
  }
}
document.getElementById("retry").addEventListener("click", () => {
  deadline = Date.now() + 30000;
  polling = true;
  failure.style.display = "none";
  starting.style.display = "block";
  poll();
});
poll();
</script>
</body>
</html>
'@
    $html = $html.Replace("__DASHBOARD_URL__", $dashboardJson).Replace("__LOGS_URL__", $logsJson)
    $utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
    [IO.File]::WriteAllText($Destination, $html, $utf8)
    return [IO.Path]::GetFullPath($Destination)
}

function Start-Or-ReuseService {
    param(
        [scriptblock]$OnColdStart,
        [switch]$SkipPortPersistence,
        [switch]$Silent
    )

    if (-not (Test-Path -LiteralPath $Python) -or -not (Test-Path -LiteralPath $Config)) { throw "Current AutoDy installation is incomplete." }
    $expected = Get-ExpectedVersions
    foreach ($port in Get-ServicePortCandidates) {
        $snapshot = Get-ServiceSnapshot $port
        if ($null -eq $snapshot -or -not (Test-OwnedAutoDyIdentity $snapshot)) { continue }
        Set-ServicePort $port
        Set-ManagedServiceSnapshot $snapshot
        if (Test-HealthyAutoDy $snapshot $expected) {
            if (-not $SkipPortPersistence) { Save-ServicePort $port }
            return $snapshot
        }
        if (-not (Stop-ManagedService -Silent:$Silent)) { throw "Verified old AutoDy service could not be stopped." }
    }
    $selectedPort = Get-AvailableServicePort
    if ($null -eq $selectedPort) { throw "No safe AutoDy port is available from $PreferredPort through 8799." }
    Set-ServicePort $selectedPort
    Start-Process -FilePath $Python -ArgumentList @("-m", "autody.cli", "ui", "--no-open", "--config", $Config, "--port", $selectedPort) -WorkingDirectory $DataRoot -WindowStyle Hidden | Out-Null
    if ($null -ne $OnColdStart) { & $OnColdStart }
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        $snapshot = Get-ServiceSnapshot
        if ($null -ne $snapshot -and (Test-HealthyAutoDy $snapshot $expected)) {
            Set-ManagedServiceSnapshot $snapshot
            if (-not $SkipPortPersistence) { Save-ServicePort $selectedPort }
            return $snapshot
        }
        if ($null -ne $snapshot -and (Test-OwnedAutoDy $snapshot $expected.Core) -and $null -ne $snapshot.Modules) {
            Set-ManagedServiceSnapshot $snapshot
            $module = @($snapshot.Modules.modules | Where-Object { $_.id -eq "autody-test-center" }) | Select-Object -First 1
            if ($null -ne $module -and $module.load_error) {
                Stop-ManagedService -Silent:$Silent | Out-Null
                throw "Current installation has a stale or invalid Test Center package: $($module.load_error)"
            }
        }
    }
    throw "AutoDy did not become healthy with the current installation within 10 seconds."
}

function Wait-ForExistingHealthyService {
    if (-not (Test-Path -LiteralPath $Python) -or -not (Test-Path -LiteralPath $Config)) {
        throw "Current AutoDy installation is incomplete."
    }
    $expected = Get-ExpectedVersions
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        foreach ($port in Get-ServicePortCandidates) {
            $snapshot = Get-ServiceSnapshot $port
            if ($null -ne $snapshot -and (Test-OwnedAutoDy $snapshot $expected.Core) -and (Test-HealthyAutoDy $snapshot $expected)) {
                Set-ServicePort $port
                Set-ManagedServiceSnapshot $snapshot
                Save-ServicePort $port
                return $snapshot
            }
        }
        Start-Sleep -Milliseconds 250
    }
    throw "The existing AutoDy service did not become healthy within 10 seconds."
}

function Reset-WatchdogRecoveryState {
    $script:WatchdogNeedsAttention = $false
    $script:WatchdogFailureSince = $null
    $script:WatchdogRecoveryAttempts = @()
}

function Get-RecentWatchdogRecoveryAttempts {
    $cutoff = (Get-Date).AddMinutes(-$WatchdogRecoveryWindowMinutes)
    $script:WatchdogRecoveryAttempts = @(
        $script:WatchdogRecoveryAttempts | Where-Object { $_ -ge $cutoff }
    )
    return @($script:WatchdogRecoveryAttempts)
}

function Invoke-WatchdogRecovery([string]$Reason) {
    if ($script:WatchdogNeedsAttention -or $script:WatchdogSuppressed -or (Test-ManualStopToday)) { return }
    $recent = @(Get-RecentWatchdogRecoveryAttempts)
    if ($recent.Count -ge $WatchdogRecoveryLimit) {
        $script:WatchdogNeedsAttention = $true
        return
    }
    try {
        if (Test-ManagedProcessStillCurrent) {
            Stop-ManagedService -Silent | Out-Null
        } else {
            Clear-ManagedServiceSnapshot
        }
        Start-Or-ReuseService -SkipPortPersistence -Silent | Out-Null
        $script:WatchdogFailureSince = $null
    } catch {
        # Automatic recovery intentionally leaves user data and repair actions alone.
    } finally {
        $script:WatchdogRecoveryAttempts = @($script:WatchdogRecoveryAttempts) + @(Get-Date)
        $recent = @(Get-RecentWatchdogRecoveryAttempts)
        if ($recent.Count -ge $WatchdogRecoveryLimit) {
            $script:WatchdogNeedsAttention = $true
        }
    }
}

function Invoke-WatchdogTick {
    if ($script:WatchdogSuppressed -or $script:Repairing -or $script:WatchdogNeedsAttention -or (Test-ManualStopToday)) { return }
    try {
        $expected = Get-ExpectedVersions
        $snapshot = Get-ServiceSnapshot
        if ($null -ne $snapshot -and (Test-HealthyAutoDy $snapshot $expected)) {
            Set-ManagedServiceSnapshot $snapshot
            $script:WatchdogFailureSince = $null
            return
        }
        if ($null -ne $snapshot -and (Test-OwnedAutoDyIdentity $snapshot)) {
            Set-ManagedServiceSnapshot $snapshot
        }
        if (-not (Test-ManagedProcessStillCurrent)) {
            Clear-ManagedServiceSnapshot
            Invoke-WatchdogRecovery "managed process exited"
            return
        }
        if ($null -eq $script:WatchdogFailureSince) {
            $script:WatchdogFailureSince = Get-Date
            return
        }
        if (((Get-Date) - $script:WatchdogFailureSince).TotalSeconds -lt $WatchdogHealthGraceSeconds) { return }
        Invoke-WatchdogRecovery "health failed for at least $WatchdogHealthGraceSeconds seconds"
    } catch {
        # A watchdog probe failure is non-destructive and retried by the next timer tick.
    }
}

function Invoke-OptionalDashboardActivation {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )

    try {
        return [bool](& $Action)
    } catch {
        try {
            Write-TrayLog "Dashboard window enumeration is unavailable; opening the dashboard normally."
        } catch {
            # Window reuse is optional, including its diagnostic logging.
        }
        return $false
    }
}

function Show-ExistingDashboard {
    return Invoke-OptionalDashboardActivation {
        Add-Type -AssemblyName UIAutomationClient
        if (-not ("AutoDyWindowActivation" -as [type])) {
            Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class AutoDyWindowActivation {
    [DllImport("user32.dll")]
    public static extern bool ShowWindowAsync(IntPtr hWnd, int command);
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@
        }
        $desktop = [Windows.Automation.AutomationElement]::RootElement
        $windowCondition = New-Object Windows.Automation.PropertyCondition(
            [Windows.Automation.AutomationElement]::ControlTypeProperty,
            [Windows.Automation.ControlType]::Window
        )
        $tabCondition = New-Object Windows.Automation.PropertyCondition(
            [Windows.Automation.AutomationElement]::ControlTypeProperty,
            [Windows.Automation.ControlType]::TabItem
        )
        foreach ($window in $desktop.FindAll([Windows.Automation.TreeScope]::Children, $windowCondition)) {
            $name = [string]$window.Current.Name
            $dashboardTab = $null
            if ($name -like "AutoDy 续火助手*") {
                $dashboardTab = $window
            } else {
                foreach ($tab in $window.FindAll([Windows.Automation.TreeScope]::Descendants, $tabCondition)) {
                    if ([string]$tab.Current.Name -eq "AutoDy 续火助手") {
                        $dashboardTab = $tab
                        break
                    }
                }
            }
            if ($null -eq $dashboardTab) { continue }
            try {
                if ($dashboardTab -ne $window) {
                    $selection = $dashboardTab.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern)
                    $selection.Select()
                }
                $handle = [IntPtr]$window.Current.NativeWindowHandle
                if ($handle -ne [IntPtr]::Zero) {
                    [AutoDyWindowActivation]::ShowWindowAsync($handle, 9) | Out-Null
                    [AutoDyWindowActivation]::SetForegroundWindow($handle) | Out-Null
                }
                return $true
            } catch {
                Write-TrayLog "Existing dashboard activation failed; opening a new dashboard window."
            }
        }
        return $false
    }
}

function Open-VerifiedDashboard {
    param([switch]$ReuseOnly)

    $script:StartupPageOpened = $false
    if ($ReuseOnly) {
        Wait-ForExistingHealthyService | Out-Null
    } else {
        Start-Or-ReuseService -OnColdStart {
            if (-not (Show-ExistingDashboard)) {
                try {
                    $waitPage = New-StartupWaitPage -DashboardUrl $script:Url
                    Start-Process $waitPage
                    $script:StartupPageOpened = $true
                } catch {
                    Write-TrayLog "Startup wait page could not be opened; the dashboard will open after readiness."
                }
            }
        } | Out-Null
    }
    if (-not $script:StartupPageOpened -and -not (Show-ExistingDashboard)) {
        Start-Process $Url
    }
}

$script:LastDashboardActivation = [DateTime]::MinValue
function Invoke-DashboardOpenAsync {
    $now = Get-Date
    if (($now - $script:LastDashboardActivation).TotalMilliseconds -lt 700) { return }
    $script:LastDashboardActivation = $now
    $arguments = @(
        "-NoProfile",
        "-Sta",
        "-WindowStyle", "Hidden",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $PSCommandPath),
        "-ProjectRoot", ('"{0}"' -f $ProjectRoot),
        "-DataRoot", ('"{0}"' -f $DataRoot),
        "-OpenDashboardOnly"
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -WorkingDirectory $ProjectRoot -WindowStyle Hidden | Out-Null
}

function Get-TrayState {
    if ($script:Repairing) { return "正在修复" }
    if ($script:WatchdogNeedsAttention) { return "需要处理" }
    $expected = Get-ExpectedVersions
    $snapshot = Get-ServiceSnapshot
    if ($null -eq $snapshot -or -not (Test-OwnedAutoDy $snapshot $expected.Core)) { return "已停止" }
    if (Test-Path -LiteralPath (Join-Path $DataRoot "data\notifications\need-attention.txt")) { return "需要处理" }
    $outcomes = Join-Path $DataRoot "data\history\task-outcomes.json"
    if ((Test-Path -LiteralPath $outcomes) -and ((Get-Content -Raw -LiteralPath $outcomes -ErrorAction SilentlyContinue) -match '"outcome"\s*:\s*"retry_pending"')) { return "正在执行" }
    return "运行正常"
}

if ($StopExisting) {
    Stop-ExistingAutoDyTrayHosts | Out-Null
    Stop-VerifiedInstalledAutoDyServices | Out-Null
    return
}
if ($DefineOnly) { return }
if ($OpenDashboardOnly) {
    try {
        Open-VerifiedDashboard
    } catch {
        Write-TrayLog "Dashboard activation failed; the normal browser fallback did not complete."
        Add-Type -AssemblyName System.Windows.Forms
        [Windows.Forms.MessageBox]::Show("无法打开 AutoDy 管理台，请稍后重试。", "AutoDy") | Out-Null
    }
    return
}

# A normal user launch explicitly resumes AutoDy for today.
Clear-ManualStopDate

$createdNew = $false
$Mutex = New-Object System.Threading.Mutex($true, "Local\AutoDyTray-$([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($ProjectRoot)).Replace('=','').Replace('/','_').Replace('+','-'))", [ref]$createdNew)
if (-not $createdNew) {
    try {
        Open-VerifiedDashboard -ReuseOnly
    } catch {
        Write-TrayLog "Reuse error: $($_.Exception.Message)"
        Add-Type -AssemblyName System.Windows.Forms
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, "AutoDy 启动失败") | Out-Null
    } finally {
        $Mutex.Dispose()
    }
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$context = New-Object System.Windows.Forms.ApplicationContext
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$menu.AutoSize = $true
$menu.MinimumSize = New-Object System.Drawing.Size -ArgumentList @(224, 0)
Set-AutoDyMenuTheme -Menu $menu
$menu.add_Opened({ Set-AutoDyMenuTheme -Menu $menu; [AutoDyMenuWindow]::ApplyRoundedCorners($menu) })
$status = $menu.Items.Add("AutoDy · 启动中")
$status.Enabled = $false
[void]$menu.Items.Add("-")
$open = $menu.Items.Add("打开管理台")
$repair = $menu.Items.Add("一键诊断与修复")
$logs = $menu.Items.Add("查看运行日志")
[void]$menu.Items.Add("-")
$restart = $menu.Items.Add("重新启动后台服务")
[void]$menu.Items.Add("-")
$exitTray = $menu.Items.Add("隐藏托盘图标")
$exitStop = $menu.Items.Add("完全退出 AutoDy")
$notify = New-Object System.Windows.Forms.NotifyIcon
$iconPath = Join-Path $ProjectRoot "assets\icons\autody.ico"
$notify.Icon = if (Test-Path -LiteralPath $iconPath) { New-Object System.Drawing.Icon -ArgumentList @($iconPath, 32, 32) } else { [Drawing.SystemIcons]::Application }
$notify.ContextMenuStrip = $menu
$notify.Visible = $true
$notify.Text = "AutoDy - 启动中"

$refresh = {
    $state = Get-TrayState
    $notify.Text = "AutoDy - $state"
    $status.Text = "AutoDy · $state"
}
$open.add_Click({ Invoke-DashboardOpenAsync })
$notify.add_MouseClick({
    param($sender, $eventArgs)
    if ($eventArgs.Button -eq [Windows.Forms.MouseButtons]::Left) {
        Invoke-DashboardOpenAsync
    }
})
$logs.add_Click({ New-Item -ItemType Directory -Force -Path $LogDir | Out-Null; Start-Process explorer.exe -ArgumentList $LogDir })
$restart.add_Click({
    $script:WatchdogSuppressed = $true
    try {
        Stop-ManagedService | Out-Null
        Start-Or-ReuseService | Out-Null
        $script:WatchdogFailureSince = $null
    } catch {
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, "AutoDy") | Out-Null
    } finally {
        $script:WatchdogSuppressed = $false
        & $refresh
    }
})
$repair.add_Click({
    $script:Repairing = $true
    $script:WatchdogSuppressed = $true
    & $refresh
    try {
        Start-Or-ReuseService | Out-Null
        $result = Invoke-RestMethod -Uri "$script:Url/api/repair" -Method Post -TimeoutSec 600 -ErrorAction Stop
        $lines = @($result.summary)
        $lines += @($result.repaired | ForEach-Object { "✓ $($_.label)" })
        $lines += @($result.checks | ForEach-Object { "✓ $($_.label)" })
        $lines += @($result.manual | ForEach-Object { "! $($_.label)" })
        if (@($result.manual).Count -eq 0) { Reset-WatchdogRecoveryState }
        [Windows.Forms.MessageBox]::Show(($lines -join [Environment]::NewLine), "AutoDy 诊断与修复") | Out-Null
    } catch {
        [Windows.Forms.MessageBox]::Show("诊断与修复未完成，请查看运行日志。", "AutoDy") | Out-Null
    } finally {
        $script:WatchdogSuppressed = $false
        $script:Repairing = $false
        & $refresh
    }
})
$exitTray.add_Click({ $notify.Visible = $false; $context.ExitThread() })
$exitStop.add_Click({
    Set-ManualStopToday
    $script:WatchdogSuppressed = $true
    try { Stop-ManagedService | Out-Null } finally {
        $notify.Visible = $false
        $context.ExitThread()
    }
})
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.add_Tick({ Invoke-WatchdogTick; & $refresh })
try {
    Open-VerifiedDashboard
    & $refresh
    $timer.Start()
    [Windows.Forms.Application]::Run($context)
} catch {
    Write-TrayLog "Startup error: $($_.Exception.Message)"
    [Windows.Forms.MessageBox]::Show($_.Exception.Message, "AutoDy 启动失败") | Out-Null
} finally {
    $timer.Stop()
    $notify.Visible = $false
    $notify.Dispose()
    $Mutex.ReleaseMutex() | Out-Null
    $Mutex.Dispose()
}
