from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tomllib
import zipfile

import pytest


RELEASE_COMMON = Path("scripts/release-common.ps1").resolve()
BOOTSTRAP_SOURCE = Path("scripts/bootstrap-source.ps1").resolve()
BUILD_MSI = Path("scripts/build-msi.ps1").resolve()
BUILD_PORTABLE = Path("scripts/build-portable.ps1").resolve()
SOURCE_VERSION = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]["version"]


def run_powershell(command: str, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )


def test_release_path_guard_accepts_only_the_canonical_version_directory(
    tmp_path: Path, monkeypatch
):
    canonical = tmp_path / "output" / "release" / "v1.4.3"
    canonical.mkdir(parents=True)
    artifact = canonical / "AutoDy-1.4.3-x64.msi"
    artifact.write_bytes(b"fixture-msi")
    intermediate = tmp_path / "packaging" / "wix" / "obj" / "x64" / "Debug"
    intermediate.mkdir(parents=True)
    debug_artifact = intermediate / "AutoDy-1.4.3-x64.msi"
    debug_artifact.write_bytes(b"fixture-msi")

    monkeypatch.setenv("AUTODY_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODY_TEST_ARTIFACT", str(artifact))
    monkeypatch.setenv("AUTODY_TEST_DEBUG_ARTIFACT", str(debug_artifact))
    monkeypatch.setenv("AUTODY_RELEASE_COMMON", str(RELEASE_COMMON))
    completed = run_powershell(
        r"""
        $ErrorActionPreference = "Stop"
        . $env:AUTODY_RELEASE_COMMON
        $release = Get-CanonicalReleaseDirectory -Root $env:AUTODY_TEST_ROOT -Version "1.4.3"
        $accepted = Assert-CanonicalReleaseArtifact `
            -Root $env:AUTODY_TEST_ROOT -Version "1.4.3" -Path $env:AUTODY_TEST_ARTIFACT
        $rejected = $false
        try {
          Assert-CanonicalReleaseArtifact `
              -Root $env:AUTODY_TEST_ROOT -Version "1.4.3" -Path $env:AUTODY_TEST_DEBUG_ARTIFACT
        } catch {
          $rejected = $true
        }
        [pscustomobject]@{
          release = $release
          accepted = $accepted
          rejected = $rejected
        } | ConvertTo-Json -Compress
        """,
        env=dict(__import__("os").environ),
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert Path(result["release"]) == canonical
    assert Path(result["accepted"]) == artifact
    assert result["rejected"] is True


def test_release_work_pruning_keeps_only_current_canonical_directories(
    tmp_path: Path, monkeypatch
):
    work = tmp_path / "output" / "work"
    for name in [
        "msi-stage-v1.4.4",
        "msi-stage-v1.5.1",
        "msi-stage",
        "portable-v1.4.4",
        "msi-lifecycle-v1.4.4",
        "ci-lifecycle-33033797667",
        "portable-v1.5.1",
        "unrelated-work",
    ]:
        (work / name).mkdir(parents=True)

    monkeypatch.setenv("AUTODY_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODY_RELEASE_COMMON", str(RELEASE_COMMON))
    completed = run_powershell(
        r"""
        $ErrorActionPreference = "Stop"
        . $env:AUTODY_RELEASE_COMMON
        Clear-SupersededReleaseWorkDirectories -Root $env:AUTODY_TEST_ROOT -Version "1.5.1"
        Get-ChildItem -LiteralPath (Join-Path $env:AUTODY_TEST_ROOT "output\work") -Directory |
          Select-Object -ExpandProperty Name | Sort-Object | ConvertTo-Json -Compress
        """,
        env=dict(__import__("os").environ),
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == [
        "msi-stage",
        "portable-v1.5.1",
        "unrelated-work",
    ]


def test_release_manifest_hashes_real_canonical_artifacts(tmp_path: Path, monkeypatch):
    canonical = tmp_path / "output" / "release" / "v1.4.3"
    canonical.mkdir(parents=True)
    artifact = canonical / "AutoDy-Windows-Portable-1.4.3.zip"
    payload = b"portable-fixture\n"
    artifact.write_bytes(payload)

    monkeypatch.setenv("AUTODY_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODY_TEST_ARTIFACT", str(artifact))
    monkeypatch.setenv("AUTODY_RELEASE_COMMON", str(RELEASE_COMMON))
    completed = run_powershell(
        r"""
        $ErrorActionPreference = "Stop"
        . $env:AUTODY_RELEASE_COMMON
        $manifest = New-ReleaseManifestData `
            -Root $env:AUTODY_TEST_ROOT `
            -Version "1.4.3" `
            -Commit "0123456789abcdef0123456789abcdef01234567" `
            -Configuration "Release" `
            -ArtifactPaths @($env:AUTODY_TEST_ARTIFACT) `
            -ProductCode "{11111111-1111-1111-1111-111111111111}" `
            -UpgradeCode "{22222222-2222-2222-2222-222222222222}" `
            -PrivacyPassed $true `
            -LifecyclePassed $true
        $manifest | ConvertTo-Json -Depth 8 -Compress
        """,
        env=dict(__import__("os").environ),
    )

    assert completed.returncode == 0, completed.stderr
    manifest = json.loads(completed.stdout)
    assert manifest["version"] == "1.4.3"
    assert manifest["commit"] == "0123456789abcdef0123456789abcdef01234567"
    assert manifest["configuration"] == "Release"
    assert manifest["privacy_scan"] == "passed"
    assert manifest["lifecycle_test"] == "passed"
    assert manifest["product_code"] == "{11111111-1111-1111-1111-111111111111}"
    assert manifest["upgrade_code"] == "{22222222-2222-2222-2222-222222222222}"
    assert manifest["artifacts"] == [
        {
            "file": "AutoDy-Windows-Portable-1.4.3.zip",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    ]


def test_source_bootstrap_check_only_is_read_only(monkeypatch):
    before = subprocess.run(
        ["git", "status", "--short"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    monkeypatch.setenv("AUTODY_BOOTSTRAP_SCRIPT", str(BOOTSTRAP_SOURCE))
    monkeypatch.setenv("AUTODY_TEST_ROOT", str(Path.cwd()))
    completed = run_powershell(
        r"""
        $ErrorActionPreference = "Stop"
        & $env:AUTODY_BOOTSTRAP_SCRIPT -Root $env:AUTODY_TEST_ROOT -CheckOnly
        """,
        env=dict(__import__("os").environ),
    )
    after = subprocess.run(
        ["git", "status", "--short"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["passed"] is True
    assert result["checks"] == {
        "windows_x64": "passed",
        "python_3_11": "passed",
        "node": "passed",
        "npm": "passed",
        "source_metadata": "passed",
        "dependency_lock": "passed",
    }
    assert before == after


def test_source_bootstrap_runs_doctor_in_utf8_mode():
    script = BOOTSTRAP_SOURCE.read_text(encoding="utf-8-sig")

    assert "Run source doctor check" in script
    assert "@('-X', 'utf8', '-m', 'autody.cli', 'doctor'" in script


def test_public_build_plans_use_release_configuration_and_canonical_outputs(
    monkeypatch,
):
    monkeypatch.setenv("AUTODY_BUILD_MSI", str(BUILD_MSI))
    monkeypatch.setenv("AUTODY_BUILD_PORTABLE", str(BUILD_PORTABLE))
    monkeypatch.setenv("AUTODY_TEST_VERSION", SOURCE_VERSION)
    completed = run_powershell(
        r"""
        $ErrorActionPreference = "Stop"
        $msi = & $env:AUTODY_BUILD_MSI -Version $env:AUTODY_TEST_VERSION -PlanOnly | ConvertFrom-Json
        $portable = & $env:AUTODY_BUILD_PORTABLE -Version $env:AUTODY_TEST_VERSION -PlanOnly | ConvertFrom-Json
        [pscustomobject]@{ msi = $msi; portable = $portable } |
          ConvertTo-Json -Depth 6 -Compress
        """,
        env=dict(__import__("os").environ),
    )

    assert completed.returncode == 0, completed.stderr
    plans = json.loads(completed.stdout)
    assert plans["msi"]["configuration"] == "Release"
    source_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()
    assert plans["msi"]["source_revision"] == source_revision
    assert plans["msi"]["artifact"].endswith(
        f"output\\release\\v{SOURCE_VERSION}\\AutoDy-{SOURCE_VERSION}-x64.msi"
    )
    assert plans["portable"]["artifact"].endswith(
        f"output\\release\\v{SOURCE_VERSION}\\AutoDy-Windows-Portable-{SOURCE_VERSION}.zip"
    )
    assert plans["msi"]["release_directory"] == plans["portable"][
        "release_directory"
    ]
    assert all(
        token not in plans["msi"]["artifact"].casefold()
        for token in ("\\obj\\", "\\debug\\")
    )


def test_msi_package_identity_changes_with_revision_without_changing_product_identity(
    monkeypatch,
):
    monkeypatch.setenv("AUTODY_RELEASE_COMMON", str(RELEASE_COMMON))
    completed = run_powershell(
        r"""
        $ErrorActionPreference = "Stop"
        . $env:AUTODY_RELEASE_COMMON
        [pscustomobject]@{
          product_a = Get-ReproducibleMsiGuid `
            -Purpose product -Version "1.4.3" -IdentitySeed ('a' * 40)
          product_b = Get-ReproducibleMsiGuid `
            -Purpose product -Version "1.4.3" -IdentitySeed ('b' * 40)
          package_a = Get-ReproducibleMsiGuid `
            -Purpose package -Version "1.4.3" -IdentitySeed ('a' * 40)
          package_a_repeat = Get-ReproducibleMsiGuid `
            -Purpose package -Version "1.4.3" -IdentitySeed ('a' * 40)
          package_b = Get-ReproducibleMsiGuid `
            -Purpose package -Version "1.4.3" -IdentitySeed ('b' * 40)
        } | ConvertTo-Json -Compress
        """,
        env=dict(__import__("os").environ),
    )

    assert completed.returncode == 0, completed.stderr
    identities = json.loads(completed.stdout)
    assert identities["product_a"] == identities["product_b"]
    assert identities["package_a"] == identities["package_a_repeat"]
    assert identities["package_a"] != identities["package_b"]


def test_msi_builder_rejects_a_version_that_does_not_match_source(monkeypatch):
    monkeypatch.setenv("AUTODY_BUILD_MSI", str(BUILD_MSI))
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BUILD_MSI),
            "-Version",
            "9.9.9",
            "-PlanOnly",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode != 0
    assert "does not match source package version" in completed.stderr


def test_release_build_scripts_reset_or_remove_their_canonical_work_directories():
    msi_builder = Path("scripts/build-msi.ps1").read_text(encoding="utf-8-sig")
    portable_builder = Path("scripts/build-portable.ps1").read_text(
        encoding="utf-8-sig"
    )
    lifecycle_verifier = Path("scripts/verify-msi-lifecycle.ps1").read_text(
        encoding="utf-8-sig"
    )

    for script in (msi_builder, portable_builder, lifecycle_verifier):
        assert "Clear-SupersededReleaseWorkDirectories" in script
    assert "Remove-ReleaseWorkDirectory -Root $Root -Path $Work" in msi_builder
    assert "Reset-ReleaseWorkDirectory -Root $Root -Path $Work" in portable_builder
    assert "Remove-ReleaseWorkDirectory -Root $Root -Path $Work" in portable_builder
    assert "if ($report.passed)" in lifecycle_verifier


@pytest.mark.release_build
def test_portable_release_archive_is_byte_reproducible(monkeypatch):
    monkeypatch.setenv("AUTODY_BUILD_PORTABLE", str(BUILD_PORTABLE))
    env = dict(__import__("os").environ)
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(BUILD_PORTABLE),
        "-Version",
        SOURCE_VERSION,
        "-ReuseRuntime",
    ]

    first = subprocess.run(command, capture_output=True, env=env, check=False)
    assert first.returncode == 0, first.stderr.decode("utf-8", errors="replace")
    archive = Path(
        f"output/release/v{SOURCE_VERSION}/AutoDy-Windows-Portable-{SOURCE_VERSION}.zip"
    )
    first_bytes = archive.read_bytes()
    second = subprocess.run(command, capture_output=True, env=env, check=False)
    assert second.returncode == 0, second.stderr.decode("utf-8", errors="replace")

    assert archive.read_bytes() == first_bytes
    with zipfile.ZipFile(archive) as portable:
        assert {entry.date_time for entry in portable.infolist()} == {
            (2000, 1, 1, 0, 0, 0)
        }
        names = set(portable.namelist())
        assert "runtime/python/python.exe" in names
        assert "runtime/distribution-mode.txt" in names
        assert portable.read("runtime/distribution-mode.txt") == b"portable"
        assert any(name.startswith("runtime/ms-playwright/") for name in names)
        assert "runtime/python/Lib/site-packages/autody/web/static/index.html" in names
        assert "message-packs/index.json" in names
        assert "AutoDy.cmd" in names
        assert not any(name.startswith("src/") for name in names)
        for release_only in {
            "scripts/bootstrap-source.ps1",
            "scripts/build-release-from-clean-source.ps1",
            "scripts/release-common.ps1",
            "scripts/write-release-manifest.ps1",
        }:
            assert release_only not in names


@pytest.mark.release_build
def test_msi_release_is_byte_reproducible_with_stable_identity(monkeypatch):
    monkeypatch.setenv("AUTODY_BUILD_MSI", str(BUILD_MSI))
    env = dict(__import__("os").environ)
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(BUILD_MSI),
        "-Version",
        SOURCE_VERSION,
        "-ReuseRuntime",
    ]
    archive = Path(
        f"output/release/v{SOURCE_VERSION}/AutoDy-{SOURCE_VERSION}-x64.msi"
    )

    def build_and_inspect() -> dict[str, str]:
        completed = subprocess.run(command, capture_output=True, env=env, check=False)
        assert completed.returncode == 0, completed.stderr.decode(
            "utf-8", errors="replace"
        )
        monkeypatch.setenv("AUTODY_TEST_MSI", str(archive.resolve()))
        inspected = run_powershell(
            r"""
            $ErrorActionPreference = "Stop"
            $installer = New-Object -ComObject WindowsInstaller.Installer
            $database = $installer.OpenDatabase($env:AUTODY_TEST_MSI, 0)
            $view = $database.OpenView(
              "SELECT ``Value`` FROM ``Property`` WHERE ``Property`` = 'ProductCode'"
            )
            $view.Execute()
            $productCode = $view.Fetch().StringData(1)
            $view.Close()
            $summary = $database.SummaryInformation(0)
            [pscustomobject]@{
              product_code = $productCode
              package_code = $summary.Property(9)
            } | ConvertTo-Json -Compress
            """,
            env=dict(__import__("os").environ),
        )
        assert inspected.returncode == 0, inspected.stderr
        result = json.loads(inspected.stdout)
        with archive.open("rb") as stream:
            result["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        return result

    first = build_and_inspect()
    second = build_and_inspect()

    assert second == first


def test_release_text_normalization_makes_crlf_and_lf_archives_identical(
    tmp_path: Path, monkeypatch
):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "script.ps1").write_bytes(b"Write-Host one\r\nWrite-Host two\r\n")
    (right / "script.ps1").write_bytes(b"Write-Host one\nWrite-Host two\n")
    (left / "icon.ico").write_bytes(b"\x00\x01\r\n")
    (right / "icon.ico").write_bytes(b"\x00\x01\r\n")

    monkeypatch.setenv("AUTODY_RELEASE_COMMON", str(RELEASE_COMMON))
    monkeypatch.setenv("AUTODY_TEST_LEFT", str(left))
    monkeypatch.setenv("AUTODY_TEST_RIGHT", str(right))
    monkeypatch.setenv("AUTODY_TEST_LEFT_ZIP", str(tmp_path / "left.zip"))
    monkeypatch.setenv("AUTODY_TEST_RIGHT_ZIP", str(tmp_path / "right.zip"))
    completed = run_powershell(
        r"""
        $ErrorActionPreference = "Stop"
        . $env:AUTODY_RELEASE_COMMON
        Convert-ReleaseTextFilesToLf -Root $env:AUTODY_TEST_LEFT
        Convert-ReleaseTextFilesToLf -Root $env:AUTODY_TEST_RIGHT
        New-ReproducibleZipFromDirectory `
          -SourceDirectory $env:AUTODY_TEST_LEFT -DestinationPath $env:AUTODY_TEST_LEFT_ZIP
        New-ReproducibleZipFromDirectory `
          -SourceDirectory $env:AUTODY_TEST_RIGHT -DestinationPath $env:AUTODY_TEST_RIGHT_ZIP
        """,
        env=dict(__import__("os").environ),
    )

    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "left.zip").read_bytes() == (tmp_path / "right.zip").read_bytes()
    assert b"\r" not in (left / "script.ps1").read_bytes()
    assert (left / "icon.ico").read_bytes() == b"\x00\x01\r\n"


def test_release_text_normalization_keeps_windows_scripts_utf8_for_powershell_51(
    tmp_path: Path, monkeypatch
):
    script = tmp_path / "unicode.ps1"
    script.write_text('Write-Output "需要处理"\r\n', encoding="utf-8")

    monkeypatch.setenv("AUTODY_RELEASE_COMMON", str(RELEASE_COMMON))
    monkeypatch.setenv("AUTODY_TEST_ROOT", str(tmp_path))
    completed = run_powershell(
        r"""
        $ErrorActionPreference = "Stop"
        . $env:AUTODY_RELEASE_COMMON
        Convert-ReleaseTextFilesToLf -Root $env:AUTODY_TEST_ROOT
        """,
        env=dict(__import__("os").environ),
    )
    assert completed.returncode == 0, completed.stderr

    normalized = script.read_bytes()
    assert normalized.startswith(b"\xef\xbb\xbf")
    executed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-File", str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert executed.returncode == 0, executed.stderr
    assert executed.stdout.strip() == "需要处理"


def test_release_text_normalization_keeps_vbs_compatible_with_windows_script_host(
    tmp_path: Path, monkeypatch
):
    script = tmp_path / "launcher.vbs"
    script.write_text('WScript.Echo "launcher-ok"\r\n', encoding="utf-8")

    monkeypatch.setenv("AUTODY_RELEASE_COMMON", str(RELEASE_COMMON))
    monkeypatch.setenv("AUTODY_TEST_ROOT", str(tmp_path))
    completed = run_powershell(
        r"""
        $ErrorActionPreference = "Stop"
        . $env:AUTODY_RELEASE_COMMON
        Convert-ReleaseTextFilesToLf -Root $env:AUTODY_TEST_ROOT
        """,
        env=dict(__import__("os").environ),
    )
    assert completed.returncode == 0, completed.stderr

    assert not script.read_bytes().startswith(b"\xef\xbb\xbf")
    executed = subprocess.run(
        ["cscript.exe", "//nologo", str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert executed.returncode == 0, executed.stderr
    assert executed.stdout.strip() == "launcher-ok"
