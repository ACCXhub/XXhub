from pathlib import Path


def test_source_installer_installs_locked_frontend_dependencies_before_build():
    text = Path("scripts/install.ps1").read_text(encoding="utf-8")

    assert 'Join-Path $frontendRoot "package-lock.json"' in text
    install_dependencies = text.index('Stage "Install source frontend dependencies"')
    production_build = text.index('Stage "Source frontend production build"')
    assert install_dependencies < production_build
    assert '-Arguments @("ci")' in text[install_dependencies:production_build]


def test_source_installer_tolerates_manual_stop_only_registration():
    text = Path("scripts/install.ps1").read_text(encoding="utf-8")

    assert '$Registration.PSObject.Properties["InstallFolder"]' in text
    assert '$Registration.PSObject.Properties["DataRoot"]' in text
    assert '$Registration.InstallFolder' not in text
    assert '$Registration.DataRoot' not in text
    assert "Ignoring incomplete AutoDy registration without install roots" in text
    assert "$Registration = $null" in text


def test_cmd_installer_forces_powershell_exceptions_to_nonzero_exit():
    text = Path("install.cmd").read_text(encoding="utf-8")

    assert 'set "AUTODY_INSTALL_PS1=%~dp0scripts\\install.ps1"' in text
    assert "try { & $env:AUTODY_INSTALL_PS1; exit 0 } catch {" in text
    assert "exit 1" in text
    assert "if errorlevel 1" in text.lower()
    assert text.index("exit 1") < text.index("echo [SUCCESS] AutoDy installation completed.")
