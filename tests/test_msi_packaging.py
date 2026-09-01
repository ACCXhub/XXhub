from pathlib import Path
import xml.etree.ElementTree as ET


def test_wix_project_is_sdk_style_and_per_machine():
    project = Path("packaging/wix/AutoDy.Package.wixproj").read_text(encoding="utf-8")
    product = Path("packaging/wix/Product.wxs").read_text(encoding="utf-8")

    assert 'Sdk="WixToolset.Sdk/7.0.0"' in project
    assert 'Scope="perMachine"' in product
    assert 'Codepage="65001"' in product
    assert 'Id="LocalAppDataFolder"' in product
    assert 'Id="ProgramsFolder" Name="Programs"' in product
    assert 'Id="INSTALLFOLDER" Name="AutoDy"' in product
    assert 'Id="AUTODYDATAROOT" Name="AutoDy"' in product
    assert 'Id="PreserveDataRoot"' in product
    assert 'Permanent="yes"' in product
    assert "<MajorUpgrade" in product
    for version, product_code in {
        "140": "{BA1EC9E1-105F-87D6-ADB2-BFC533517E95}",
        "141": "{ED630967-103C-0CB8-7CD3-565B67B27629}",
        "142": "{F2845514-95E6-787A-7B1A-5679988B63AE}",
    }.items():
        assert f'Id="AUTODY_LEGACY_PERUSER_{version}"' in product
        assert f"Uninstall\\{product_code}" in product
    assert "A per-user AutoDy 1.4.0-1.4.2 installation was found" in product
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
    assert "<SuppressIces>ICE60</SuppressIces>" in project


def test_main_feature_is_always_installed_locally():
    namespace = {"w": "http://wixtoolset.org/schemas/v4/wxs"}
    root = ET.parse("packaging/wix/Product.wxs").getroot()

    feature = root.find(".//w:Feature[@Id='MainFeature']", namespace)

    assert feature is not None
    assert feature.attrib["Level"] == "1"
    assert feature.attrib["AllowAdvertise"] == "no"


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


def test_msi_upgrade_repairs_scheduler_from_canonical_config_and_roots():
    namespace = {"w": "http://wixtoolset.org/schemas/v4/wxs"}
    root = ET.parse("packaging/wix/Product.wxs").getroot()

    properties = {
        item.attrib["Id"]: item
        for item in root.findall(".//w:Property", namespace)
    }
    assert properties["AUTODY_INTERACTIVE_USER_SID"].attrib["Secure"] == "yes"
    assert properties["AUTODY_INTERACTIVE_LOCALAPPDATA"].attrib["Secure"] == "yes"

    action = root.find(
        ".//w:CustomAction[@Id='RepairInstalledAutoDyTasks']",
        namespace,
    )
    assert action is not None
    command = next(
        item.attrib["Value"]
        for item in root.findall(".//w:CustomAction", namespace)
        if item.attrib.get("Property") == "RepairInstalledAutoDyTasks"
    )
    assert "runtime\\python\\python.exe" in command
    assert 'python.exe" -B -m autody.cli repair-scheduler' in command
    assert "repair-scheduler" in command
    assert "[AUTODYDATAROOT]config.yaml" in command
    assert '--program-root "[INSTALLFOLDER]."' in command
    assert '--program-root "[INSTALLFOLDER]"' not in command
    assert '--data-root "[AUTODYDATAROOT]."' in command
    assert '--task-user-id "[AUTODY_INTERACTIVE_USER_SID]"' in command
    assert "--if-config-exists" not in command
    assert action.attrib["BinaryRef"] == "Wix4UtilCA_X64"
    assert action.attrib["DllEntry"] == "WixQuietExec"
    assert "ExeCommand" not in action.attrib
    assert action.attrib["Execute"] == "deferred"
    assert action.attrib["Impersonate"] == "no"
    assert action.attrib["Return"] == "check"

    scheduled = root.find(
        ".//w:InstallExecuteSequence/w:Custom[@Action='RepairInstalledAutoDyTasks']",
        namespace,
    )
    assert scheduled is not None
    assert scheduled.attrib["After"] == "RollbackFreshInstallAutoDyTasks"
    assert scheduled.attrib["Condition"] == 'NOT REMOVE~="ALL"'
    data_action = root.find(
        ".//w:InstallExecuteSequence/w:Custom[@Action='SetRepairInstalledAutoDyTasksData']",
        namespace,
    )
    assert data_action is not None
    assert data_action.attrib["After"] == "StopExistingAutoDyTray"


