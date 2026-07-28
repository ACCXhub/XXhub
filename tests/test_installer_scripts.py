from pathlib import Path
import subprocess


SCRIPTS = sorted(Path("scripts").glob("*.ps1"))


def test_tracked_powershell_scripts_have_no_parser_errors():
    command = r'''
    $ErrorActionPreference = "Stop"
    $failed = @()
    Get-ChildItem -LiteralPath scripts -Filter *.ps1 | ForEach-Object {
      $tokens = $null; $errors = $null
      [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errors) | Out-Null
      if ($errors.Count) { $failed += "$($_.Name): $($errors.Message -join '; ')" }
    }
    if ($failed.Count) { $failed | Write-Error; exit 1 }
    '''
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_installer_reuses_valid_environment_and_checks_native_stages():
    text = Path("scripts/install.ps1").read_text(encoding="utf-8")

    assert "function Test-VirtualEnvironment" in text
    assert "function Invoke-NativeChecked" in text
    assert "Reusing existing virtual environment" in text
    assert "Create virtual environment" in text
    assert "Invoke-NativeChecked -Stage \"Install editable package\"" in text
    assert "Invoke-NativeChecked -Stage \"Install Chromium\"" in text
    assert "function Update-FrontendBuild" in text
    assert "Source frontend production build" in text
    assert "Stop-ProjectAutoDyService" in text
    assert "Get-ScheduledTask" in text
    assert "taskkill /IM python.exe" not in text
    assert "py -3.11 -m venv .venv" not in text
    assert "python -m venv .venv" not in text


def test_shortcut_installer_uses_tracked_launcher_and_safe_icon_formatting():
    text = Path("scripts/install-shortcut.ps1").read_text(encoding="utf-8")

    assert "Set-StrictMode -Version Latest" in text
    assert "$PSScriptRoot" in text
    assert "scripts\\start-dashboard.cmd" in text
    assert "('{0},0' -f $Icon)" in text
    assert '"$Icon,0"' not in text
    assert "Test-Path -LiteralPath $ShortcutPath" in text
    assert all(byte < 128 for byte in Path("scripts/install-shortcut.ps1").read_bytes())


def test_top_level_installer_propagates_powershell_failure():
    text = Path("install.cmd").read_text(encoding="utf-8")

    assert "if errorlevel 1" in text.lower()
    assert "exit /b %ExitCode%" in text


def test_dashboard_launcher_reuses_an_identified_project_service():
    text = Path("scripts/start-dashboard.cmd").read_text(encoding="utf-8")

    assert "set \"ScriptsDir=%~dp0\"" in text
    assert "start-dashboard.ps1" in text
    assert all(byte < 128 for byte in Path("scripts/start-dashboard.cmd").read_bytes())


def test_dashboard_powershell_launcher_waits_for_identity_and_preserves_failures():
    launcher = Path("scripts/start-dashboard.ps1").read_text(encoding="utf-8")
    text = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")

    assert "autody-tray.ps1" in launcher
    assert "Set-StrictMode -Version Latest" in launcher
    assert "/api/service-identity" in text
    assert "Start-Process -FilePath $Python" in text
    assert "Stop-Process -Id $ManagedPid" in text
    assert "MessageBox" in text
    assert "Start-Process $Url" in text
