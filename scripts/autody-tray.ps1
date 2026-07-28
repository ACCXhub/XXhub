param(
    [string]$ProjectRoot = (Join-Path $PSScriptRoot "..")
)

# A small first-party Windows Forms host.  It supervises the dashboard only;
# scheduled send/health tasks keep their independent Task Scheduler ownership.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Config = Join-Path $ProjectRoot "config.yaml"
$PackagePath = Join-Path $ProjectRoot "src\autody"
$Url = "http://127.0.0.1:8765"
$env:AUTODY_HOME = $ProjectRoot
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $ProjectRoot "data\ms-playwright"
$env:PLAYWRIGHT_SKIP_BROWSER_GC = "1"
$LogDir = Join-Path $ProjectRoot "data\logs"
$TrayLog = Join-Path $LogDir "tray.log"

function Write-TrayLog([string]$Message) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message" | Add-Content -LiteralPath $TrayLog -Encoding UTF8
}

function Get-Listener {
    try { return Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop | Select-Object -First 1 } catch { return $null }
}

function Get-ServiceSnapshot {
    $listener = Get-Listener
    if ($null -eq $listener) { return $null }
    try {
        $identity = Invoke-RestMethod -Uri "$Url/api/service-identity" -TimeoutSec 2 -ErrorAction Stop
        $modules = Invoke-RestMethod -Uri "$Url/api/modules" -TimeoutSec 2 -ErrorAction Stop
        return [pscustomobject]@{ Pid = [int]$listener.OwningProcess; Identity = $identity; Modules = $modules }
    } catch { return [pscustomobject]@{ Pid = [int]$listener.OwningProcess; Identity = $null; Modules = $null } }
}

function Test-OwnedAutoDy($Snapshot) {
    if ($null -eq $Snapshot -or $null -eq $Snapshot.Identity) { return $false }
    try {
        return $Snapshot.Identity.application -eq "AutoDy" -and
            ([IO.Path]::GetFullPath([string]$Snapshot.Identity.project_path).TrimEnd('\\') -eq [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\\')) -and
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
    if (-not (Test-OwnedAutoDy $Snapshot) -or $null -eq $Snapshot.Modules) { return $false }
    $module = @($Snapshot.Modules.modules | Where-Object { $_.id -eq "autody-test-center" }) | Select-Object -First 1
    return $Snapshot.Identity.version -eq $Expected.Core -and $module.bundled_version -eq $Expected.Module -and
        $module.bundled_package.required_autody_version -eq ">=1.3.0,<2.0.0" -and $module.bundled_available
}

$ManagedPid = $null
function Stop-ManagedService {
    if ($null -eq $ManagedPid) { return $false }
    $snapshot = Get-ServiceSnapshot
    if ($null -eq $snapshot -or $snapshot.Pid -ne $ManagedPid -or -not (Test-OwnedAutoDy $snapshot)) {
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
    $snapshot = Get-ServiceSnapshot
    if ($null -ne $snapshot) {
        if (-not (Test-OwnedAutoDy $snapshot)) { throw "Port 8765 belongs to an unrelated process; the tray will not attach or stop it." }
        $script:ManagedPid = $snapshot.Pid
        if (Test-HealthyAutoDy $snapshot $expected) { return $snapshot }
        if (-not (Stop-ManagedService)) { throw "Verified old AutoDy service could not be stopped." }
    }
    $process = Start-Process -FilePath $Python -ArgumentList @("-m", "autody.cli", "ui", "--no-open", "--config", $Config) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        $snapshot = Get-ServiceSnapshot
        if ($null -ne $snapshot -and (Test-HealthyAutoDy $snapshot $expected)) {
            $script:ManagedPid = $snapshot.Pid
            return $snapshot
        }
        if ($null -ne $snapshot -and (Test-OwnedAutoDy $snapshot) -and $null -ne $snapshot.Modules) {
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
        $snapshot = Get-ServiceSnapshot
        if ($null -ne $snapshot) {
            if (-not (Test-OwnedAutoDy $snapshot)) {
                throw "Port 8765 belongs to an unrelated process; AutoDy will not open it."
            }
            if (Test-HealthyAutoDy $snapshot $expected) {
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

function Get-TrayState {
    $snapshot = Get-ServiceSnapshot
    if ($null -eq $snapshot -or -not (Test-OwnedAutoDy $snapshot)) { return "已停止" }
    if (Test-Path -LiteralPath (Join-Path $ProjectRoot "data\notifications\need-attention.txt")) { return "需要处理" }
    $outcomes = Join-Path $ProjectRoot "data\history\task-outcomes.json"
    if ((Test-Path -LiteralPath $outcomes) -and ((Get-Content -Raw -LiteralPath $outcomes -ErrorAction SilentlyContinue) -match '"outcome"\s*:\s*"retry_pending"')) { return "正在安全重试" }
    return "运行正常"
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
$open = $menu.Items.Add("打开 AutoDy 管理台")
$status = $menu.Items.Add("查看当前状态")
$logs = $menu.Items.Add("打开日志")
$restart = $menu.Items.Add("重启管理台")
$startup = $menu.Items.Add("启用或关闭开机启动")
[void]$menu.Items.Add("-")
$exitTray = $menu.Items.Add("退出托盘")
$exitStop = $menu.Items.Add("退出并停止 AutoDy")
$notify = New-Object System.Windows.Forms.NotifyIcon
$iconPath = Join-Path $ProjectRoot "assets\icons\autody.ico"
$notify.Icon = if (Test-Path -LiteralPath $iconPath) { [Drawing.Icon]::ExtractAssociatedIcon($iconPath) } else { [Drawing.SystemIcons]::Application }
$notify.ContextMenuStrip = $menu
$notify.Visible = $true
$notify.Text = "AutoDy - 启动中"

$refresh = {
    $state = Get-TrayState
    $notify.Text = "AutoDy - $state"
    $status.Text = "查看当前状态 ($state)"
}
$open.add_Click({ try { Open-VerifiedDashboard } catch { [Windows.Forms.MessageBox]::Show($_.Exception.Message, "AutoDy") | Out-Null } })
$notify.add_DoubleClick({ $open.PerformClick() })
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
