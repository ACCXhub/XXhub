from pathlib import Path
import os
import subprocess

from PIL import Image


def test_tray_controller_has_a_real_single_instance_windows_host_and_expected_menu():
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")

    for token in [
        "System.Threading.Mutex",
        "System.Windows.Forms.NotifyIcon",
        "AutoDy ·",
        "打开管理台",
        "一键诊断与修复",
        "查看运行日志",
        "重新启动后台服务",
        "隐藏托盘图标",
        "完全退出 AutoDy",
        "启动中",
        "运行正常",
        "正在执行",
        "正在修复",
        "需要处理",
        "已停止",
    ]:
        assert token in text

    for retired in [
        "查看当前状态", "重启管理台", "启用/关闭开机启动",
        "退出托盘", "退出并停止 AutoDy",
    ]:
        assert retired not in text

    assert '$status.Enabled = $false' in text
    assert 'Invoke-RestMethod -Uri "$script:Url/api/repair" -Method Post' in text


def test_tray_falls_back_from_an_unrelated_8765_owner_and_stops_only_verified_managed_service():
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")

    assert "$PreferredPort = 8765" in text
    assert "function Get-PersistedServicePort" in text
    assert "function Save-ServicePort" in text
    assert "function Get-ServicePortCandidates" in text
    assert "foreach ($port in $PreferredPort..8799)" in text
    assert '"--port", $selectedPort' in text
    assert "Save-ServicePort $selectedPort" in text
    assert "Test-OwnedAutoDy" in text
    assert "$snapshot.Pid -ne $ManagedPid" in text
    assert "Stop-Process -Id $ManagedPid" in text


def test_tray_reuses_a_persisted_fallback_port_for_dashboard_and_shutdown():
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")

    wait = text[
        text.index("function Wait-ForExistingHealthyService"):
        text.index("function Invoke-OptionalDashboardActivation")
    ]
    assert "foreach ($port in Get-ServicePortCandidates)" in wait
    assert "Set-ServicePort $port" in wait
    assert "Save-ServicePort $port" in wait
    assert "$script:Url = \"http://127.0.0.1:$Port\"" in text
    assert "function Stop-ManagedService" in text


def test_tray_renderer_is_initialized_only_by_the_tray_menu_path():
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")

    assert "function Initialize-TrayRenderer" in text
    assert "Initialize-TrayRenderer" in text[text.index("function Set-AutoDyMenuTheme"):]
    renderer = text[text.index("function Initialize-TrayRenderer"):text.index("function Write-TrayLog")]
    assert "$script:TrayRendererInitialized" in renderer


def test_tray_revalidates_a_stale_persisted_port_before_selecting_a_new_service_port():
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")

    start = text[
        text.index("function Start-Or-ReuseService"):
        text.index("function Wait-ForExistingHealthyService")
    ]
    assert "foreach ($port in Get-ServicePortCandidates)" in start
    assert "$selectedPort = Get-AvailableServicePort" in start
    assert start.index("foreach ($port in Get-ServicePortCandidates)") < start.index(
        "$selectedPort = Get-AvailableServicePort"
    )


