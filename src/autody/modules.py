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
OFFICIAL_TEST_CENTER_VERSION = "1.2.0"
OFFICIAL_TEST_CENTER_CORE_RANGE = ">=1.3.0,<2.0.0"
_MANIFEST_NAME = "manifest.json"
_MAX_FILE_COUNT = 16
_MAX_ARCHIVE_BYTES = 512 * 1024
_MAX_EXTRACTED_BYTES = 1024 * 1024
_REPRODUCIBLE_ZIP_TIMESTAMP = (2000, 1, 1, 0, 0, 0)
_ALLOWED_FILES = {
    _MANIFEST_NAME,
    "backend.py",
    "frontend/index.html",
    "frontend/module.js",
    "frontend/module.css",
    "README.md",
}


def _write_reproducible_zip_entry(
    archive: zipfile.ZipFile, name: str, data: bytes | str
) -> None:
    info = zipfile.ZipInfo(name, date_time=_REPRODUCIBLE_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def _project_source_present(root: Path) -> bool:
    """Whether ``root`` is a source checkout which can generate its package."""
    return (root / "src" / "autody" / "modules.py").is_file()


def official_module_archive_path(root: Path) -> Path:
    """Return the sole package location for this installation.

    Source installations generate a runtime copy under their own data directory;
    portable installations use the package copied into their own root.  In
    particular, an ``output`` directory is never an input at runtime.
    """
    root = root.resolve()
    if _project_source_present(root):
        return root / "data" / "module-cache" / MODULE_FILENAME
    return root / "optional-modules" / MODULE_FILENAME


def inspect_module_archive(path: Path) -> dict:
    """Read the non-sensitive package identity used by startup diagnostics."""
    try:
        with zipfile.ZipFile(path) as archive:
            files = _safe_members(archive)
        manifest = _read_manifest(files, "1.4.0")
    except (OSError, zipfile.BadZipFile, ModulePackageError) as exc:
        raise ModulePackageError(f"官方模块包无效：{exc}") from exc
    return {
        "path": str(path.resolve()),
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "modified_at": path.stat().st_mtime,
        "module_version": manifest["module_version"],
        "module_api_version": manifest["module_api_version"],
        "required_autody_version": manifest["required_autody_version"],
        "package_checksum": manifest["package_checksum"],
    }


def ensure_official_module_archive(root: Path) -> dict:
    """Create or validate the authoritative first-party module package.

    This deliberately refuses a stale portable package.  A source checkout
    rewrites only its generated cache atomically; installed module data and
    registries are never touched here.
    """
    root = root.resolve()
    package = official_module_archive_path(root)
    if _project_source_present(root) or not package.is_file():
        # A test/project fixture (or a partial portable repair) has no bundled
        # archive yet.  Generate a local cache from this exact code; never fall
        # back to a release/output directory.
        package = root / "data" / "module-cache" / MODULE_FILENAME
        temporary = package.with_suffix(".tmp")
        build_official_module_archive(temporary)
        package.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, package)
    info = inspect_module_archive(package)
    expected = (OFFICIAL_TEST_CENTER_VERSION, MODULE_API_VERSION, OFFICIAL_TEST_CENTER_CORE_RANGE)
    actual = (info["module_version"], info["module_api_version"], info["required_autody_version"])
    if actual != expected:
        raise ModulePackageError(
            "官方模块包版本不匹配："
            f"期望 {OFFICIAL_TEST_CENTER_VERSION}/{MODULE_API_VERSION}/{OFFICIAL_TEST_CENTER_CORE_RANGE}，"
            f"实际 {actual[0]}/{actual[1]}/{actual[2]}。"
        )
    return info


def _version_parts(value: str) -> tuple[int, int, int]:
    """Parse the deliberately small semver subset accepted by module packages."""
    pieces = value.strip().split(".")
    if len(pieces) != 3 or any(not piece.isdigit() for piece in pieces):
        raise ValueError("invalid version")
    return tuple(int(piece) for piece in pieces)  # type: ignore[return-value]


def _supports_core(requirement: str, core_version: str) -> bool:
    """Evaluate comma-separated ``>=``, ``>``, ``<=``, ``<`` and exact bounds."""
    try:
        current = _version_parts(core_version)
        clauses = [item.strip() for item in requirement.split(",") if item.strip()]
        if not clauses:
            return False
        for clause in clauses:
            operator = next((item for item in (">=", "<=", ">", "<", "=") if clause.startswith(item)), "=")
            expected = _version_parts(clause[len(operator):] if clause.startswith(operator) else clause)
            if not {
                ">=": current >= expected,
                "<=": current <= expected,
                ">": current > expected,
                "<": current < expected,
                "=": current == expected,
            }[operator]:
                return False
        return True
    except ValueError:
        return False


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
        "module_api_version": MODULE_API_VERSION,
        "backend_entry": "backend.py",
        "frontend_entry": "frontend/index.html",
        "data_directory": "data",
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            label = "发布者" if key == "publisher" else "模块标识" if key == "module_id" else "兼容性"
            raise ModulePackageError(f"{label}不匹配")
    if not isinstance(manifest.get("required_autody_version"), str) or not _supports_core(manifest["required_autody_version"], core_version):
        raise ModulePackageError("兼容性不匹配")
    try:
        _version_parts(str(manifest.get("module_version", "")))
    except ValueError:
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


