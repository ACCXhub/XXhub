from pathlib import Path
import zipfile

import pytest

from autody.modules import (
    MODULE_ID,
    OFFICIAL_TEST_CENTER_CORE_RANGE,
    OFFICIAL_TEST_CENTER_VERSION,
    ModuleManager,
    ModulePackageError,
    build_official_module_archive,
    build_module_archive,
    ensure_official_module_archive,
)


def test_test_center_is_uninstalled_by_default(tmp_path: Path):
    manager = ModuleManager(tmp_path, core_version="1.3.0")

    status = manager.status()

    assert status["id"] == MODULE_ID
    assert status["installed"] is False
    assert not (tmp_path / "modules" / MODULE_ID).exists()


def test_official_module_metadata_is_generated_from_the_single_version_policy(tmp_path: Path):
    package = build_official_module_archive(tmp_path / "AutoDy-Test-Center.autody-module.zip")

    with zipfile.ZipFile(package) as archive:
        manifest = __import__("json").loads(archive.read("manifest.json"))

    assert manifest["module_version"] == OFFICIAL_TEST_CENTER_VERSION
    assert manifest["required_autody_version"] == OFFICIAL_TEST_CENTER_CORE_RANGE


def test_source_runtime_generates_current_package_without_reading_output_directory(tmp_path: Path):
    (tmp_path / "src" / "autody").mkdir(parents=True)
    (tmp_path / "src" / "autody" / "modules.py").write_text("# source marker", encoding="utf-8")
    build_module_archive(tmp_path / "output" / "AutoDy-Test-Center.autody-module.zip", version="1.1.0")

    info = ensure_official_module_archive(tmp_path)

    assert info["module_version"] == "1.2.0"
    assert "output" not in info["path"]
    assert Path(info["path"]).is_file()


def test_stale_portable_bundle_is_rejected_instead_of_becoming_runtime_metadata(tmp_path: Path):
    build_module_archive(tmp_path / "optional-modules" / "AutoDy-Test-Center.autody-module.zip", version="1.1.0")

    with pytest.raises(ModulePackageError, match="版本不匹配"):
        ensure_official_module_archive(tmp_path)


def test_valid_official_package_installs_and_uninstalls_cleanly(tmp_path: Path):
    package = build_module_archive(tmp_path / "AutoDy-Test-Center.autody-module.zip", version="1.1.0", core_version="1.3.0")
    manager = ModuleManager(tmp_path, core_version="1.3.0")

    installed = manager.install(package)
    module_root = tmp_path / "modules" / MODULE_ID
    for name in ("history.jsonl", "settings.json", "overrides.json", "preflight/progress.json", "fixtures/data.json"):
        path = module_root / "data" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")

    assert installed["installed"] is True
    assert (module_root / "manifest.json").is_file()
    assert manager.uninstall() is True
    assert not module_root.exists()
    assert manager.status()["installed"] is False


def test_module_uses_a_core_version_range_and_reports_an_outdated_install(tmp_path: Path):
    package = build_module_archive(
        tmp_path / "AutoDy-Test-Center.autody-module.zip",
        version="1.2.0",
        core_version=">=1.3.0,<2.0.0",
    )
    manager = ModuleManager(tmp_path, core_version="1.4.0")

    installed = manager.install(package)

    assert installed["compatible"] is True
    assert installed["required_autody_version"] == ">=1.3.0,<2.0.0"
    assert installed["update_available"] is False

    old_package = build_module_archive(
        tmp_path / "old.autody-module.zip", version="1.1.0", core_version="1.3.0"
    )
    old_manager = ModuleManager(tmp_path / "old", core_version="1.4.0")
    with pytest.raises(ModulePackageError, match="兼容性"):
        old_manager.install(old_package)


def test_status_detects_a_stale_module_manifest_without_hiding_the_reason(tmp_path: Path):
    package = build_module_archive(tmp_path / "old.autody-module.zip", version="1.1.0", core_version="1.3.0")
    installer = ModuleManager(tmp_path, core_version="1.3.0")
    installer.install(package)

    status = ModuleManager(tmp_path, core_version="1.4.0").status()

    assert status["installed"] is True
    assert status["compatible"] is False
    assert "1.3.0" in status["compatibility_reason"]


def test_test_center_package_uses_an_explicit_self_removal_dialog(tmp_path: Path):
    package = build_module_archive(tmp_path / "AutoDy-Test-Center.autody-module.zip", version="1.1.0", core_version="1.3.0")

    with zipfile.ZipFile(package) as archive:
        module_js = archive.read("frontend/module.js").decode("utf-8")

    assert 'id="remove-dialog"' in module_js
    assert 'id="remove-cancel"' in module_js
    assert 'id="remove-confirm"' in module_js
    assert "confirm(warning)" not in module_js
    assert "autody-test-center:resize" in module_js


@pytest.mark.parametrize(
    "mutate, expected",
    [
        ("traversal", "非法文件路径"),
        ("publisher", "发布者"),
        ("checksum", "校验"),
    ],
)
def test_invalid_module_package_is_rejected_without_creating_module_data(tmp_path: Path, mutate: str, expected: str):
    package = build_module_archive(tmp_path / "invalid.autody-module.zip", version="1.1.0", core_version="1.3.0", mutate=mutate)
    manager = ModuleManager(tmp_path, core_version="1.3.0")

    with pytest.raises(ModulePackageError, match=expected):
        manager.install(package)

    assert manager.status()["installed"] is False
    assert not (tmp_path / "modules" / MODULE_ID).exists()