def test_msi_rolls_back_tasks_only_for_failed_fresh_install():
    namespace = {"w": "http://wixtoolset.org/schemas/v4/wxs"}
    root = ET.parse("packaging/wix/Product.wxs").getroot()

    rollback = root.find(
        ".//w:CustomAction[@Id='RollbackFreshInstallAutoDyTasks']",
        namespace,
    )
    assert rollback is not None
    assert rollback.attrib["BinaryRef"] == "Wix4UtilCA_X64"
    assert rollback.attrib["DllEntry"] == "WixQuietExec"
    assert rollback.attrib["Execute"] == "rollback"
    assert rollback.attrib["Impersonate"] == "no"
    assert rollback.attrib["Return"] == "ignore"

    command = next(
        item.attrib["Value"]
        for item in root.findall(".//w:CustomAction", namespace)
        if item.attrib.get("Property") == "RollbackFreshInstallAutoDyTasks"
    )
    assert "WindowsPowerShell\\v1.0\\powershell.exe" in command
    assert "scripts\\remove-task.ps1" in command

    scheduled = root.find(
        ".//w:InstallExecuteSequence/w:Custom[@Action='RollbackFreshInstallAutoDyTasks']",
        namespace,
    )
    assert scheduled is not None
    condition = scheduled.attrib["Condition"]
    assert "NOT Installed" in condition
    assert "NOT WIX_UPGRADE_DETECTED" in condition
    assert 'NOT REMOVE~="ALL"' in condition

    repair = root.find(
        ".//w:InstallExecuteSequence/w:Custom[@Action='RepairInstalledAutoDyTasks']",
        namespace,
    )
    assert repair is not None
    assert repair.attrib["After"] == "RollbackFreshInstallAutoDyTasks"


def test_msi_verifiers_enforce_fresh_install_scheduler_rollback_contract():
    release_verifier = Path("scripts/verify-release-artifacts.ps1").read_text(
        encoding="utf-8-sig"
    )
    lifecycle_verifier = Path("scripts/verify-msi-lifecycle.ps1").read_text(
        encoding="utf-8-sig"
    )
    condition = 'NOT Installed AND NOT WIX_UPGRADE_DETECTED AND NOT REMOVE~="ALL"'

    for verifier in (release_verifier, lifecycle_verifier):
        assert "SetRollbackFreshInstallAutoDyTasksData" in verifier
        assert "RollbackFreshInstallAutoDyTasks" in verifier
    assert condition in lifecycle_verifier
    assert "MSI Scheduler fresh-install rollback contract is invalid" in lifecycle_verifier


def test_msi_uninstall_removes_scheduler_tasks_before_program_files():
    namespace = {"w": "http://wixtoolset.org/schemas/v4/wxs"}
    root = ET.parse("packaging/wix/Product.wxs").getroot()

    action = root.find(
        ".//w:CustomAction[@Id='RemoveInstalledAutoDyTasks']",
        namespace,
    )
    assert action is not None
    command = next(
        item.attrib["Value"]
        for item in root.findall(".//w:CustomAction", namespace)
        if item.attrib.get("Property") == "RemoveInstalledAutoDyTasks"
    )
    assert "WindowsPowerShell\\v1.0\\powershell.exe" in command
    assert "scripts\\remove-task.ps1" in command
    assert action.attrib["BinaryRef"] == "Wix4UtilCA_X64"
    assert action.attrib["DllEntry"] == "WixQuietExec"
    assert action.attrib["Execute"] == "deferred"
    assert action.attrib["Impersonate"] == "no"
    assert action.attrib["Return"] == "check"

    data_action = root.find(
        ".//w:InstallExecuteSequence/w:Custom[@Action='SetRemoveInstalledAutoDyTasksData']",
        namespace,
    )
    assert data_action is not None
    assert data_action.attrib["Before"] == "RemoveInstalledAutoDyTasks"
    assert data_action.attrib["Condition"] == 'REMOVE~="ALL" AND NOT UPGRADINGPRODUCTCODE'

    scheduled = root.find(
        ".//w:InstallExecuteSequence/w:Custom[@Action='RemoveInstalledAutoDyTasks']",
        namespace,
    )
    assert scheduled is not None
    assert scheduled.attrib["Before"] == "RemoveFiles"
    assert scheduled.attrib["Condition"] == 'REMOVE~="ALL" AND NOT UPGRADINGPRODUCTCODE'


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
        "scripts\\resolve-runtime-roots.ps1",
        "scripts\\scheduled-task-launcher.vbs",
        "runtime\\python\\python.exe",
        "runtime\\ms-playwright",
        "packaging\\runtime-requirements.txt",
        "AutoDy-Test-Center.autody-module.zip",
        "EmbeddedPythonSha256",
        "distribution-mode.txt",
        "StageOnly",
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


