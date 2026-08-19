from __future__ import annotations

from datetime import datetime
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
                self.recover_pending_transaction()
                if self.catalog_path.exists():
                    return self._read_validated()
                catalog = self._seed_from_builtin_index()
                self._atomic_write(catalog)
                return catalog
        except TaskAlreadyRunning as exc:
            raise MessagePackConflict("文案包正在由另一个进程修改，请稍后重试") from exc

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
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.catalog_path.with_name(
            f"{self.catalog_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    catalog.model_dump(mode="json"),
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.catalog_path)
        finally:
            temporary.unlink(missing_ok=True)

    def recover_pending_transaction(self) -> None:
        if self.pending_path.exists():
            raise MessagePackError("检测到未完成的文案包事务，当前版本无法恢复")
