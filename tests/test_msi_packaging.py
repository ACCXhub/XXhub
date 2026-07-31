from pathlib import Path


def test_wix_project_is_sdk_style_and_per_user():
    project = Path("packaging/wix/AutoDy.Package.wixproj").read_text(encoding="utf-8")
    product = Path("packaging/wix/Product.wxs").read_text(encoding="utf-8")

    assert 'Sdk="WixToolset.Sdk/7.0.0"' in project
    assert 'Scope="perUser"' in product
    assert 'Id="LocalAppDataFolder"' in product
    assert 'Id="ProgramsFolder" Name="Programs"' in product
    assert 'Id="INSTALLFOLDER" Name="AutoDy"' in product
    assert 'Id="AUTODYDATAROOT" Name="AutoDy"' in product
    assert 'Id="PreserveDataRoot"' in product
    assert 'Permanent="yes"' in product
    assert "<MajorUpgrade" in product
    assert "<SuppressIces>ICE60;ICE91</SuppressIces>" in project


def test_msi_shortcuts_use_the_hidden_launcher():
    product = Path("packaging/wix/Product.wxs").read_text(encoding="utf-8")

    assert 'Target="[SystemFolder]wscript.exe"' in product
    assert "scripts\\start-dashboard.vbs" in product
    assert 'Id="DesktopShortcut"' in product
    assert 'Id="StartMenuShortcut"' in product


def test_msi_builder_uses_explicit_allowlist_and_clean_runtime():
    builder = Path("scripts/build-msi.ps1").read_text(encoding="utf-8-sig")

    for token in [
        "$releaseFiles = [ordered]@{",
        "runtime\\python\\python.exe",
        "runtime\\ms-playwright",
        "packaging\\runtime-requirements.txt",
        "AutoDy-Test-Center.autody-module.zip",
        "EmbeddedPythonSha256",
        "Get-FileHash",
        "AcceptEula=wix7",
        "WixToolset.Sdk/7.0.0",
    ]:
        if token == "WixToolset.Sdk/7.0.0":
            assert token in Path("packaging/wix/AutoDy.Package.wixproj").read_text(
                encoding="utf-8"
            )
        else:
            assert token in builder
    for token in [
        r"config\.yaml",
        r"messages\.txt",
        "account-profiles",
        "browser-profile",
        "screenshots",
        "backups",
    ]:
        assert token in builder
    assert "Copy-Item -LiteralPath $Root -Recurse" not in builder
    assert "Test-FileContainsMappedPattern" in builder
    assert "[IO.File]::ReadAllBytes" not in builder
    assert "SHA256]::HashData" not in builder
    assert "[Convert]::ToHexString" not in builder


def test_generated_payload_components_use_hkcu_registry_keypaths():
    builder = Path("scripts/build-msi.ps1").read_text(encoding="utf-8-sig")

    assert "function Get-StableGuid" in builder
    assert '$writer.WriteAttributeString("Guid", (Get-StableGuid $relative))' in builder
    assert '"Root", "HKCU"' in builder
    assert '"Key", "Software\\AutoDy\\Installer\\Components"' in builder
    assert '"KeyPath", "yes"' in builder
    assert '"Id", "PayloadDirectoryCleanup"' in builder
    assert '"Id", "RemoveInstallFolder"' in builder
    assert '"Id", "RemoveProgramsFolderIfEmpty"' in builder
    assert '"On", "uninstall"' in builder
    assert '$writer.WriteAttributeString("KeyPath", "yes")' in builder
    assert (
        '$writer.WriteAttributeString("Source", $file.FullName)\n'
        '        $writer.WriteEndElement()\n'
        '        $writer.WriteStartElement("RegistryValue")'
    ) in builder


def test_msi_runtime_dependencies_are_pinned():
    requirements = Path("packaging/runtime-requirements.txt").read_text(
        encoding="utf-8"
    ).splitlines()

    assert requirements
    assert all("==" in line for line in requirements)
    assert any(line.startswith("playwright==") for line in requirements)


def test_release_privacy_verifier_covers_all_release_artifacts():
    verifier = Path("scripts/verify-release-artifacts.ps1").read_text(
        encoding="utf-8-sig"
    )

    for token in [
        "WindowsInstaller.Installer",
        "SELECT `File`,`FileName` FROM `File`",
        "SELECT `Shortcut`,`Name`,`Target`,`Arguments` FROM `Shortcut`",
        "FROM ``_Tables`` WHERE ``Name`` = 'CustomAction'",
        '"/a `"$Msi`" /qn',
        "AutoDy-Windows-Portable.zip",
        "AutoDy-Test-Center.autody-module.zip",
        "release-privacy-report.json",
        "release-privacy-report.md",
        "Test-FileContainsMappedPattern",
        "Test-ForbiddenEntryPath",
    ]:
        assert token in verifier


def test_msi_lifecycle_verifier_preserves_shortcuts_and_data():
    verifier = Path("scripts/verify-msi-lifecycle.ps1").read_text(
        encoding="utf-8-sig"
    )

    for token in [
        '"/i `"$Msi`""',
        '"/fa $ProductCode"',
        '"/x $ProductCode"',
        "Assert-Shortcut",
        "Get-DataSnapshot",
        "Test-SnapshotsEqual",
        "shortcutBackups",
        "not_testable_no_prior_msi_baseline",
        "msi-lifecycle-report.json",
    ]:
        assert token in verifier


def test_ci_restores_wix_sdk_and_parses_release_scripts():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "dotnet restore .\\packaging\\wix\\AutoDy.Package.wixproj" in workflow
    assert "-p:AcceptEula=wix7" in workflow
    assert ".\\scripts\\build-msi.ps1" in workflow
    assert ".\\scripts\\verify-msi-lifecycle.ps1" in workflow
    assert ".\\scripts\\verify-release-artifacts.ps1" in workflow