def test_reused_msi_runtime_still_refreshes_the_current_autody_package():
    builder = Path("scripts/build-msi.ps1").read_text(encoding="utf-8-sig")
    reusable_runtime_setup = builder.index(
        '$browserRoot = Join-Path $Stage "runtime\\ms-playwright"'
    )

    assert builder.index('"Build AutoDy wheel"') > reusable_runtime_setup
    assert builder.index('"Install AutoDy runtime package"') > reusable_runtime_setup
    assert "MSI staging frontend does not match the current production build" in builder


def test_generated_payload_components_use_file_keypaths_and_hklm_cleanup_marker():
    builder = Path("scripts/build-msi.ps1").read_text(encoding="utf-8-sig")

    assert "function Get-StableGuid" in builder
    assert '$writer.WriteAttributeString("Guid", (Get-StableGuid $relative))' in builder
    assert '"Root", "HKCU"' not in builder
    assert '"Key", "Software\\AutoDy\\Installer\\Components"' not in builder
    assert '"Root", "HKLM"' in builder
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
        '        $writer.WriteAttributeString("KeyPath", "yes")\n'
        '        $writer.WriteEndElement()'
    ) in builder


def test_msi_seeds_config_without_overwriting_and_registers_original_user():
    namespace = {"w": "http://wixtoolset.org/schemas/v4/wxs"}
    root = ET.parse("packaging/wix/Product.wxs").getroot()
    config_file = root.find(".//w:File[@Id='InitialConfigFile']", namespace)
    assert config_file is not None
    assert config_file.attrib["Name"] == "config.yaml"
    component = root.find(".//w:Component[@Id='DataRootComponent']", namespace)
    assert component is not None and component.attrib["Directory"] == "AUTODYDATAROOT"
    assert component.attrib["NeverOverwrite"] == "yes"
    registrations = root.findall(".//w:RegistryValue", namespace)
    assert registrations
    assert all(item.attrib["Root"] == "HKU" for item in registrations)
    assert all(
        item.attrib["Key"].startswith("[AUTODY_INTERACTIVE_USER_SID]\\")
        for item in registrations
    )


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
        "CaptureInteractiveUserSid",
        "CaptureInteractiveLocalAppData",
        "SetAutoDyDataRoot",
        "StopExistingAutoDyTray",
        "StopExistingAutoDyForRemove",
        "SetRepairInstalledAutoDyTasksData",
        "RepairInstalledAutoDyTasks",
        "SetRemoveInstalledAutoDyTasksData",
        "RemoveInstalledAutoDyTasks",
        "WixQuietExec",
        "invalid_initial_config_seed",
        '"/a `"$Msi`" /qn',
        "AutoDy-Windows-Portable-$Version.zip",
        "AutoDy-Test-Center.autody-module.zip",
        "release-privacy-report.json",
        "release-privacy-report.md",
        "Test-FileContainsMappedPattern",
        "Test-PrivacyTextFile",
        "Test-ForbiddenEntryPath",
        "Test-InitialConfigSeed",
    ]:
        assert token in verifier
    assert "$_.Values[1] -like $expectedShortcut.Name" not in verifier


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
        '"Remove previous per-user baseline for scope migration"',
        '"Install current per-machine package after scope migration"',
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
        "Assert-ScheduledTasksPresent",
        "Assert-ScheduledTasksAbsent",
        "msi-lifecycle-report.json",
    ]:
        assert token in verifier