def test_tray_port_selection_queries_listeners_in_bulk(tmp_path: Path):
    script_path = Path("scripts/autody-tray.ps1").resolve()
    command = r"""
    $ErrorActionPreference = "Stop"
    . $env:AUTODY_TEST_TRAY_SCRIPT -ProjectRoot $env:AUTODY_TEST_ROOT -DataRoot $env:AUTODY_TEST_DATA -DefineOnly
    $script:ListenerQueries = 0
    function Get-PersistedServicePort { return 8768 }
    function Get-NetTCPConnection {
      $script:ListenerQueries += 1
      return @(
        [pscustomobject]@{ LocalPort = 8765; State = "Listen" },
        [pscustomobject]@{ LocalPort = 8768; State = "Listen" },
        [pscustomobject]@{ LocalPort = 9000; State = "Listen" }
      )
    }
    $candidates = @(Get-ServicePortCandidates)
    $available = Get-AvailableServicePort
    if (($candidates -join ",") -ne "8768,8765") {
      throw "unexpected candidates: $($candidates -join ',')"
    }
    if ($available -ne 8766) { throw "unexpected available port: $available" }
    if ($script:ListenerQueries -ne 2) {
      throw "listener table was not queried once per operation: $script:ListenerQueries"
    }
    "ok"
    """
    test_env = os.environ.copy()
    test_env.update(
        {
            "AUTODY_TEST_TRAY_SCRIPT": str(script_path),
            "AUTODY_TEST_ROOT": str(Path.cwd()),
            "AUTODY_TEST_DATA": str(tmp_path),
        }
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=test_env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"


def test_tray_requires_current_identity_process_and_user_scope_before_reusing_a_service():
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
        $node.Name -eq "Test-OwnedAutoDy"
    }, $true)
    if ($null -eq $function) { throw "ownership function missing" }
    Invoke-Expression $function.Extent.Text
    $DataRoot = "C:\\AutoDyData"
    $PackagePath = "C:\\AutoDy\\runtime\\python\\Lib\\site-packages\\autody"
    $Python = "C:\\AutoDy\\runtime\\python\\python.exe"
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $identity = [pscustomobject]@{
      application = "AutoDy"
      version = "1.4.3"
      project_path = $DataRoot
      package_path = $PackagePath
      python_executable = $Python
    }
    $valid = [pscustomobject]@{
      Pid = 4242
      ProcessPath = $Python
      Owner = $owner
      Identity = $identity
    }
    if (-not (Test-OwnedAutoDy $valid "1.4.3")) { throw "valid AutoDy identity was rejected" }
    $wrongVersion = [pscustomobject]@{
      Pid = 4242
      ProcessPath = $Python
      Owner = $owner
      Identity = [pscustomobject]@{
        application = "AutoDy"
        version = "1.4.1"
        project_path = $DataRoot
        package_path = $PackagePath
        python_executable = $Python
      }
    }
    $wrongOwner = [pscustomobject]@{
      Pid = 4242
      ProcessPath = $Python
      Owner = "OTHER\\User"
      Identity = $identity
    }
    $malformed = [pscustomobject]@{
      Pid = 4242
      ProcessPath = $Python
      Owner = $owner
      Identity = [pscustomobject]@{ application = "AutoDy"; version = $null }
    }
    if (Test-OwnedAutoDy $wrongVersion "1.4.3") { throw "stale identity was reused" }
    if (Test-OwnedAutoDy $wrongOwner "1.4.3") { throw "another user identity was reused" }
    if (Test-OwnedAutoDy $malformed "1.4.3") { throw "malformed identity was reused" }
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


def test_tray_exit_does_not_change_scheduled_tasks_or_user_data():
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")

    exit_tray = text[text.index("$exitTray.add_Click"):text.index("$exitStop.add_Click")]
    assert "Stop-ManagedService" not in exit_tray
    assert "Unregister-ScheduledTask" not in text
    assert "Remove-Item" not in text


def test_tray_restart_and_exit_labels_map_to_their_real_actions():
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")

    restart = text[text.index("$restart.add_Click"):text.index("$repair.add_Click")]
    hide = text[text.index("$exitTray.add_Click"):text.index("$exitStop.add_Click")]
    stop = text[text.index("$exitStop.add_Click"):text.index("$timer =")]

    assert "Stop-ManagedService" in restart
    assert "Start-Or-ReuseService" in restart
    assert "Stop-ManagedService" not in hide
    assert "Stop-ManagedService" in stop


def test_desktop_launcher_starts_the_tray_host():
    text = Path("scripts/start-dashboard.ps1").read_text(encoding="utf-8-sig")

    assert "autody-tray.ps1" in text
    assert "-ProjectRoot" in text


def test_tray_cold_start_opens_a_local_wait_page_before_health_and_reuses_the_tab(
    tmp_path: Path,
):
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")
    service = text[
        text.index("function Start-Or-ReuseService"):
        text.index("function Wait-ForExistingHealthyService")
    ]

    assert "function Wait-ForExistingHealthyService" in text
    assert "function Show-ExistingDashboard" in text
    assert "function Open-VerifiedDashboard" in text
    assert service.index("Start-Process -FilePath $Python") < service.index("& $OnColdStart")
    assert service.index("& $OnColdStart") < service.index("for ($attempt = 0; $attempt -lt 40; $attempt++)")
    assert "Start-Or-ReuseService -OnColdStart" in text
    assert "Wait-ForExistingHealthyService | Out-Null" in text
    assert text.count("Start-Process $Url") == 1
    assert "SelectionItemPattern" in text
    assert "$open.add_Click({ Invoke-DashboardOpenAsync })" in text
    startup = text[text.index("try {", text.index("$timer.add_Tick")):text.index("[Windows.Forms.Application]::Run")]
    assert "Open-VerifiedDashboard" in startup

    script_path = Path("scripts/autody-tray.ps1").resolve()
    wait_path = tmp_path / "startup-wait.html"
    command = r"""
    $ErrorActionPreference = "Stop"
    . $env:AUTODY_TEST_TRAY_SCRIPT -DefineOnly -ProjectRoot $env:AUTODY_TEST_PROJECT_ROOT -DataRoot $env:AUTODY_TEST_DATA_ROOT
    $actual = New-StartupWaitPage -DashboardUrl "http://127.0.0.1:8777" -Destination $env:AUTODY_TEST_WAIT_PAGE
    if ($actual -ne $env:AUTODY_TEST_WAIT_PAGE) { throw "unexpected wait-page path" }
    """
    test_env = os.environ.copy()
    test_env.update({
        "AUTODY_TEST_TRAY_SCRIPT": str(script_path),
        "AUTODY_TEST_PROJECT_ROOT": str(Path.cwd()),
        "AUTODY_TEST_DATA_ROOT": str(tmp_path / "data"),
        "AUTODY_TEST_WAIT_PAGE": str(wait_path),
    })
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Sta", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=test_env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    html = wait_path.read_text(encoding="utf-8")
    for token in [
        "AutoDy 正在启动…",
        "http://127.0.0.1:8777",
        "location.replace(dashboardUrl)",
        "setTimeout(poll, 250)",
        "30000",
        "重试",
        "打开日志",
    ]:
        assert token in html


