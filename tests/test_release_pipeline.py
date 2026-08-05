from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


RELEASE_COMMON = Path("scripts/release-common.ps1").resolve()
BOOTSTRAP_SOURCE = Path("scripts/bootstrap-source.ps1").resolve()
BUILD_MSI = Path("scripts/build-msi.ps1").resolve()
BUILD_PORTABLE = Path("scripts/build-portable.ps1").resolve()


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
    canonical = tmp_path / "output" / "release" / "v1.4.2"
    canonical.mkdir(parents=True)
    artifact = canonical / "AutoDy-1.4.2-x64.msi"
    artifact.write_bytes(b"fixture-msi")
    intermediate = tmp_path / "packaging" / "wix" / "obj" / "x64" / "Debug"
    intermediate.mkdir(parents=True)
    debug_artifact = intermediate / "AutoDy-1.4.2-x64.msi"
    debug_artifact.write_bytes(b"fixture-msi")

    monkeypatch.setenv("AUTODY_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODY_TEST_ARTIFACT", str(artifact))
    monkeypatch.setenv("AUTODY_TEST_DEBUG_ARTIFACT", str(debug_artifact))
    monkeypatch.setenv("AUTODY_RELEASE_COMMON", str(RELEASE_COMMON))
    completed = run_powershell(
        r"""
        $ErrorActionPreference = "Stop"
        . $env:AUTODY_RELEASE_COMMON
        $release = Get-CanonicalReleaseDirectory -Root $env:AUTODY_TEST_ROOT -Version "1.4.2"
        $accepted = Assert-CanonicalReleaseArtifact `
            -Root $env:AUTODY_TEST_ROOT -Version "1.4.2" -Path $env:AUTODY_TEST_ARTIFACT
        $rejected = $false
        try {
          Assert-CanonicalReleaseArtifact `
              -Root $env:AUTODY_TEST_ROOT -Version "1.4.2" -Path $env:AUTODY_TEST_DEBUG_ARTIFACT
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


def test_release_manifest_hashes_real_canonical_artifacts(tmp_path: Path, monkeypatch):
    canonical = tmp_path / "output" / "release" / "v1.4.2"
    canonical.mkdir(parents=True)
    artifact = canonical / "AutoDy-Windows-Portable-1.4.2.zip"
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
            -Version "1.4.2" `
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
    assert manifest["version"] == "1.4.2"
    assert manifest["commit"] == "0123456789abcdef0123456789abcdef01234567"
    assert manifest["configuration"] == "Release"
    assert manifest["privacy_scan"] == "passed"
    assert manifest["lifecycle_test"] == "passed"
    assert manifest["product_code"] == "{11111111-1111-1111-1111-111111111111}"
    assert manifest["upgrade_code"] == "{22222222-2222-2222-2222-222222222222}"
    assert manifest["artifacts"] == [
        {
            "file": "AutoDy-Windows-Portable-1.4.2.zip",
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


def test_public_build_plans_use_release_configuration_and_canonical_outputs(
    monkeypatch,
):
    monkeypatch.setenv("AUTODY_BUILD_MSI", str(BUILD_MSI))
    monkeypatch.setenv("AUTODY_BUILD_PORTABLE", str(BUILD_PORTABLE))
    completed = run_powershell(
        r"""
        $ErrorActionPreference = "Stop"
        $msi = & $env:AUTODY_BUILD_MSI -Version "1.4.2" -PlanOnly | ConvertFrom-Json
        $portable = & $env:AUTODY_BUILD_PORTABLE -Version "1.4.2" -PlanOnly | ConvertFrom-Json
        [pscustomobject]@{ msi = $msi; portable = $portable } |
          ConvertTo-Json -Depth 6 -Compress
        """,
        env=dict(__import__("os").environ),
    )

    assert completed.returncode == 0, completed.stderr
    plans = json.loads(completed.stdout)
    assert plans["msi"]["configuration"] == "Release"
    assert plans["msi"]["artifact"].endswith(
        r"output\release\v1.4.2\AutoDy-1.4.2-x64.msi"
    )
    assert plans["portable"]["artifact"].endswith(
        r"output\release\v1.4.2\AutoDy-Windows-Portable-1.4.2.zip"
    )
    assert plans["msi"]["release_directory"] == plans["portable"][
        "release_directory"
    ]
    assert all(
        token not in plans["msi"]["artifact"].casefold()
        for token in ("\\obj\\", "\\debug\\")
    )


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
        "1.4.2",
    ]

    first = subprocess.run(command, capture_output=True, env=env, check=False)
    assert first.returncode == 0, first.stderr.decode("utf-8", errors="replace")
    archive = Path("output/release/v1.4.2/AutoDy-Windows-Portable-1.4.2.zip")
    first_bytes = archive.read_bytes()
    second = subprocess.run(command, capture_output=True, env=env, check=False)
    assert second.returncode == 0, second.stderr.decode("utf-8", errors="replace")

    assert archive.read_bytes() == first_bytes
    with zipfile.ZipFile(archive) as portable:
        assert {entry.date_time for entry in portable.infolist()} == {
            (2000, 1, 1, 0, 0, 0)
        }
        names = set(portable.namelist())
        for release_only in {
            "scripts/bootstrap-source.ps1",
            "scripts/build-release-from-clean-source.ps1",
            "scripts/release-common.ps1",
            "scripts/write-release-manifest.ps1",
        }:
            assert release_only not in names


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
        "1.4.2",
        "-ReuseRuntime",
    ]
    archive = Path("output/release/v1.4.2/AutoDy-1.4.2-x64.msi")

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
