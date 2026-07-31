from pathlib import Path


def test_installer_reuses_or_creates_environment_browser_and_shortcut():
    text = Path("scripts/install.ps1").read_text(encoding="utf-8-sig")
    for token in [
        "Test-VirtualEnvironment",
        "New-ProjectVirtualEnvironment",
        "Invoke-NativeChecked",
        '"pip", "install", "-e", "."',
        '"playwright", "install", "chromium"',
        "install-shortcut.ps1",
        "config.example.yaml",
    ]:
        assert token in text

    for token in ["AUTODY_HOME", "PLAYWRIGHT_BROWSERS_PATH", "PLAYWRIGHT_SKIP_BROWSER_GC"]:
        assert token in text
    launcher = Path("install.cmd").read_text(encoding="utf-8-sig")
    for token in ["AUTODY_HOME", "PLAYWRIGHT_BROWSERS_PATH", "PLAYWRIGHT_SKIP_BROWSER_GC"]:
        assert token in launcher


def test_repair_script_uses_project_local_browser_directory():
    text = Path("scripts/repair-playwright.ps1").read_text(encoding="utf-8-sig")
    for token in [
        "AUTODY_HOME",
        "PLAYWRIGHT_BROWSERS_PATH",
        "PLAYWRIGHT_SKIP_BROWSER_GC",
        "repair-playwright",
    ]:
        assert token in text


def test_dashboard_launcher_and_shortcut_are_portable_and_use_icon():
    launcher = Path("scripts/start-dashboard.cmd").read_text(encoding="utf-8-sig")
    for token in [
        "%~dp0",
        "ScriptsDir",
        "start-dashboard.ps1",
        "powershell.exe",
    ]:
        assert token in launcher

    startup = Path("scripts/start-dashboard.ps1").read_text(encoding="utf-8-sig")
    tray = Path("scripts/autody-tray.ps1").read_text(encoding="utf-8-sig")
    assert "autody-tray.ps1" in startup
    for token in [
        "AUTODY_HOME",
        "PLAYWRIGHT_BROWSERS_PATH",
        "PLAYWRIGHT_SKIP_BROWSER_GC",
        ".venv\\Scripts\\python.exe",
        "-m\", \"autody.cli\", \"ui\"",
    ]:
        assert token in tray

    shortcut = Path("scripts/install-shortcut.ps1").read_text(encoding="utf-8-sig")
    for token in [
        "WScript.Shell",
        "AutoDy Management.lnk",
        "assets\\icons\\autody.ico",
        "Set-StrictMode -Version Latest",
        "('{0},0' -f $Icon)",
    ]:
        assert token in shortcut
    assert "start-dashboard.vbs" in shortcut
    assert "wscript.exe" in shortcut
    assert "start-dashboard.cmd" not in shortcut
    assert "C:\\Users\\Administrator" not in shortcut


def test_custom_icon_exists_and_is_included_with_message_packs():
    icon = Path("assets/icons/autody.ico")
    assert icon.is_file()
    assert icon.stat().st_size > 1000
    builder = Path("scripts/build-portable.ps1").read_text(encoding="utf-8-sig")
    assert '"assets"' in builder
    assert '"message-packs"' in builder


def test_portable_builder_excludes_sensitive_data():
    text = Path("scripts/build-portable.ps1").read_text(encoding="utf-8-sig")
    for token in [".venv", "browser-profile", "avatar-cache", "discovered_friends", "account-profile", "account-profiles", "account-avatar", "screenshots", "config.yaml", "data", "node_modules"]:
        assert token in text
    assert "Compress-Archive" in text
    assert "capture-doc-screenshots.py" in text
    assert "AutoDy-Windows-Portable.zip.sha256" in text
    assert "Get-FileHash" in text
    assert "data/avatar-cache/" in Path(".gitignore").read_text(encoding="utf-8")
    assert "data/discovered_friends.json" in Path(".gitignore").read_text(encoding="utf-8")
    assert "data/account-profile.json" in Path(".gitignore").read_text(encoding="utf-8")
    assert "data/account-avatar/" in Path(".gitignore").read_text(encoding="utf-8")
    assert "data/account-profiles/" in Path(".gitignore").read_text(encoding="utf-8")


def test_portable_builder_uses_ci_python_when_project_venv_is_absent():
    text = Path("scripts/build-portable.ps1").read_text(encoding="utf-8-sig")
    assert "Get-Command python" in text
    assert "if (-not (Test-Path $Python))" in text
    assert "sys.path.insert" in text
