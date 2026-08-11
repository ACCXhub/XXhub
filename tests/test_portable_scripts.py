import json
import os
from pathlib import Path
import subprocess


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


def test_repair_script_uses_shared_runtime_context():
    text = Path("scripts/repair-playwright.ps1").read_text(encoding="utf-8-sig")
    for token in [
        "Resolve-AutoDyLaunchContext",
        "AUTODY_HOME",
        "AUTODY_PROGRAM_ROOT",
        "AUTODY_BROWSERS_PATH",
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
        "AUTODY_PROGRAM_ROOT",
        "AUTODY_BROWSERS_PATH",
        "PLAYWRIGHT_BROWSERS_PATH",
        "PLAYWRIGHT_SKIP_BROWSER_GC",
        "Resolve-AutoDyLaunchContext",
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
    assert "C:\\Users\\" not in shortcut


def test_custom_icon_exists_and_is_included_with_message_packs():
    icon = Path("assets/icons/autody.ico")
    assert icon.is_file()
    assert icon.stat().st_size > 1000
    builder = Path("scripts/build-msi.ps1").read_text(encoding="utf-8-sig")
    assert '"assets\\icons\\autody.ico"' in builder
    assert '"message-packs\\index.json"' in builder


def test_portable_builder_excludes_sensitive_data():
    text = Path("scripts/build-portable.ps1").read_text(encoding="utf-8-sig")
    for token in [".venv", "browser-profile", "avatar-cache", "discovered_friends", "account-profile", "account-profiles", "account-avatar", "screenshots", "config.yaml", "data", "node_modules"]:
        assert token in text
    assert "New-ReproducibleZipFromDirectory" in text
    assert "Convert-ReleaseTextFilesToLf" in text
    assert "src" in text
    assert "runtime\\python\\python.exe" in text
    assert "runtime\\ms-playwright" in text
    assert "distribution-mode.txt" in text
    assert '"portable"' in text
    assert "StageOnly" in text
    assert "build-msi.ps1" in text
    assert "verify-msi-lifecycle.ps1" in text
    assert "verify-release-artifacts.ps1" in text
    assert '"AutoDy-Windows-Portable-$Version.zip"' in text
    assert '"$ArchiveName.sha256"' in text
    assert "Get-ReleaseFileSha256" in text
    assert "data/avatar-cache/" in Path(".gitignore").read_text(encoding="utf-8")
    assert "data/discovered_friends.json" in Path(".gitignore").read_text(encoding="utf-8")
    assert "data/account-profile.json" in Path(".gitignore").read_text(encoding="utf-8")
    assert "data/account-avatar/" in Path(".gitignore").read_text(encoding="utf-8")
    assert "data/account-profiles/" in Path(".gitignore").read_text(encoding="utf-8")


def test_portable_builder_reuses_the_msi_standalone_runtime_authority():
    text = Path("scripts/build-portable.ps1").read_text(encoding="utf-8-sig")
    assert "SharedRuntimeStage" in text
    assert "build-msi.ps1" in text
    assert "-StageOnly" in text
    assert "Get-Command python" not in text


def test_runtime_context_distinguishes_source_and_portable_data_roots(tmp_path):
    resolver = Path("scripts/resolve-runtime-roots.ps1").resolve()
    source_root = tmp_path / "source"
    portable_root = tmp_path / "portable"
    (source_root / ".venv" / "Scripts").mkdir(parents=True)
    (portable_root / "runtime" / "python").mkdir(parents=True)
    (portable_root / "runtime" / "ms-playwright").mkdir(parents=True)
    (portable_root / "runtime" / "distribution-mode.txt").write_text(
        "portable", encoding="ascii"
    )
    env = os.environ.copy()
    env.update(
        {
            "AUTODY_TEST_RESOLVER": str(resolver),
            "AUTODY_TEST_SOURCE": str(source_root),
            "AUTODY_TEST_PORTABLE": str(portable_root),
        }
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            r"""
            $ErrorActionPreference = 'Stop'
            . $env:AUTODY_TEST_RESOLVER
            $source = Resolve-AutoDyLaunchContext -ProgramRoot $env:AUTODY_TEST_SOURCE
            $portable = Resolve-AutoDyLaunchContext -ProgramRoot $env:AUTODY_TEST_PORTABLE
            [pscustomobject]@{ source = $source; portable = $portable } |
                ConvertTo-Json -Depth 4 -Compress
            """,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["source"]["DistributionMode"] == "source"
    assert Path(result["source"]["DataRoot"]) == source_root.resolve()
    assert Path(result["source"]["Python"]) == (
        source_root / ".venv" / "Scripts" / "python.exe"
    ).resolve()
    assert result["portable"]["DistributionMode"] == "portable"
    assert Path(result["portable"]["DataRoot"]) == portable_root.resolve()
    assert Path(result["portable"]["Python"]) == (
        portable_root / "runtime" / "python" / "python.exe"
    ).resolve()
    assert Path(result["portable"]["BrowserRoot"]) == (
        portable_root / "runtime" / "ms-playwright"
    ).resolve()


def test_runtime_registration_reader_tolerates_partial_preserved_key():
    resolver = Path("scripts/resolve-runtime-roots.ps1").read_text(
        encoding="utf-8-sig"
    )
    registration = resolver.split("function Get-AutoDyDistributionMode", 1)[0]
    assert 'PSObject.Properties["InstallFolder"]' in registration
    assert 'PSObject.Properties["DataRoot"]' in registration
    assert ".InstallFolder" not in registration
    assert ".DataRoot" not in registration


def test_ci_installs_playwright_into_the_test_runtime():
    text = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    bootstrap = Path("scripts/bootstrap-source.ps1").read_text(encoding="utf-8-sig")
    assert ".\\scripts\\bootstrap-source.ps1" in text
    assert "PLAYWRIGHT_BROWSERS_PATH" in bootstrap
    assert "PLAYWRIGHT_SKIP_BROWSER_GC" in bootstrap
    assert "'playwright', 'install', 'chromium'" in bootstrap
