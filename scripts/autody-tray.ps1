param(
    [string]$ProjectRoot = (Join-Path $PSScriptRoot ".."),
    [string]$DataRoot,
    [switch]$DefineOnly,
    [switch]$StopExisting,
    [switch]$OpenDashboardOnly
)

# A small first-party Windows Forms host.  It supervises the dashboard only;
# scheduled send/health tasks keep their independent Task Scheduler ownership.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$PackagedPython = Join-Path $ProjectRoot "runtime\python\python.exe"
$IsPackaged = Test-Path -LiteralPath $PackagedPython -PathType Leaf
if (-not $DataRoot) {
    $DataRoot = if ($IsPackaged) {
        Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "AutoDy"
    } else {
        $ProjectRoot
    }
}
$DataRoot = [IO.Path]::GetFullPath($DataRoot)
New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
$Python = if ($IsPackaged) { $PackagedPython } else { Join-Path $ProjectRoot ".venv\Scripts\python.exe" }
$Config = Join-Path $DataRoot "config.yaml"
$PackagePath = if ($IsPackaged) {
    Join-Path $ProjectRoot "runtime\python\Lib\site-packages\autody"
} else {
    Join-Path $ProjectRoot "src\autody"
}
$PreferredPort = 8765
$PortStatePath = Join-Path $DataRoot "data\service-port.json"
$script:ServicePort = $PreferredPort
$script:Url = "http://127.0.0.1:$PreferredPort"
$env:AUTODY_HOME = $DataRoot
$env:AUTODY_PROGRAM_ROOT = $ProjectRoot
$BrowserRoot = if ($IsPackaged) {
    Join-Path $ProjectRoot "runtime\ms-playwright"
} else {
    Join-Path $DataRoot "data\ms-playwright"
}
$env:AUTODY_BROWSERS_PATH = $BrowserRoot
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserRoot
$env:PLAYWRIGHT_SKIP_BROWSER_GC = "1"
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
    if (Test-Path -LiteralPath $packSource -PathType Container) {
        New-Item -ItemType Directory -Force -Path $packDestination | Out-Null
        Get-ChildItem -LiteralPath $packSource -File | Copy-Item -Destination $packDestination -Force
    }
    $moduleSource = Join-Path $ProjectRoot "optional-modules\AutoDy-Test-Center.autody-module.zip"
    $moduleDestination = Join-Path $DataRoot "optional-modules\AutoDy-Test-Center.autody-module.zip"
    if (Test-Path -LiteralPath $moduleSource -PathType Leaf) {
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

function Get-ServicePortCandidates {
    $ports = New-Object 'System.Collections.Generic.List[int]'
    $persisted = Get-PersistedServicePort
    if ($null -ne $persisted) { $ports.Add($persisted) }
    foreach ($port in $PreferredPort..8799) {
        if (-not $ports.Contains($port)) { $ports.Add($port) }
    }
    return $ports
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

function Get-ServiceSnapshot([int]$Port = $script:ServicePort) {
    $listener = Get-Listener $Port
    if ($null -eq $listener) { return $null }
    try {
        $url = "http://127.0.0.1:$Port"
        $identity = Invoke-RestMethod -Uri "$url/api/service-identity" -TimeoutSec 2 -ErrorAction Stop
        $modules = Invoke-RestMethod -Uri "$url/api/modules" -TimeoutSec 2 -ErrorAction Stop
        $process = Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId = {0}" -f $listener.OwningProcess) -ErrorAction Stop
        $ownerResult = Invoke-CimMethod -InputObject $process -MethodName GetOwner -ErrorAction Stop
        $owner = if ($ownerResult.ReturnValue -eq 0 -and $ownerResult.User) {
            if ($ownerResult.Domain) { "$($ownerResult.Domain)\$($ownerResult.User)" } else { [string]$ownerResult.User }
        } else { $null }
        return [pscustomobject]@{
            Pid = [int]$listener.OwningProcess
            ProcessPath = [string]$process.ExecutablePath
            Owner = $owner
            Identity = $identity
            Modules = $modules
        }
    } catch {
        return [pscustomobject]@{ Pid = [int]$listener.OwningProcess; ProcessPath = $null; Owner = $null; Identity = $null; Modules = $null }
    }
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

$ManagedPid = $null
function Stop-ManagedService {
    if ($null -eq $ManagedPid) { return $false }
    $expected = Get-ExpectedVersions
    $snapshot = Get-ServiceSnapshot
    if ($null -eq $snapshot -or $snapshot.Pid -ne $ManagedPid -or -not (Test-OwnedAutoDy $snapshot $expected.Core)) {
        Write-TrayLog "Refused to stop an unverified listener."
        return $false
    }
    Stop-Process -Id $ManagedPid -Force -ErrorAction Stop
    Write-TrayLog "Stopped managed AutoDy service PID $ManagedPid."
    $script:ManagedPid = $null
    return $true
}

function Start-Or-ReuseService {
    if (-not (Test-Path -LiteralPath $Python) -or -not (Test-Path -LiteralPath $Config)) { throw "Current AutoDy installation is incomplete." }
    $expected = Get-ExpectedVersions
    foreach ($port in Get-ServicePortCandidates) {
        $snapshot = Get-ServiceSnapshot $port
        if ($null -eq $snapshot -or -not (Test-OwnedAutoDy $snapshot $expected.Core)) { continue }
        Set-ServicePort $port
        $script:ManagedPid = $snapshot.Pid
        if (Test-HealthyAutoDy $snapshot $expected) { Save-ServicePort $port; return $snapshot }
        if (-not (Stop-ManagedService)) { throw "Verified old AutoDy service could not be stopped." }
    }
    $selectedPort = $null
    foreach ($port in $PreferredPort..8799) {
        if ($null -eq (Get-Listener $port)) { $selectedPort = $port; break }
    }
    if ($null -eq $selectedPort) { throw "No safe AutoDy port is available from $PreferredPort through 8799." }
    Set-ServicePort $selectedPort
    $process = Start-Process -FilePath $Python -ArgumentList @("-m", "autody.cli", "ui", "--no-open", "--config", $Config, "--port", $selectedPort) -WorkingDirectory $DataRoot -WindowStyle Hidden -PassThru
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        $snapshot = Get-ServiceSnapshot
        if ($null -ne $snapshot -and (Test-HealthyAutoDy $snapshot $expected)) {
            $script:ManagedPid = $snapshot.Pid
            Save-ServicePort $selectedPort
            return $snapshot
        }
        if ($null -ne $snapshot -and (Test-OwnedAutoDy $snapshot $expected.Core) -and $null -ne $snapshot.Modules) {
            $script:ManagedPid = $snapshot.Pid
            $module = @($snapshot.Modules.modules | Where-Object { $_.id -eq "autody-test-center" }) | Select-Object -First 1
            if ($null -ne $module -and $module.load_error) {
                Stop-ManagedService | Out-Null
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
                $script:ManagedPid = $snapshot.Pid
                Save-ServicePort $port
                return $snapshot
            }
        }
        Start-Sleep -Milliseconds 250
    }
    throw "The existing AutoDy service did not become healthy within 10 seconds."
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

    if ($ReuseOnly) {
        Wait-ForExistingHealthyService | Out-Null
    } else {
        Start-Or-ReuseService | Out-Null
    }
    if (-not (Show-ExistingDashboard)) {
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
    $expected = Get-ExpectedVersions
    $snapshot = Get-ServiceSnapshot
    if ($null -eq $snapshot -or -not (Test-OwnedAutoDy $snapshot $expected.Core)) { return "已停止" }
    if (Test-Path -LiteralPath (Join-Path $DataRoot "data\notifications\need-attention.txt")) { return "需要处理" }
    $outcomes = Join-Path $DataRoot "data\history\task-outcomes.json"
    if ((Test-Path -LiteralPath $outcomes) -and ((Get-Content -Raw -LiteralPath $outcomes -ErrorAction SilentlyContinue) -match '"outcome"\s*:\s*"retry_pending"')) { return "正在安全重试" }
    return "运行正常"
}

if ($StopExisting) {
    Stop-ExistingAutoDyTrayHosts | Out-Null
    return
}
if ($DefineOnly) { return }
if ($OpenDashboardOnly) {
    try {
        Open-VerifiedDashboard
    } catch {
        Write-TrayLog "Dashboard activation failed; the normal browser fallback did not complete."
        [Windows.Forms.MessageBox]::Show("无法打开 AutoDy 管理台，请稍后重试。", "AutoDy") | Out-Null
    }
    return
}

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
$open = $menu.Items.Add("打开 AutoDy 管理台")
$status = $menu.Items.Add("查看当前状态")
$logs = $menu.Items.Add("打开日志")
$restart = $menu.Items.Add("重启管理台")
$startup = $menu.Items.Add("启用/关闭开机启动")
[void]$menu.Items.Add("-")
$exitTray = $menu.Items.Add("退出托盘")
$exitStop = $menu.Items.Add("退出并停止 AutoDy")
$notify = New-Object System.Windows.Forms.NotifyIcon
$iconPath = Join-Path $ProjectRoot "assets\icons\autody.ico"
$notify.Icon = if (Test-Path -LiteralPath $iconPath) { New-Object System.Drawing.Icon -ArgumentList @($iconPath, 32, 32) } else { [Drawing.SystemIcons]::Application }
$notify.ContextMenuStrip = $menu
$notify.Visible = $true
$notify.Text = "AutoDy - 启动中"

$refresh = {
    $state = Get-TrayState
    $notify.Text = "AutoDy - $state"
    $status.Text = "查看当前状态 ($state)"
}
$open.add_Click({ Invoke-DashboardOpenAsync })
$notify.add_MouseClick({
    param($sender, $eventArgs)
    if ($eventArgs.Button -eq [Windows.Forms.MouseButtons]::Left) {
        Invoke-DashboardOpenAsync
    }
})
$status.add_Click({ & $refresh; [Windows.Forms.MessageBox]::Show($notify.Text, "AutoDy 当前状态") | Out-Null })
$logs.add_Click({ New-Item -ItemType Directory -Force -Path $LogDir | Out-Null; Start-Process explorer.exe -ArgumentList $LogDir })
$restart.add_Click({ try { Stop-ManagedService | Out-Null; Start-Or-ReuseService | Out-Null; & $refresh } catch { [Windows.Forms.MessageBox]::Show($_.Exception.Message, "AutoDy") | Out-Null } })
$startup.add_Click({ [Windows.Forms.MessageBox]::Show("开机启动由安装器/任务计划管理；托盘开关不会修改每日发送计划。", "AutoDy") | Out-Null })
$exitTray.add_Click({ $notify.Visible = $false; $context.ExitThread() })
$exitStop.add_Click({ Stop-ManagedService | Out-Null; $notify.Visible = $false; $context.ExitThread() })
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.add_Tick($refresh)
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
