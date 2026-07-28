"""Bounded management for AutoDy's official optional modules.

Only the first-party Test Center package is accepted.  The package is data,
not executable plugin code: the core owns the route implementation and only
serves the installed module's isolated static assets.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

from autody.module_assets import TEST_CENTER_CSS, TEST_CENTER_INDEX, TEST_CENTER_JS


MODULE_ID = "autody-test-center"
MODULE_FILENAME = "AutoDy-Test-Center.autody-module.zip"
MODULE_API_VERSION = "1"
MODULE_PUBLISHER = "AutoDy"
_MANIFEST_NAME = "manifest.json"
_MAX_FILE_COUNT = 16
_MAX_ARCHIVE_BYTES = 512 * 1024
_MAX_EXTRACTED_BYTES = 1024 * 1024
_ALLOWED_FILES = {
    _MANIFEST_NAME,
    "backend.py",
    "frontend/index.html",
    "frontend/module.js",
    "frontend/module.css",
    "README.md",
}


class ModulePackageError(ValueError):
    """Raised when an optional module archive is not safe or compatible."""


def _canonical_checksum(files: dict[str, bytes]) -> str:
    digest = sha256()
    for name in sorted(files):
        if name == _MANIFEST_NAME:
            continue
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256(files[name]).digest())
    return digest.hexdigest()


def _safe_members(archive: zipfile.ZipFile) -> dict[str, bytes]:
    if sum(item.compress_size for item in archive.infolist()) > _MAX_ARCHIVE_BYTES:
        raise ModulePackageError("模块包过大")
    if len(archive.infolist()) > _MAX_FILE_COUNT:
        raise ModulePackageError("模块包文件数量超限")
    files: dict[str, bytes] = {}
    extracted_size = 0
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        path = PurePosixPath(name)
        if not name or name.endswith("/"):
            continue
        if path.is_absolute() or ".." in path.parts or name not in _ALLOWED_FILES:
            raise ModulePackageError("非法文件路径或未知模块文件")
        if name in files:
            raise ModulePackageError("模块包包含重复文件")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ModulePackageError("模块包不能包含链接文件")
        if info.file_size > _MAX_EXTRACTED_BYTES:
            raise ModulePackageError("模块文件过大")
        extracted_size += info.file_size
        if extracted_size > _MAX_EXTRACTED_BYTES:
            raise ModulePackageError("模块解压后过大")
        files[name] = archive.read(info)
    return files


def _read_manifest(files: dict[str, bytes], core_version: str) -> dict:
    if set(files) != _ALLOWED_FILES:
        raise ModulePackageError("模块包文件结构不完整或包含未知文件")
    try:
        manifest = json.loads(files[_MANIFEST_NAME].decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModulePackageError("模块清单无效") from exc
    expected = {
        "module_id": MODULE_ID,
        "display_name": "测试中心",
        "publisher": MODULE_PUBLISHER,
        "required_autody_version": core_version,
        "module_api_version": MODULE_API_VERSION,
        "backend_entry": "backend.py",
        "frontend_entry": "frontend/index.html",
        "data_directory": "data",
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            label = "发布者" if key == "publisher" else "模块标识" if key == "module_id" else "兼容性"
            raise ModulePackageError(f"{label}不匹配")
    if manifest.get("module_version") != "1.1.0":
        raise ModulePackageError("模块版本无效")
    if not isinstance(manifest.get("permissions"), list):
        raise ModulePackageError("模块权限声明无效")
    checksums = manifest.get("file_checksums")
    if not isinstance(checksums, dict) or set(checksums) != _ALLOWED_FILES - {_MANIFEST_NAME}:
        raise ModulePackageError("模块文件校验清单无效")
    if any(checksums.get(name) != sha256(files[name]).hexdigest() for name in checksums):
        raise ModulePackageError("模块文件校验失败")
    if manifest.get("package_checksum") != _canonical_checksum(files):
        raise ModulePackageError("模块包校验失败")
    return manifest


def build_module_archive(destination: Path, *, version: str, core_version: str = "1.3.0", mutate: str | None = None) -> Path:
    """Build the official first-party package used by release and tests."""
    payload = {
        "backend.py": b"# Routes are registered by the bounded AutoDy host.\n",
        "frontend/index.html": TEST_CENTER_INDEX.encode("utf-8"),
        "frontend/module.js": TEST_CENTER_JS.encode("utf-8"),
        "frontend/module.css": TEST_CENTER_CSS.encode("utf-8"),
        "README.md": b"# AutoDy Test Center\n",
    }
    manifest = {
        "module_id": MODULE_ID,
        "display_name": "测试中心",
        "module_version": version,
        "publisher": MODULE_PUBLISHER,
        "required_autody_version": core_version,
        "module_api_version": MODULE_API_VERSION,
        "backend_entry": "backend.py",
        "frontend_entry": "frontend/index.html",
        "permissions": ["read_core_status", "read_core_state", "manage_module_overrides", "run_safe_diagnostics"],
        "data_directory": "data",
    }
    manifest["file_checksums"] = {name: sha256(data).hexdigest() for name, data in payload.items()}
    all_files = {**payload, _MANIFEST_NAME: b""}
    manifest["package_checksum"] = _canonical_checksum(all_files | {_MANIFEST_NAME: json.dumps(manifest, ensure_ascii=False).encode("utf-8")})
    if mutate == "publisher":
        manifest["publisher"] = "Unknown"
    if mutate == "checksum":
        manifest["package_checksum"] = "0" * 64
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in payload.items():
            archive.writestr(name, data)
        archive.writestr(_MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
        if mutate == "traversal":
            archive.writestr("../escape.txt", "invalid")
    return destination


class ModuleManager:
    def __init__(self, state_root: Path, *, core_version: str):
        self.state_root = state_root.resolve()
        self.core_version = core_version

    @property
    def modules_root(self) -> Path:
        return self.state_root / "modules"

    @property
    def module_root(self) -> Path:
        return self.modules_root / MODULE_ID

    @property
    def registry_path(self) -> Path:
        return self.modules_root / "registry.json"

    def _registry(self) -> dict:
        try:
            value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"modules": {}}
        return value if isinstance(value, dict) and isinstance(value.get("modules"), dict) else {"modules": {}}

    def _write_registry(self, value: dict) -> None:
        self.modules_root.mkdir(parents=True, exist_ok=True)
        temporary = self.registry_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.registry_path)

    def _safe_module_root(self) -> Path:
        root = self.module_root
        if not str(root) or root.name != MODULE_ID or root.parent != self.modules_root:
            raise ModulePackageError("模块目录不安全")
        resolved_modules = self.modules_root.resolve()
        resolved_root = root.resolve(strict=False)
        try:
            resolved_root.relative_to(resolved_modules)
        except ValueError as exc:
            raise ModulePackageError("模块目录不安全") from exc
        if resolved_root.name != MODULE_ID:
            raise ModulePackageError("模块目录不安全")
        return root

    def status(self) -> dict:
        entry = self._registry()["modules"].get(MODULE_ID)
        installed = bool(entry and self.module_root.is_dir() and (self.module_root / _MANIFEST_NAME).is_file())
        return {
            "id": MODULE_ID,
            "display_name": "测试中心",
            "installed": installed,
            "version": entry.get("version") if installed else None,
            "compatible": True,
            "load_error": None if installed or entry is None else "模块加载失败",
        }

    def installed(self) -> bool:
        return bool(self.status()["installed"])

    def install(self, archive_path: Path) -> dict:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                files = _safe_members(archive)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ModulePackageError("模块包无法读取") from exc
        manifest = _read_manifest(files, self.core_version)
        self.modules_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{MODULE_ID}-", dir=self.modules_root))
        replacement = temporary / MODULE_ID
        backup = self.modules_root / f".{MODULE_ID}.previous"
        previous_registry = self._registry()
        try:
            replacement.mkdir()
            for name, content in files.items():
                target = replacement / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            (replacement / "data").mkdir()
            if backup.exists():
                shutil.rmtree(backup)
            root = self._safe_module_root()
            if root.exists():
                os.replace(root, backup)
            os.replace(replacement, root)
            registry = json.loads(json.dumps(previous_registry))
            registry["modules"][MODULE_ID] = {"version": manifest["module_version"]}
            self._write_registry(registry)
        except Exception:
            if self.module_root.exists() and backup.exists():
                shutil.rmtree(self.module_root)
                os.replace(backup, self.module_root)
            self._write_registry(previous_registry)
            raise
        finally:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
        return self.status()

    def uninstall(self) -> bool:
        root = self._safe_module_root()
        existed = root.exists()
        if existed:
            shutil.rmtree(root)
        registry = self._registry()
        registry["modules"].pop(MODULE_ID, None)
        if registry["modules"] or self.registry_path.exists():
            self._write_registry(registry)
        return existed
