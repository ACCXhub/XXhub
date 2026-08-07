from pathlib import Path
import xml.etree.ElementTree as ET


def test_wix_project_is_sdk_style_and_per_user():
    project = Path("packaging/wix/AutoDy.Package.wixproj").read_text(encoding="utf-8")
    product = Path("packaging/wix/Product.wxs").read_text(encoding="utf-8")

    assert 'Sdk="WixToolset.Sdk/7.0.0"' in project
    assert 'Scope="perUser"' in product
    assert 'Codepage="65001"' in product
    assert 'Id="LocalAppDataFolder"' in product
    assert 'Id="ProgramsFolder" Name="Programs"' in product
    assert 'Id="INSTALLFOLDER" Name="AutoDy"' in product
    assert 'Id="AUTODYDATAROOT" Name="AutoDy"' in product
    assert 'Id="PreserveDataRoot"' in product
    assert 'Permanent="yes"' in product
    assert "<MajorUpgrade" in product
    assert 'PackageReference Include="WixToolset.UI.wixext" Version="7.0.0"' in project
    assert 'PackageReference Include="WixToolset.Util.wixext" Version="7.0.0"' in project
    assert 'xmlns:ui="http://wixtoolset.org/schemas/v4/wxs/ui"' in product
    assert 'ui:WixUI Id="WixUI_InstallDir"' in product
    assert 'InstallDirectory="INSTALLFOLDER"' in product
    assert 'Id="WixUILicenseRtf"' in product
    assert 'Id="InstalledFolderSearch"' in product
    assert 'Value="Z:\\__AutoDyNoExistingInstall__"' in product
    assert 'Name="InstallFolder"' in product
    assert 'Value="[INSTALLFOLDER]"' in product
    assert "<SuppressIces>ICE60;ICE91</SuppressIces>" in project


def test_msi_shortcuts_use_the_hidden_launcher():
    product = Path("packaging/wix/Product.wxs").read_text(encoding="utf-8")

    assert 'Target="[SystemFolder]wscript.exe"' in product
    assert "scripts\\start-dashboard.vbs" in product
    assert 'Id="DesktopShortcut"' in product
    assert 'Id="StartMenuShortcut"' in product


def test_msi_stops_only_verified_existing_tray_hosts_after_installing_files():
    namespace = {"w": "http://wixtoolset.org/schemas/v4/wxs"}
    root = ET.parse("packaging/wix/Product.wxs").getroot()

    action = root.find(".//w:CustomAction[@Id='StopExistingAutoDyTray']", namespace)
    assert action is not None
    assert action.attrib["Directory"] == "INSTALLFOLDER"
    assert "scripts\\autody-tray.ps1" in action.attrib["ExeCommand"]
    assert "-StopExisting" in action.attrib["ExeCommand"]
    assert "-ProjectRoot" not in action.attrib["ExeCommand"]
    assert "-DataRoot" not in action.attrib["ExeCommand"]
    assert action.attrib["Execute"] == "deferred"
    assert action.attrib["Impersonate"] == "yes"

    scheduled = root.find(
        ".//w:InstallExecuteSequence/w:Custom[@Action='StopExistingAutoDyTray']",
        namespace,
    )
    assert scheduled is not None
    assert scheduled.attrib["After"] == "InstallFiles"
    assert scheduled.attrib["Condition"] == 'NOT REMOVE~="ALL"'


def test_msi_uninstall_shortcut_and_install_folder_resolution_are_msi_native():
    product = Path("packaging/wix/Product.wxs").read_text(encoding="utf-8")

    assert 'Id="UninstallShortcut"' in product
    assert 'Name="卸载 AutoDy"' in product
    assert 'Target="[SystemFolder]msiexec.exe"' in product
    assert 'Arguments="/x [ProductCode]"' in product
    assert 'Id="InstalledFolderSearch"' in product
    assert 'Id="ExistingInstallFolderSearch"' in product
    assert 'Path="[AUTODY_REGISTERED_INSTALLFOLDER]"' in product
    assert 'Id="AutoDyDDriveSearch"' in product
    assert 'Path="D:\\"' in product
    assert 'Value="D:\\AutoDy"' in product
    assert 'After="AppSearch"' in product
    assert 'Sequence="both"' in product
    assert 'Condition="AUTODY_EXISTING_INSTALLFOLDER"' in product
    assert 'Condition="NOT INSTALLFOLDER AND NOT AUTODY_EXISTING_INSTALLFOLDER AND AUTODY_D_DRIVE"' in product
    assert 'Script="vbscript"' not in product
    assert not Path("packaging/wix/InstallFolderValidation.vbs").exists()


