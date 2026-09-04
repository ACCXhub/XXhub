from pathlib import Path


def test_release_version_is_154_everywhere():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    frontend = Path("frontend/package.json").read_text(encoding="utf-8")
    wix_project = Path("packaging/wix/AutoDy.Package.wixproj").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert 'version = "1.5.4"' in pyproject
    assert '"version": "1.5.4"' in frontend
    assert "<ProductVersion Condition=\"'$(ProductVersion)' == ''\">1.5.4</ProductVersion>" in wix_project
    assert 'AUTODY_RELEASE_VERSION: "1.5.4"' in workflow


def test_setup_exe_is_a_wix_bundle_over_the_canonical_msi():
    project = Path("packaging/wix/AutoDy.Bundle.wixproj").read_text(encoding="utf-8")
    package_project = Path("packaging/wix/AutoDy.Package.wixproj").read_text(encoding="utf-8")
    bundle = Path("packaging/wix/Bundle.wxs").read_text(encoding="utf-8")
    builder = Path("scripts/build-setup-exe.ps1").read_text(encoding="utf-8-sig")

    assert 'Sdk="WixToolset.Sdk/5.0.2"' in project
    assert 'Sdk="WixToolset.Sdk/5.0.2"' in package_project
    assert 'PackageReference Include="WixToolset.Bal.wixext" Version="5.0.2"' in project
    assert 'PackageReference Include="WixToolset.UI.wixext" Version="5.0.2"' in package_project
    assert 'PackageReference Include="WixToolset.Util.wixext" Version="5.0.2"' in package_project
    assert '<OutputType>Bundle</OutputType>' in project
    assert '<EnableDefaultCompileItems>false</EnableDefaultCompileItems>' in project
    assert '<Compile Include="Bundle.wxs" />' in project
    assert '<EnableDefaultCompileItems>false</EnableDefaultCompileItems>' in package_project
    assert '<Compile Include="Product.wxs" />' in package_project
    assert '<Compile Include="$(GeneratedWxs)" />' in package_project
    assert 'bal:WixStandardBootstrapperApplication' in bundle
    assert 'MsiPackage SourceFile="$(var.MsiPath)"' in bundle
    assert 'AutoDy-Setup-$Version.exe' in builder
    assert 'AutoDy-$Version-x64.msi' in builder
    assert 'Get-FileHash -Algorithm SHA256' in builder


def test_msi_installs_a_visible_uninstaller_exe_and_start_menu_shortcut():
    product = Path("packaging/wix/Product.wxs").read_text(encoding="utf-8")
    builder = Path("scripts/build-msi.ps1").read_text(encoding="utf-8-sig")
    uninstaller = Path("packaging/uninstall/Program.cs").read_text(encoding="utf-8")

    assert 'Name="Uninstall AutoDy.exe"' in product
    assert 'Target="[INSTALLFOLDER]Uninstall AutoDy.exe"' in product
    assert 'Id="UninstallShortcut"' in product
    assert 'Build AutoDy uninstaller' in builder
    assert 'Uninstall AutoDy.exe' in builder
    assert 'msiexec.exe' in uninstaller
    assert '/x' in uninstaller
    assert '是否同时删除用户数据' in uninstaller
    assert 'LocalApplicationData' in uninstaller
    assert 'catch (Win32Exception ex) when' not in uninstaller


def test_release_pipeline_builds_and_publishes_setup_exe_assets():
    release_script = Path("scripts/build-release-from-clean-source.ps1").read_text(
        encoding="utf-8-sig"
    )
    manifest = Path("scripts/write-release-manifest.ps1").read_text(encoding="utf-8-sig")
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "build-setup-exe.ps1" in release_script
    assert 'AutoDy-Setup-$Version.exe' in manifest
    assert 'AutoDy-Setup-$Version.exe.sha256' in manifest
    assert "output/release/v1.5.4/AutoDy-Setup-1.5.4.exe" in workflow
    assert "output/release/v1.5.4/AutoDy-Setup-1.5.4.exe.sha256" in workflow
    assert '"AutoDy-Setup-$env:AUTODY_RELEASE_VERSION.exe"' in workflow
    assert '"AutoDy-Setup-$env:AUTODY_RELEASE_VERSION.exe.sha256"' in workflow
