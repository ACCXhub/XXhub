from pathlib import Path
from hashlib import sha256
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


def test_upgrade_preserves_runtime_data_and_records_package_identity(tmp_path: Path):
    old_package = build_module_archive(
        tmp_path / "old.autody-module.zip",
        version="1.1.0",
        core_version=">=1.3.0,<2.0.0",
    )
    new_package = build_official_module_archive(tmp_path / "new.autody-module.zip")
    manager = ModuleManager(tmp_path, core_version="1.4.0")
    manager.install(old_package)
    module_root = tmp_path / "modules" / MODULE_ID
    preserved = {
        "settings.json": b'{"delay":1200}',
        "history/events.jsonl": b'{"result":"navigation_only_passed"}\n',
        "recovery/session.bin": b"\x00private-runtime-state\xff",
    }
    for relative, content in preserved.items():
        path = module_root / "data" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    installed = manager.install(new_package)

    assert installed["version"] == OFFICIAL_TEST_CENTER_VERSION
    assert installed["module_api_version"] == "1"
    assert installed["package_sha256"] == sha256(new_package.read_bytes()).hexdigest()
    assert installed["package_checksum"]
    assert {
        relative: (module_root / "data" / relative).read_bytes()
        for relative in preserved
    } == preserved
    assert not (tmp_path / "modules" / f".{MODULE_ID}.previous").exists()


def test_failed_upgrade_rolls_back_code_registry_and_runtime_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    old_package = build_module_archive(
        tmp_path / "old.autody-module.zip",
        version="1.1.0",
        core_version=">=1.3.0,<2.0.0",
    )
    new_package = build_official_module_archive(tmp_path / "new.autody-module.zip")
    manager = ModuleManager(tmp_path, core_version="1.4.0")
    old_status = manager.install(old_package)
    module_root = tmp_path / "modules" / MODULE_ID
    data_path = module_root / "data" / "history" / "events.jsonl"
    data_path.parent.mkdir(parents=True)
    data_path.write_bytes(b'{"private":"preserve"}\n')
    old_manifest = (module_root / "manifest.json").read_bytes()

    def reject_activation(_root: Path) -> dict:
        raise ModulePackageError("activation rejected")

    monkeypatch.setattr(manager, "_validate_installed_module", reject_activation)

    with pytest.raises(ModulePackageError, match="activation rejected"):
        manager.install(new_package)

    assert (module_root / "manifest.json").read_bytes() == old_manifest
    assert data_path.read_bytes() == b'{"private":"preserve"}\n'
    assert manager.status()["version"] == old_status["version"]
    assert manager.status()["package_sha256"] == old_status["package_sha256"]


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
    assert "request('/uninstall', {confirmed:true})" in module_js
    assert "confirm(warning)" not in module_js
    assert "autody-test-center:resize" in module_js


def test_test_center_package_uses_real_page_dry_run_controls_without_screenshot_preview(tmp_path: Path):
    package = build_official_module_archive(tmp_path / "AutoDy-Test-Center.autody-module.zip")

    with zipfile.ZipFile(package) as archive:
        module_js = archive.read("frontend/module.js").decode("utf-8")
        module_css = archive.read("frontend/module.css").decode("utf-8")

    assert "当前阶段" in module_js
    assert "自动切换" not in module_js
    assert "开始单个测试" in module_js
    assert "开始批量测试" in module_js
    assert "automatic, navigation_only" in module_js
    assert "可安全批量测试" in module_js
    assert "恢复默认值" in module_js
    assert "/dry-run/start" in module_js
    assert "page_ready_delay_ms" in module_js
    assert "人工查看" not in module_js
    assert "screenshot" not in module_js.lower()
    assert "textarea" in module_js
    assert "send_message" not in module_js
    assert "press(\"Enter\")" not in module_js
    assert "overflow:auto" not in module_css
    assert "overflow-y:auto" not in module_css
    assert "#root{width:100%;padding:0}" in module_css
    assert ".module-shell{width:100%;margin:0}" in module_css
    assert "max-width:1180px" not in module_css
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in module_css
    assert "@media(max-width:760px)" in module_css
    assert ".dry-grid{grid-template-columns:1fr}" in module_css


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
