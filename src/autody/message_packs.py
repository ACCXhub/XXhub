from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import os
from pathlib import Path
import shutil
from typing import Callable, TypeVar

from autody.config import load_config, serialize_config
from autody.message_pack_catalog import (
    CatalogDocument,
    FusedSourceItem,
    MessageItem,
    MessagePackConflict,
    MessagePackCatalogStore,
    MessagePackError,
    MessageRecord,
    PackageRecord,
)
from autody.locking import SingleInstanceLock, TaskAlreadyRunning


class ImportMode(str, Enum):
    MERGE = "merge"
    REPLACE = "replace"
    PREVIEW_ONLY = "preview_only"


@dataclass(frozen=True)
class MessagePack:
    id: str
    name: str
    description: str
    version: str
    file: str
    count: int
    category: str
    direct_fused_sources: list[MessagePack] = field(default_factory=list)
    fused_source_count: int = 0


@dataclass(frozen=True)
class PackCatalog:
    packs: list[MessagePack]
    revision: int = 0


@dataclass(frozen=True)
class PackEntry:
    id: str
    text: str
    origin_pack_id: str
    origin_pack_name: str
    native: bool


@dataclass(frozen=True)
class PackPreview:
    pack: MessagePack
    messages: list[str]
    duplicate_count: int
    entries: list[PackEntry] = field(default_factory=list)


@dataclass(frozen=True)
class PackMutationResult:
    revision: int
    pack: MessagePack | None
    catalog: PackCatalog


@dataclass(frozen=True)
class MessageMutationResult:
    revision: int
    pack: MessagePack
    entry: PackEntry
    catalog: PackCatalog


@dataclass(frozen=True)
class ImportResult:
    added_count: int
    duplicate_count: int
    total_count: int
    backup_path: Path | None
    mode: ImportMode


