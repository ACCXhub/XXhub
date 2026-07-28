from pathlib import Path


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