def test_duplicate_tray_launch_opens_dashboard_without_starting_another_host_or_service():
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")

    duplicate_start = text.index("if (-not $createdNew)")
    duplicate = text[duplicate_start:text.index("\nAdd-Type -AssemblyName System.Windows.Forms", text.index("exit 0", duplicate_start))]
    assert "Start-Or-ReuseService" not in duplicate
    assert "Open-VerifiedDashboard -ReuseOnly" in duplicate
    assert "exit 0" in duplicate
    opener = text[text.index("function Open-VerifiedDashboard"):text.index("function Get-TrayState")]
    assert "Wait-ForExistingHealthyService" in opener


def test_stop_existing_tray_targets_only_the_exact_installed_script(tmp_path: Path):
    fake_root = tmp_path / "fake install"
    fake_scripts = fake_root / "scripts"
    fake_scripts.mkdir(parents=True)
    fake_tray = fake_scripts / "autody-tray.ps1"
    fake_tray.write_text("Start-Sleep -Seconds 30\n", encoding="utf-8")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    target = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-File", str(fake_tray)],
        creationflags=creation_flags,
    )
    unrelated = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-Command", "Start-Sleep -Seconds 30"],
        creationflags=creation_flags,
    )
    controller = None
    try:
        controller = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-File",
                str(Path("scripts/autody-tray.ps1").resolve()),
                "-ProjectRoot",
                str(fake_root),
                "-DataRoot",
                str(tmp_path / "data"),
                "-StopExisting",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
        )
        _stdout, stderr = controller.communicate(timeout=15)

        assert controller.returncode == 0, stderr
        target.wait(timeout=5)
        assert unrelated.poll() is None
    finally:
        for process in (controller, target, unrelated):
            if process is None:
                continue
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


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


def test_tray_left_click_is_async_and_double_click_is_not_required():
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")

    assert "$notify.add_MouseClick" in text
    assert "Windows.Forms.MouseButtons]::Left" in text
    assert "Invoke-DashboardOpenAsync" in text
    assert "-OpenDashboardOnly" in text
    assert "-WindowStyle Hidden" in text
    assert "$notify.add_DoubleClick" not in text
    assert "$notify.ContextMenuStrip = $menu" in text
    assert "TotalMilliseconds -lt 700" in text


def test_tray_menu_theme_helpers_execute_in_powershell():
    script_path = Path("scripts/autody-tray.ps1").resolve()
    command = r"""
    $ErrorActionPreference = "Stop"
    . $env:AUTODY_TEST_TRAY_SCRIPT -DefineOnly
    Add-Type -AssemblyName System.Windows.Forms
    $menu = New-Object System.Windows.Forms.ContextMenuStrip
    [void]$menu.Items.Add("测试项目")
    Set-AutoDyMenuTheme -Menu $menu
    if ($menu.Renderer.GetType().Name -ne "AutoDyMenuRenderer") {
      throw "custom tray renderer was not installed"
    }
    if ($menu.Font.Name -ne "Segoe UI") {
      throw "tray menu font is not Segoe UI"
    }
    if ($menu.Padding.Left -lt 4) {
      throw "tray menu padding is too small"
    }
    if ($menu.Items[0].Padding.Top -lt 3) {
      throw "tray item padding is too small"
    }
    $script:LaunchCount = 0
    function Start-Process { $script:LaunchCount += 1 }
    Invoke-DashboardOpenAsync
    Invoke-DashboardOpenAsync
    if ($script:LaunchCount -ne 1) {
      throw "rapid tray activation was not deduplicated"
    }
    """
    test_env = os.environ.copy()
    test_env["AUTODY_TEST_TRAY_SCRIPT"] = str(script_path)
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Sta", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=test_env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_tray_icon_contains_nonblank_high_occupancy_frames():
    icon = Image.open("assets/icons/autody.ico")
    required_sizes = {(16, 16), (20, 20), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)}
    available = set(icon.ico.sizes())

    assert required_sizes <= available
    for size in required_sizes:
        frame = icon.ico.getimage(size).convert("RGBA")
        alpha = frame.getchannel("A")
        bounds = alpha.getbbox()
        assert bounds is not None
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        assert width / size[0] >= 0.68
        assert height / size[1] >= 0.68
        assert alpha.getextrema()[1] == 255
