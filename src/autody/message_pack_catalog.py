from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Annotated, Callable, Literal
import uuid

from pydantic import BaseModel, Field, ValidationError, model_validator

from autody.locking import SingleInstanceLock, TaskAlreadyRunning


class MessagePackError(RuntimeError):
    pass


class MessagePackConflict(MessagePackError):
    pass


class CatalogMigrationState(BaseModel):
    completed: bool
    completed_at: datetime


class CatalogMigrations(BaseModel):
    builtin_seed_v1: CatalogMigrationState


class MessageItem(BaseModel):
    kind: Literal["message"] = "message"
    message_id: str = Field(min_length=1)


class FusedSourceItem(BaseModel):
    kind: Literal["fused_source"] = "fused_source"
    pack_id: str = Field(min_length=1)
    fused_at: datetime
    restore_index: int = Field(ge=0)


PackItem = Annotated[MessageItem | FusedSourceItem, Field(discriminator="kind")]


class PackageRecord(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    created_at: datetime
    items: list[PackItem] = Field(default_factory=list)
    version: str = "1.0.0"
    category: str = "custom"
    seed_duplicate_count: int = Field(default=0, ge=0)


class MessageRecord(BaseModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=500)
    created_at: datetime
    updated_at: datetime


class CatalogDocument(BaseModel):
    schema_version: Literal[1] = 1
    revision: int = Field(default=1, ge=1)
    migrations: CatalogMigrations
    top_level_pack_ids: list[str]
    packages: dict[str, PackageRecord]
    messages: dict[str, MessageRecord]

    @model_validator(mode="after")
    def validate_membership(self):
        if len(self.top_level_pack_ids) != len(set(self.top_level_pack_ids)):
            raise ValueError("top-level message pack IDs must be unique")
        top_level = set(self.top_level_pack_ids)
        if not top_level.issubset(self.packages):
            raise ValueError("top-level message pack does not exist")

        parents: dict[str, str] = {}
        referenced_messages: set[str] = set()
        for key, package in self.packages.items():
            if key != package.id:
                raise ValueError("message pack key does not match its ID")
            for item in package.items:
                if isinstance(item, MessageItem):
                    if item.message_id not in self.messages:
                        raise ValueError("message pack contains an unknown message")
                    if item.message_id in referenced_messages:
                        raise ValueError("message belongs to more than one package")
                    referenced_messages.add(item.message_id)
                    continue
                if item.pack_id not in self.packages:
                    raise ValueError("fused source package does not exist")
                if item.pack_id in parents:
                    raise ValueError("fused source package has more than one parent")
                parents[item.pack_id] = package.id

        if top_level & set(parents):
            raise ValueError("top-level package cannot also be a fused source")
        if set(self.packages) != top_level | set(parents):
            raise ValueError("message pack is orphaned")
        if set(self.messages) != referenced_messages:
            raise ValueError("message record is orphaned")
        for key, message in self.messages.items():
            if key != message.id:
                raise ValueError("message key does not match its ID")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(pack_id: str) -> None:
            if pack_id in visiting:
                raise ValueError("message pack fusion contains a cycle")
            if pack_id in visited:
                return
            visiting.add(pack_id)
            for item in self.packages[pack_id].items:
                if isinstance(item, FusedSourceItem):
                    visit(item.pack_id)
            visiting.remove(pack_id)
            visited.add(pack_id)

        for pack_id in self.packages:
            visit(pack_id)
        return self


class TransactionVersion(BaseModel):
    data_base64: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TransactionTarget(BaseModel):
    path: str = Field(min_length=1)
    old: TransactionVersion
    new: TransactionVersion


class PendingTransaction(BaseModel):
    schema_version: Literal[1] = 1
    transaction_id: str = Field(min_length=1)
    state: Literal["pending"] = "pending"
    targets: list[TransactionTarget] = Field(min_length=1)


class MessagePackCatalogStore:
    def __init__(
        self,
        program_root: Path,
        data_root: Path,
        *,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ):
        self.program_root = program_root.resolve()
        self.data_root = data_root.resolve()
        self.now = now or datetime.now
        self.id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self.catalog_path = self.data_root / "data" / "message-packs" / "catalog.json"
        self.pending_path = (
            self.data_root / "data" / "message-packs" / "pending-transaction.json"
        )
        self.lock_path = self.data_root / "data" / "locks" / "message-packs.lock"

    def load_or_seed(self) -> CatalogDocument:
        try:
            with SingleInstanceLock(self.lock_path, timeout_seconds=5):
                return self.load_locked()
        except TaskAlreadyRunning as exc:
            raise MessagePackConflict("文案包正在由另一个进程修改，请稍后重试") from exc

    def load_read_only(self) -> CatalogDocument:
        """Read the effective catalog without locks, recovery, or seed writes."""
        if self.pending_path.exists():
            raise MessagePackConflict("文案包存在待恢复事务，请先打开文案包页面")
        if self.catalog_path.exists():
            return self._read_validated()
        return self._seed_from_builtin_index()

    def load_locked(self) -> CatalogDocument:
        self.recover_pending_transaction()
        if self.catalog_path.exists():
            return self._read_validated()
        catalog = self._seed_from_builtin_index()
        self._atomic_write(catalog)
        return catalog

    def write_locked(self, catalog: CatalogDocument) -> None:
        self._atomic_write(catalog)

    @staticmethod
    def serialize_catalog(catalog: CatalogDocument) -> bytes:
        return (
            json.dumps(
                catalog.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")

    def _read_validated(self) -> CatalogDocument:
        try:
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            return CatalogDocument.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise MessagePackError(f"文案包目录无效：{exc}") from exc

    def _seed_from_builtin_index(self) -> CatalogDocument:
        index_path = self.program_root / "message-packs" / "index.json"
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            rows = payload["packs"]
            if not isinstance(rows, list):
                raise TypeError("packs must be a list")
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise MessagePackError(f"内置文案包索引无效：{exc}") from exc

        created_at = self.now()
        top_level_pack_ids: list[str] = []
        packages: dict[str, PackageRecord] = {}
        messages: dict[str, MessageRecord] = {}
        for row in rows:
            try:
                pack_id = str(row["id"])
                file_name = str(row["file"])
                name = str(row["name"])
            except (KeyError, TypeError) as exc:
                raise MessagePackError(f"内置文案包索引无效：{exc}") from exc
            if not pack_id or pack_id in packages:
                raise MessagePackError("内置文案包索引包含重复或空 id")
            pack_dir = (self.program_root / "message-packs").resolve()
            candidate = (pack_dir / file_name).resolve()
            if pack_dir not in candidate.parents or not candidate.is_file():
                raise MessagePackError(f"内置文案包文件不存在：{file_name}")
            lines = [
                line.strip()
                for line in candidate.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            unique_lines = list(dict.fromkeys(lines))
            if not unique_lines:
                raise MessagePackError(f"文案包为空：{pack_id}")
            items: list[PackItem] = []
            for text in unique_lines:
                message_id = self.id_factory()
                if not message_id or message_id in messages:
                    raise MessagePackError("无法生成唯一消息 ID")
                messages[message_id] = MessageRecord(
                    id=message_id,
                    text=text,
                    created_at=created_at,
                    updated_at=created_at,
                )
                items.append(MessageItem(message_id=message_id))
            packages[pack_id] = PackageRecord(
                id=pack_id,
                name=name,
                description=str(row.get("description", "")),
                version=str(row.get("version", "1.0.0")),
                category=str(row.get("category", "custom")),
                seed_duplicate_count=len(lines) - len(unique_lines),
                created_at=created_at,
                items=items,
            )
            top_level_pack_ids.append(pack_id)

        return CatalogDocument(
            migrations=CatalogMigrations(
                builtin_seed_v1=CatalogMigrationState(
                    completed=True,
                    completed_at=created_at,
                )
            ),
            top_level_pack_ids=top_level_pack_ids,
            packages=packages,
            messages=messages,
        )

    def _atomic_write(self, catalog: CatalogDocument) -> None:
        self._replace_target(self.catalog_path, self.serialize_catalog(catalog))

    @staticmethod
    def _version(payload: bytes) -> TransactionVersion:
        return TransactionVersion(
            data_base64=base64.b64encode(payload).decode("ascii"),
            sha256=hashlib.sha256(payload).hexdigest(),
        )

    @staticmethod
    def _decode_version(version: TransactionVersion) -> bytes:
        try:
            payload = base64.b64decode(version.data_base64, validate=True)
        except (ValueError, TypeError) as exc:
            raise MessagePackError("文案包事务日志包含无效数据") from exc
        if hashlib.sha256(payload).hexdigest() != version.sha256:
            raise MessagePackError("文案包事务日志校验失败")
        return payload

    @staticmethod
    def _atomic_replace_bytes(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _replace_target(self, path: Path, payload: bytes) -> None:
        self._atomic_replace_bytes(path, payload)

    def _validated_transaction(self) -> PendingTransaction:
        try:
            transaction = PendingTransaction.model_validate_json(
                self.pending_path.read_bytes()
            )
        except (OSError, ValidationError) as exc:
            raise MessagePackError("文案包事务日志无效") from exc

        resolved_paths: list[Path] = []
        for target in transaction.targets:
            path = Path(target.path)
            if not path.is_absolute():
                raise MessagePackError("文案包事务日志包含非绝对路径")
            resolved = path.resolve()
            if resolved != self.data_root and self.data_root not in resolved.parents:
                raise MessagePackError("文案包事务日志目标超出数据目录")
            resolved_paths.append(resolved)
            self._decode_version(target.old)
            self._decode_version(target.new)
        if len(resolved_paths) != len(set(resolved_paths)):
            raise MessagePackError("文案包事务日志包含重复目标")
        if self.catalog_path not in resolved_paths:
            raise MessagePackError("文案包事务日志缺少目录目标")
        return transaction

    def _write_pending(self, transaction: PendingTransaction) -> None:
        payload = (
            json.dumps(
                transaction.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
        self._atomic_replace_bytes(self.pending_path, payload)

    def commit_external_transaction(self, changes: dict[Path, bytes]) -> None:
        normalized: dict[Path, bytes] = {}
        for raw_path, payload in changes.items():
            path = raw_path.resolve()
            if path != self.data_root and self.data_root not in path.parents:
                raise MessagePackError("文案包事务目标必须位于数据目录")
            if path in normalized:
                raise MessagePackError("文案包事务包含重复目标")
            if not path.is_file():
                raise MessagePackError("文案包事务目标不存在")
            normalized[path] = payload
        if self.catalog_path not in normalized:
            raise MessagePackError("文案包事务缺少目录变更")

        transaction = PendingTransaction(
            transaction_id=uuid.uuid4().hex,
            targets=[
                TransactionTarget(
                    path=str(path),
                    old=self._version(path.read_bytes()),
                    new=self._version(payload),
                )
                for path, payload in normalized.items()
            ],
        )
        self._write_pending(transaction)
        try:
            for path, payload in normalized.items():
                self._replace_target(path, payload)
        except Exception as exc:
            try:
                for target in transaction.targets:
                    path = Path(target.path)
                    old_payload = self._decode_version(target.old)
                    self._replace_target(path, old_payload)
                    if hashlib.sha256(path.read_bytes()).hexdigest() != target.old.sha256:
                        raise OSError("rollback verification failed")
                self.pending_path.unlink(missing_ok=True)
            except Exception as rollback_exc:
                raise MessagePackError(
                    "文案包事务写入失败，等待下次访问恢复"
                ) from rollback_exc
            raise MessagePackError("文案包事务写入失败，已回滚") from exc
        self.pending_path.unlink(missing_ok=True)

    def recover_pending_transaction(self) -> None:
        if not self.pending_path.exists():
            return
        transaction = self._validated_transaction()
        for target in transaction.targets:
            path = Path(target.path)
            payload = self._decode_version(target.new)
            current_hash = (
                hashlib.sha256(path.read_bytes()).hexdigest()
                if path.is_file()
                else None
            )
            if current_hash != target.new.sha256:
                self._replace_target(path, payload)
            if hashlib.sha256(path.read_bytes()).hexdigest() != target.new.sha256:
                raise MessagePackError("文案包事务恢复校验失败")
        self.pending_path.unlink(missing_ok=True)