def test_msi_lifecycle_verifier_accepts_wix_short_and_long_config_filename():
    verifier = Path("scripts/verify-msi-lifecycle.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "$encodedConfigName.LastIndexOf('|') + 1" in verifier
    assert '$longConfigName -eq "config.yaml"' in verifier


def test_msi_lifecycle_verifier_checks_wix_quiet_exec_custom_action_pairs():
    verifier = Path("scripts/verify-msi-lifecycle.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert verifier.count('$_.Values[1] -eq "51"') == 3
    for action in [
        "RepairInstalledAutoDyTasks",
        "RollbackFreshInstallAutoDyTasks",
        "RemoveInstalledAutoDyTasks",
    ]:
        assert f'$_.Values[2] -eq "{action}"' in verifier
    assert verifier.count('$_.Values[2] -eq "Wix4UtilCA_X64"') == 3
    assert verifier.count('$_.Values[3] -eq "WixQuietExec"') == 3
    assert verifier.count('$_.Values[1] -eq "3073"') == 2
    assert '$_.Values[1] -eq "3393"' in verifier
    assert '$_.Values[3] -eq "[CustomActionData]"' not in verifier


def test_ci_runs_only_lightweight_development_validation():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert ".\\scripts\\bootstrap-source.ps1" in workflow
    assert 'branches:\n      - "**"' in workflow
    assert '-m "not release_build"' in workflow
    assert "npm test" in workflow
    assert "Parse release PowerShell scripts" in workflow
    for release_only in [
        "build-release-from-clean-source.ps1",
        "verify-msi-lifecycle.ps1",
        "verify-release-artifacts.ps1",
        "actions/upload-artifact@v4",
        "actions/download-artifact@v4",
        "clean-windows-acceptance",
        "package:",
    ]:
        assert release_only not in workflow


def test_release_workflow_publishes_only_versioned_public_assets():
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    release_script = Path("scripts/build-release-from-clean-source.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "AUTODY_RELEASE_VERSION: \"1.5.2\"" in workflow
    assert "AUTODY_PREVIOUS_VERSION: \"1.4.4\"" in workflow
    assert "23fd4811aebbcc66faa10eacd4d09788039cd5e1" in workflow
    assert "bea7a7e7495c0137d33463f504d2999dcef250e7df7766c90eb0dbcb4a1daa10" in workflow
    assert "https://github.com/ACCXhub/XXhub/releases/download/" in workflow
    assert "ACCXhub/hlhub" not in workflow
    assert ".\\scripts\\build-release-from-clean-source.ps1" in workflow
    assert "-m release_build" not in release_script
    assert "Skipping long release_build reproducibility tests" in release_script
    assert "npm.cmd test" not in release_script
    assert "verify-release-artifacts.ps1" in release_script
    assert "verify-msi-lifecycle.ps1" in release_script
    assert "workflow_dispatch:" in workflow
    assert "Build and run focused MSI lifecycle diagnostic" in workflow
    assert "Preserve MSI lifecycle diagnostic reports" in workflow
    diagnostic_upload = workflow.split(
        "Preserve MSI lifecycle diagnostic reports", 1
    )[1].split("Publish only canonical guarded assets", 1)[0]
    assert "actions/upload-artifact@v4" in diagnostic_upload
    assert "msi-lifecycle-report.json" in diagnostic_upload
    assert "msi-lifecycle-report.md" in diagnostic_upload
    assert "AutoDy-1.5.2-x64.msi" not in diagnostic_upload
    assert "AutoDy-Windows-Portable-1.5.2.zip" not in diagnostic_upload
    assert "if: github.event_name == 'push' && success()" in workflow
    assert "write-release-manifest.ps1" not in workflow.split(
        "Publish only canonical guarded assets", 1
    )[1]
    for asset in [
        "output/release/v1.5.2/AutoDy-1.5.2-x64.msi",
        "output/release/v1.5.2/AutoDy-1.5.2-x64.msi.sha256",
        "output/release/v1.5.2/AutoDy-Windows-Portable-1.5.2.zip",
        "output/release/v1.5.2/AutoDy-Windows-Portable-1.5.2.zip.sha256",
        "output/release/v1.5.2/release-manifest.json",
    ]:
        assert asset in workflow
    published_files = workflow.split("files: |", 1)[1].split(
        "body_path:", 1
    )[0]
    assert "AutoDy-Test-Center" not in published_files