def test_msi_builder_uses_explicit_allowlist_and_clean_runtime():
    builder = Path("scripts/build-msi.ps1").read_text(encoding="utf-8-sig")

    for token in [
        "$releaseFiles = [ordered]@{",
        "scripts\\install-shortcut.ps1",
        "runtime\\python\\python.exe",
        "runtime\\ms-playwright",
        "packaging\\runtime-requirements.txt",
        "AutoDy-Test-Center.autody-module.zip",
        "EmbeddedPythonSha256",
        "Get-ReleaseFileSha256",
        "AcceptEula=wix7",
        '$previousErrorActionPreference = $ErrorActionPreference',
        '$ErrorActionPreference = "Continue"',
        "$nativeExitCode = $LASTEXITCODE",
        '"--upgrade", "--no-deps", "--target", $sitePackages, $wheel.FullName',
        "WixToolset.Sdk/7.0.0",
        "WixToolset.Util.wixext.dll",
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
    assert '"util", "RemoveFolderEx", "http://wixtoolset.org/schemas/v4/wxs/util"' in builder
    assert '"Property", "INSTALLFOLDER"' in builder
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
        "UninstallShortcut",
        "Wix4RemoveFoldersEx_X64",
        "SetExistingInstallFolder",
        "SetDDriveInstallFolder",
        '"/a `"$Msi`" /qn',
        "AutoDy-Windows-Portable-$Version.zip",
        "AutoDy-Test-Center.autody-module.zip",
        "release-privacy-report.json",
        "release-privacy-report.md",
        "Test-FileContainsMappedPattern",
        "Test-PrivacyTextFile",
        "Test-ForbiddenEntryPath",
    ]:
        assert token in verifier


def test_msi_lifecycle_verifier_preserves_shortcuts_and_data():
    verifier = Path("scripts/verify-msi-lifecycle.ps1").read_text(
        encoding="utf-8-sig"
    )

    for token in [
        '"/i `"$Msi`""',
        'INSTALLFOLDER=`"$CustomInstallRoot`"',
        '"/fa $ProductCode"',
        '"/x $ProductCode"',
        "Assert-MsiUi",
        '"WelcomeDlg"',
        '"InstallDirDlg"',
        '"VerifyReadyDlg"',
        '"ProgressDlg"',
        '"ExitDialog"',
        '"CancelDlg"',
        '"SetTargetPath"',
        '"SpawnDialog"',
        '"Install previous-version baseline"',
        '"Major upgrade"',
        "Assert-Shortcut",
        "Assert-UninstallShortcut",
        "卸载 AutoDy.lnk",
        '"/x $ExpectedProductCode"',
        "Get-DataSnapshot",
        "Test-SnapshotsEqual",
        "D:\\AutoDy",
        "existing AutoDy registration",
        "shortcutBackups",
        "prior_installation_restoration",
        "msi-lifecycle-report.json",
    ]:
        assert token in verifier


def test_ci_restores_wix_sdk_and_parses_release_scripts():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert ".\\scripts\\bootstrap-source.ps1" in workflow
    assert ".\\scripts\\build-release-from-clean-source.ps1" in workflow
    assert ".\\scripts\\verify-msi-lifecycle.ps1" in workflow
    assert ".\\scripts\\verify-release-artifacts.ps1" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "actions/download-artifact@v4" in workflow
    assert "clean-windows-acceptance" in workflow


def test_release_workflow_publishes_only_versioned_public_assets():
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "AUTODY_RELEASE_VERSION: \"1.4.2\"" in workflow
    assert ".\\scripts\\build-release-from-clean-source.ps1" in workflow
    assert "write-release-manifest.ps1" not in workflow.split(
        "Publish only canonical guarded assets", 1
    )[1]
    for asset in [
        "output/release/v1.4.2/AutoDy-1.4.2-x64.msi",
        "output/release/v1.4.2/AutoDy-1.4.2-x64.msi.sha256",
        "output/release/v1.4.2/AutoDy-Windows-Portable-1.4.2.zip",
        "output/release/v1.4.2/AutoDy-Windows-Portable-1.4.2.zip.sha256",
        "output/release/v1.4.2/release-manifest.json",
    ]:
        assert asset in workflow
    published_files = workflow.split("files: |", 1)[1].split(
        "body_path:", 1
    )[0]
    assert "AutoDy-Test-Center" not in published_files
