from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import os
from pathlib import Path
import shutil
from typing import Callable

from autody.message_pack_catalog import (
    CatalogDocument,
    FusedSourceItem,
    MessageItem,
    MessagePackCatalogStore,
    MessagePackError,
)


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


@dataclass(frozen=True)
class PackCatalog:
    packs: list[MessagePack]


@dataclass(frozen=True)
class PackPreview:
    pack: MessagePack
    messages: list[str]
    duplicate_count: int


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
        catalog = self.catalog()
        return PackCatalog(
            packs=[
                self._pack_payload(catalog, pack_id)
                for pack_id in catalog.top_level_pack_ids
            ]
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
        return MessagePack(
            id=package.id,
            name=package.name,
            description=package.description,
            version=package.version,
            file="",
            count=len(self._message_ids(catalog, pack_id)),
            category=package.category,
        )

    def preview(self, pack_id: str) -> PackPreview:
        catalog = self.catalog()
        if pack_id not in catalog.packages:
            raise MessagePackError(f"未知文案包：{pack_id}")
        package = catalog.packages[pack_id]
        pack = self._pack_payload(catalog, pack_id)
        messages = [catalog.messages[item].text for item in self._message_ids(catalog, pack_id)]
        if not messages:
            raise MessagePackError(f"文案包为空：{pack_id}")
        return PackPreview(
            pack=pack,
            messages=messages,
            duplicate_count=package.seed_duplicate_count,
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