def build_module_archive(destination: Path, *, version: str, core_version: str = ">=1.3.0,<2.0.0", mutate: str | None = None) -> Path:
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
            _write_reproducible_zip_entry(archive, name, data)
        _write_reproducible_zip_entry(
            archive,
            _MANIFEST_NAME,
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
        if mutate == "traversal":
            _write_reproducible_zip_entry(archive, "../escape.txt", "invalid")
    return destination


def build_official_module_archive(destination: Path) -> Path:
    """Build the one official Test Center package from the release policy."""
    return build_module_archive(
        destination,
        version=OFFICIAL_TEST_CENTER_VERSION,
        core_version=OFFICIAL_TEST_CENTER_CORE_RANGE,
    )


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
        manifest: dict = {}
        if installed:
            try:
                manifest = json.loads((self.module_root / _MANIFEST_NAME).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                installed = False
        requirement = str(manifest.get("required_autody_version", "")) if installed else None
        compatible = bool(requirement and _supports_core(requirement, self.core_version))
        reason = None
        if installed and not compatible:
            reason = f"已安装模块要求 AutoDy {requirement or '未知版本'}，当前核心为 {self.core_version}。"
        return {
            "id": MODULE_ID,
            "display_name": "测试中心",
            "installed": installed,
            "version": entry.get("version") if installed else None,
            "module_api_version": (
                entry.get("module_api_version") or manifest.get("module_api_version")
                if installed
                else None
            ),
            "package_sha256": entry.get("package_sha256") if installed else None,
            "package_checksum": (
                entry.get("package_checksum") or manifest.get("package_checksum")
                if installed
                else None
            ),
            "compatible": compatible if installed else True,
            "required_autody_version": requirement,
            "compatibility_reason": reason,
            "update_available": False,
            "load_error": None if installed or entry is None else "模块加载失败",
        }

    def installed(self) -> bool:
        return bool(self.status()["installed"])

    def _validate_installed_module(self, root: Path) -> dict:
        if not root.is_dir() or root.is_symlink():
            raise ModulePackageError("模块安装目录无效")
        for relative in _ALLOWED_FILES:
            path = root / Path(relative)
            if not path.is_file() or path.is_symlink():
                raise ModulePackageError("模块安装文件结构不完整")
        allowed_top_level = {PurePosixPath(name).parts[0] for name in _ALLOWED_FILES} | {"data"}
        if any(path.name not in allowed_top_level or path.is_symlink() for path in root.iterdir()):
            raise ModulePackageError("模块安装目录包含未知文件")
        frontend = root / "frontend"
        allowed_frontend = {
            PurePosixPath(name).name
            for name in _ALLOWED_FILES
            if PurePosixPath(name).parts[0] == "frontend"
        }
        if {path.name for path in frontend.iterdir()} != allowed_frontend:
            raise ModulePackageError("模块前端文件结构不完整")
        files = {
            relative: (root / Path(relative)).read_bytes()
            for relative in _ALLOWED_FILES
        }
        return _read_manifest(files, self.core_version)

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
        package_sha256 = sha256(archive_path.read_bytes()).hexdigest()
        data_moved = False
        root_backed_up = False
        replacement_activated = False
        succeeded = False
        try:
            replacement.mkdir()
            for name, content in files.items():
                target = replacement / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            (replacement / "data").mkdir()
            if backup.exists():
                raise ModulePackageError("检测到未完成的模块升级备份")
            root = self._safe_module_root()
            self._validate_installed_module(replacement)
            if root.exists():
                existing_data = root / "data"
                if not existing_data.is_dir() or existing_data.is_symlink():
                    raise ModulePackageError("模块运行数据目录无效")
                (replacement / "data").rmdir()
                os.replace(existing_data, replacement / "data")
                data_moved = True
                os.replace(root, backup)
                root_backed_up = True
            os.replace(replacement, root)
            replacement_activated = True
            activated_manifest = self._validate_installed_module(root)
            registry = json.loads(json.dumps(previous_registry))
            registry["modules"][MODULE_ID] = {
                "version": activated_manifest["module_version"],
                "module_api_version": activated_manifest["module_api_version"],
                "package_sha256": package_sha256,
                "package_checksum": activated_manifest["package_checksum"],
            }
            self._write_registry(registry)
            succeeded = True
        except Exception:
            root = self.module_root
            if replacement_activated and root.exists():
                if root_backed_up and data_moved and (root / "data").exists():
                    os.replace(root / "data", backup / "data")
                shutil.rmtree(root)
            elif data_moved and (replacement / "data").exists():
                destination = backup / "data" if root_backed_up else root / "data"
                os.replace(replacement / "data", destination)
            if root_backed_up and backup.exists():
                os.replace(backup, root)
            self._write_registry(previous_registry)
            raise
        finally:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)
            if succeeded and backup.exists():
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