class MessagePackService:
    def __init__(
        self,
        root: Path,
        data_root: Path | None = None,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ):
        self.root = root.resolve()
        self.program_root = self.root
        self.data_root = (data_root or root).resolve()
        self.now = now or datetime.now
        self.store = MessagePackCatalogStore(
            self.program_root,
            self.data_root,
            now=self.now,
            id_factory=id_factory,
        )

    def catalog(self) -> CatalogDocument:
        return self.store.load_or_seed()

    def list_packs(self) -> PackCatalog:
        return self._catalog_payload(self.catalog())

    def _catalog_payload(self, catalog: CatalogDocument) -> PackCatalog:
        return PackCatalog(
            packs=[
                self._pack_payload(catalog, pack_id)
                for pack_id in catalog.top_level_pack_ids
            ],
            revision=catalog.revision,
        )

    def _message_ids(self, catalog: CatalogDocument, pack_id: str) -> list[str]:
        result: list[str] = []
        for item in catalog.packages[pack_id].items:
            if isinstance(item, MessageItem):
                result.append(item.message_id)
            elif isinstance(item, FusedSourceItem):
                result.extend(self._message_ids(catalog, item.pack_id))
        return result

    def _pack_payload(self, catalog: CatalogDocument, pack_id: str) -> MessagePack:
        package = catalog.packages[pack_id]
        direct_source_ids = [
            item.pack_id
            for item in package.items
            if isinstance(item, FusedSourceItem)
        ]
        return MessagePack(
            id=package.id,
            name=package.name,
            description=package.description,
            version=package.version,
            file="",
            count=len(self._message_ids(catalog, pack_id)),
            category=package.category,
            direct_fused_sources=[
                self._pack_payload(catalog, source_id)
                for source_id in direct_source_ids
            ],
            fused_source_count=len(self._descendant_pack_ids(catalog, pack_id)) - 1,
        )

    def _descendant_pack_ids(
        self,
        catalog: CatalogDocument,
        pack_id: str,
    ) -> list[str]:
        result = [pack_id]
        for item in catalog.packages[pack_id].items:
            if isinstance(item, FusedSourceItem):
                result.extend(self._descendant_pack_ids(catalog, item.pack_id))
        return result

    def direct_fused_sources(self, pack_id: str) -> list[MessagePack]:
        catalog = self.catalog()
        if pack_id not in catalog.packages:
            raise MessagePackError(f"未知文案包：{pack_id}")
        return [
            self._pack_payload(catalog, item.pack_id)
            for item in catalog.packages[pack_id].items
            if isinstance(item, FusedSourceItem)
        ]

    def _entries(
        self,
        catalog: CatalogDocument,
        pack_id: str,
        *,
        root_pack_id: str,
    ) -> list[PackEntry]:
        result: list[PackEntry] = []
        package = catalog.packages[pack_id]
        for item in package.items:
            if isinstance(item, MessageItem):
                message = catalog.messages[item.message_id]
                result.append(
                    PackEntry(
                        id=message.id,
                        text=message.text,
                        origin_pack_id=package.id,
                        origin_pack_name=package.name,
                        native=package.id == root_pack_id,
                    )
                )
            elif isinstance(item, FusedSourceItem):
                result.extend(
                    self._entries(
                        catalog,
                        item.pack_id,
                        root_pack_id=root_pack_id,
                    )
                )
        return result

    def _preview_payload(self, catalog: CatalogDocument, pack_id: str) -> PackPreview:
        if pack_id not in catalog.packages:
            raise MessagePackError(f"未知文案包：{pack_id}")
        package = catalog.packages[pack_id]
        entries = self._entries(catalog, pack_id, root_pack_id=pack_id)
        return PackPreview(
            pack=self._pack_payload(catalog, pack_id),
            messages=[entry.text for entry in entries],
            duplicate_count=package.seed_duplicate_count,
            entries=entries,
        )

    def preview(self, pack_id: str) -> PackPreview:
        return self._preview_payload(self.catalog(), pack_id)

    def _next_id(self, catalog: CatalogDocument) -> str:
        candidate = self.store.id_factory()
        if (
            not candidate
            or candidate in catalog.packages
            or candidate in catalog.messages
        ):
            raise MessagePackError("无法生成唯一文案包或消息 ID")
        return candidate

    @staticmethod
    def _normalized_name(name: str) -> str:
        value = name.strip()
        if not value:
            raise MessagePackError("文案包名称不能为空")
        if len(value) > 80:
            raise MessagePackError("文案包名称不能超过 80 个字符")
        return value

    @staticmethod
    def _validated_message(text: str) -> str:
        if not text.strip():
            raise MessagePackError("文案不能为空")
        if len(text) > 500:
            raise MessagePackError("单条文案不能超过 500 个字符")
        return text

    T = TypeVar("T")

    def _mutate(
        self,
        expected_revision: int,
        change: Callable[[CatalogDocument], T],
    ) -> tuple[T, CatalogDocument]:
        try:
            with SingleInstanceLock(self.store.lock_path, timeout_seconds=5):
                current = self.store.load_locked()
                if current.revision != expected_revision:
                    raise MessagePackConflict(
                        "文案包已被其他页面修改，请刷新后重试"
                    )
                candidate = current.model_copy(deep=True)
                result = change(candidate)
                candidate.revision += 1
                candidate = CatalogDocument.model_validate(
                    candidate.model_dump(mode="json")
                )
                self.store.write_locked(candidate)
                return result, candidate
        except TaskAlreadyRunning as exc:
            raise MessagePackConflict(
                "文案包正在由另一个进程修改，请稍后重试"
            ) from exc

    def create_pack(
        self,
        expected_revision: int,
        name: str = "新建文案包",
    ) -> PackMutationResult:
        normalized = self._normalized_name(name)

        def change(catalog: CatalogDocument) -> str:
            pack_id = self._next_id(catalog)
            catalog.packages[pack_id] = PackageRecord(
                id=pack_id,
                name=normalized,
                created_at=self.now(),
                version="user",
                category="custom",
            )
            catalog.top_level_pack_ids.append(pack_id)
            return pack_id

        pack_id, catalog = self._mutate(expected_revision, change)
        return PackMutationResult(
            revision=catalog.revision,
            pack=self._pack_payload(catalog, pack_id),
            catalog=self._catalog_payload(catalog),
        )

    def rename_pack(
        self,
        pack_id: str,
        name: str,
        expected_revision: int,
    ) -> PackMutationResult:
        normalized = self._normalized_name(name)

        def change(catalog: CatalogDocument) -> str:
            if pack_id not in catalog.packages:
                raise MessagePackError(f"未知文案包：{pack_id}")
            catalog.packages[pack_id].name = normalized
            return pack_id

        changed_id, catalog = self._mutate(expected_revision, change)
        return PackMutationResult(
            revision=catalog.revision,
            pack=self._pack_payload(catalog, changed_id),
            catalog=self._catalog_payload(catalog),
        )

    def import_text(
        self,
        raw: bytes,
        filename: str,
        expected_revision: int,
    ) -> PackMutationResult:
        if Path(filename).suffix.lower() != ".txt":
            raise MessagePackError("文案包导入仅支持 TXT")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MessagePackError("TXT 必须使用 UTF-8 编码") from exc
        rows = [line for line in text.splitlines() if line.strip()]
        if not rows:
            raise MessagePackError("TXT 中没有有效文案")
        for index, row in enumerate(rows, 1):
            if len(row) > 500:
                raise MessagePackError(f"第 {index} 条文案超过 500 个字符")
        display_name = rows[0].strip()
        if len(display_name) > 80:
            display_name = display_name[:79] + "…"

        def change(catalog: CatalogDocument) -> str:
            pack_id = self._next_id(catalog)
            items: list[MessageItem] = []
            for row in rows:
                message_id = self._next_id(catalog)
                catalog.messages[message_id] = MessageRecord(
                    id=message_id,
                    text=row,
                    created_at=self.now(),
                    updated_at=self.now(),
                )
                items.append(MessageItem(message_id=message_id))
            catalog.packages[pack_id] = PackageRecord(
                id=pack_id,
                name=display_name,
                created_at=self.now(),
                items=items,
                version="user",
                category="custom",
            )
            catalog.top_level_pack_ids.append(pack_id)
            return pack_id

        pack_id, catalog = self._mutate(expected_revision, change)
        return PackMutationResult(
            revision=catalog.revision,
            pack=self._pack_payload(catalog, pack_id),
            catalog=self._catalog_payload(catalog),
        )

    def add_message(
        self,
        pack_id: str,
        text: str,
        expected_revision: int,
    ) -> MessageMutationResult:
        value = self._validated_message(text)

        def change(catalog: CatalogDocument) -> str:
            if pack_id not in catalog.top_level_pack_ids:
                raise MessagePackError("只能向顶层文案包新增文案")
            message_id = self._next_id(catalog)
            timestamp = self.now()
            catalog.messages[message_id] = MessageRecord(
                id=message_id,
                text=value,
                created_at=timestamp,
                updated_at=timestamp,
            )
            catalog.packages[pack_id].items.append(
                MessageItem(message_id=message_id)
            )
            return message_id

        message_id, catalog = self._mutate(expected_revision, change)
        entry = next(
            item
            for item in self._entries(catalog, pack_id, root_pack_id=pack_id)
            if item.id == message_id
        )
        return MessageMutationResult(
            revision=catalog.revision,
            pack=self._pack_payload(catalog, pack_id),
            entry=entry,
            catalog=self._catalog_payload(catalog),
        )

    def _owning_package_id(
        self,
        catalog: CatalogDocument,
        root_pack_id: str,
        message_id: str,
    ) -> str | None:
        for entry in self._entries(
            catalog,
            root_pack_id,
            root_pack_id=root_pack_id,
        ):
            if entry.id == message_id:
                return entry.origin_pack_id
        return None

    def update_message(
        self,
        pack_id: str,
        message_id: str,
        text: str,
        expected_revision: int,
    ) -> MessageMutationResult:
        value = self._validated_message(text)

        def change(catalog: CatalogDocument) -> str:
            owner = self._owning_package_id(catalog, pack_id, message_id)
            if owner is None:
                raise MessagePackError("指定文案不属于该文案包")
            catalog.messages[message_id].text = value
            catalog.messages[message_id].updated_at = self.now()
            return owner

        _owner, catalog = self._mutate(expected_revision, change)
        entry = next(
            item
            for item in self._entries(catalog, pack_id, root_pack_id=pack_id)
            if item.id == message_id
        )
        return MessageMutationResult(
            revision=catalog.revision,
            pack=self._pack_payload(catalog, pack_id),
            entry=entry,
            catalog=self._catalog_payload(catalog),
        )

    def delete_message(
        self,
        pack_id: str,
        message_id: str,
        expected_revision: int,
    ) -> PackMutationResult:
        def change(catalog: CatalogDocument) -> None:
            owner = self._owning_package_id(catalog, pack_id, message_id)
            if owner is None:
                raise MessagePackError("指定文案不属于该文案包")
            package = catalog.packages[owner]
            package.items = [
                item
                for item in package.items
                if not (
                    isinstance(item, MessageItem)
                    and item.message_id == message_id
                )
            ]
            del catalog.messages[message_id]

        _unused, catalog = self._mutate(expected_revision, change)
        return PackMutationResult(
            revision=catalog.revision,
            pack=self._pack_payload(catalog, pack_id),
            catalog=self._catalog_payload(catalog),
        )

    def reorder_packs(
        self,
        pack_ids: list[str],
        expected_revision: int,
    ) -> PackMutationResult:
        def change(catalog: CatalogDocument) -> None:
            if (
                len(pack_ids) != len(set(pack_ids))
                or set(pack_ids) != set(catalog.top_level_pack_ids)
            ):
                raise MessagePackError("文案包排序必须包含全部顶层文案包")
            catalog.top_level_pack_ids = list(pack_ids)

        _unused, catalog = self._mutate(expected_revision, change)
        return PackMutationResult(
            revision=catalog.revision,
            pack=None,
            catalog=self._catalog_payload(catalog),
        )

    def fuse(
        self,
        source_id: str,
        destination_id: str,
        expected_revision: int,
        config_path: Path,
    ) -> PackMutationResult:
        try:
            with SingleInstanceLock(self.store.lock_path, timeout_seconds=5):
                current = self.store.load_locked()
                if current.revision != expected_revision:
                    raise MessagePackConflict(
                        "文案包已被其他页面修改，请刷新后重试"
                    )
                if source_id == destination_id:
                    raise MessagePackError("不能将文案包融合到自身")
                if source_id not in current.top_level_pack_ids:
                    raise MessagePackError("来源文案包不是顶层文案包")
                if destination_id not in current.top_level_pack_ids:
                    raise MessagePackError("目标文案包不是顶层文案包")

                candidate = current.model_copy(deep=True)
                restore_index = candidate.top_level_pack_ids.index(source_id)
                candidate.top_level_pack_ids.remove(source_id)
                candidate.packages[destination_id].items.append(
                    FusedSourceItem(
                        pack_id=source_id,
                        fused_at=self.now(),
                        restore_index=restore_index,
                    )
                )
                candidate.revision += 1
                candidate = CatalogDocument.model_validate(
                    candidate.model_dump(mode="json")
                )

                resolved_config_path = config_path.resolve()
                config = load_config(resolved_config_path)
                for target in config.targets:
                    if target.message_pack == source_id:
                        target.message_pack = destination_id
                self.store.commit_external_transaction(
                    {
                        self.store.catalog_path: self.store.serialize_catalog(candidate),
                        resolved_config_path: serialize_config(
                            config,
                            resolved_config_path.parent,
                        ),
                    }
                )
                return PackMutationResult(
                    revision=candidate.revision,
                    pack=self._pack_payload(candidate, destination_id),
                    catalog=self._catalog_payload(candidate),
                )
        except TaskAlreadyRunning as exc:
            raise MessagePackConflict(
                "文案包正在由另一个进程修改，请稍后重试"
            ) from exc

    def split(
        self,
        destination_id: str,
        source_id: str,
        expected_revision: int,
    ) -> PackMutationResult:
        def change(catalog: CatalogDocument) -> str:
            if destination_id not in catalog.top_level_pack_ids:
                raise MessagePackError("只能从顶层文案包拆出来源")
            source_item = next(
                (
                    item
                    for item in catalog.packages[destination_id].items
                    if isinstance(item, FusedSourceItem)
                    and item.pack_id == source_id
                ),
                None,
            )
            if source_item is None:
                raise MessagePackError("指定文案包不是当前包的直接融合来源")
            catalog.packages[destination_id].items.remove(source_item)
            restore_index = min(
                source_item.restore_index,
                len(catalog.top_level_pack_ids),
            )
            catalog.top_level_pack_ids.insert(restore_index, source_id)
            return destination_id

        changed_id, catalog = self._mutate(expected_revision, change)
        return PackMutationResult(
            revision=catalog.revision,
            pack=self._pack_payload(catalog, changed_id),
            catalog=self._catalog_payload(catalog),
        )

    def delete_pack(
        self,
        pack_id: str,
        expected_revision: int,
        referenced_pack_ids: set[str],
    ) -> PackMutationResult:
        def change(catalog: CatalogDocument) -> None:
            if pack_id not in catalog.top_level_pack_ids:
                raise MessagePackError("只能删除顶层文案包")
            subtree_ids = set(self._descendant_pack_ids(catalog, pack_id))
            if subtree_ids & referenced_pack_ids:
                raise MessagePackConflict("该文案包仍被目标使用，无法删除")
            message_ids = {
                item.message_id
                for subtree_id in subtree_ids
                for item in catalog.packages[subtree_id].items
                if isinstance(item, MessageItem)
            }
            catalog.top_level_pack_ids.remove(pack_id)
            for message_id in message_ids:
                del catalog.messages[message_id]
            for subtree_id in subtree_ids:
                del catalog.packages[subtree_id]

        _unused, catalog = self._mutate(expected_revision, change)
        return PackMutationResult(
            revision=catalog.revision,
            pack=None,
            catalog=self._catalog_payload(catalog),
        )

    def _backup(self, messages_file: Path) -> Path | None:
        if not messages_file.exists():
            return None
        backup_dir = self.root / "data" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"messages-{self.now():%Y%m%d-%H%M%S}.txt"
        shutil.copy2(messages_file, backup)
        return backup

    @staticmethod
    def _read_existing(messages_file: Path) -> list[str]:
        if not messages_file.exists():
            return []
        lines = [
            line.strip()
            for line in messages_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return list(dict.fromkeys(lines))

    @staticmethod
    def _write(messages_file: Path, messages: list[str]) -> None:
        messages_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = messages_file.with_suffix(messages_file.suffix + ".tmp")
        temporary.write_text("\n".join(messages) + "\n", encoding="utf-8")
        os.replace(temporary, messages_file)

    def import_pack(
        self,
        pack_id: str,
        messages_file: Path,
        mode: ImportMode,
    ) -> ImportResult:
        preview = self.preview(pack_id)
        existing = self._read_existing(messages_file)
        if mode is ImportMode.PREVIEW_ONLY:
            return ImportResult(
                added_count=0,
                duplicate_count=preview.duplicate_count,
                total_count=len(existing),
                backup_path=None,
                mode=mode,
            )
        backup = self._backup(messages_file)
        if mode is ImportMode.MERGE:
            existing_set = set(existing)
            additions = [item for item in preview.messages if item not in existing_set]
            final = existing + additions
            duplicate_count = preview.duplicate_count + len(preview.messages) - len(additions)
            added_count = len(additions)
        elif mode is ImportMode.REPLACE:
            final = preview.messages
            duplicate_count = preview.duplicate_count
            added_count = len(final)
        else:
            raise MessagePackError(f"不支持的导入模式：{mode}")
        self._write(messages_file, final)
        return ImportResult(
            added_count=added_count,
            duplicate_count=duplicate_count,
            total_count=len(final),
            backup_path=backup,
            mode=mode,
        )
