from pathlib import Path
import os
import subprocess


def test_tray_controller_has_a_real_single_instance_windows_host_and_expected_menu():
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")

    for token in [
        "System.Threading.Mutex",
        "System.Windows.Forms.NotifyIcon",
        "打开 AutoDy 管理台",
        "查看当前状态",
        "打开日志",
        "重启管理台",
        "启用或关闭开机启动",
        "退出托盘",
        "退出并停止 AutoDy",
        "启动中",
        "运行正常",
        "正在安全重试",
        "需要处理",
        "已停止",
    ]:
        assert token in text


def test_tray_refuses_unrelated_8765_owner_and_stops_only_verified_managed_service():
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")

    assert "Port 8765 belongs to an unrelated process" in text
    assert "Test-OwnedAutoDy" in text
    assert "$snapshot.Pid -ne $ManagedPid" in text
    assert "Stop-Process -Id $ManagedPid" in text


def test_tray_exit_does_not_change_scheduled_tasks_or_user_data():
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")

    exit_tray = text[text.index("$exitTray.add_Click"):text.index("$exitStop.add_Click")]
    assert "Stop-ManagedService" not in exit_tray
    assert "Unregister-ScheduledTask" not in text
    assert "Remove-Item" not in text


def test_desktop_launcher_starts_the_tray_host():
    text = Path("scripts/start-dashboard.ps1").read_text(encoding="utf-8-sig")

    assert "autody-tray.ps1" in text
    assert "-ProjectRoot" in text


def test_tray_opens_dashboard_once_after_health_and_reuses_it_on_second_launch():
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")

    assert "function Wait-ForExistingHealthyService" in text
    assert "function Show-ExistingDashboard" in text
    assert "function Open-VerifiedDashboard" in text
    assert "Start-Or-ReuseService | Out-Null" in text
    assert "Wait-ForExistingHealthyService | Out-Null" in text
    assert text.count("Start-Process $Url") == 1
    assert "SelectionItemPattern" in text
    assert "$open.add_Click({ try { Open-VerifiedDashboard" in text
    startup = text[text.index("try {", text.index("$timer.add_Tick")):text.index("[Windows.Forms.Application]::Run")]
    assert "Open-VerifiedDashboard" in startup


def test_duplicate_tray_launch_opens_dashboard_without_starting_another_host_or_service():
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")

    duplicate_start = text.index("if (-not $createdNew)")
    duplicate = text[duplicate_start:text.index("\nAdd-Type -AssemblyName System.Windows.Forms", text.index("exit 0", duplicate_start))]
    assert "Start-Or-ReuseService" not in duplicate
    assert "Open-VerifiedDashboard -ReuseOnly" in duplicate
    assert "exit 0" in duplicate
    opener = text[text.index("function Open-VerifiedDashboard"):text.index("function Get-TrayState")]
    assert "Wait-ForExistingHealthyService" in opener


def test_ui_automation_failure_is_optional_and_returns_fallback_without_throwing():
    script_path = Path("scripts/autody-tray.ps1").resolve()
    command = r"""
    $ErrorActionPreference = "Stop"
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
      $env:AUTODY_TEST_TRAY_SCRIPT, [ref]$tokens, [ref]$errors
    )
    if ($errors.Count) { throw ($errors.Message -join "; ") }
    $function = $ast.Find({
      param($node)
      $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq "Invoke-OptionalDashboardActivation"
    }, $true)
    if ($null -eq $function) { throw "optional activation function missing" }
    Invoke-Expression $function.Extent.Text
    function Write-TrayLog([string]$Message) { $script:RecordedLog = $Message }
    $result = Invoke-OptionalDashboardActivation -Action {
      throw [System.Runtime.InteropServices.COMException]::new(
        "RPC_E_SERVERFAULT",
        [int]0x80010105
      )
    }
    if ($result -ne $false) { throw "UI Automation failure did not request fallback" }
    if ($script:RecordedLog -match "RPC_E_SERVERFAULT") {
      throw "raw UI Automation exception was logged"
    }
    """
    test_env = os.environ.copy()
    test_env["AUTODY_TEST_TRAY_SCRIPT"] = str(script_path)
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=test_env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
